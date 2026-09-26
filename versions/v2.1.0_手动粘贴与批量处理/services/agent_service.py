# -*- coding: utf-8 -*-
"""
Agent 编排服务。

- 主路径：使用 OpenAI 兼容接口的 tools 参数完成 Function Calling
- 降级路径：模型明确不支持 tools 时，用提示词解析单个工具调用
- Agent 不提供删除工具，避免绕过页面确认
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from services import (
    ai_service,
    note_service,
    rag_service,
    rss_service,
    vector_service,
)
from models.models import RssSource

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 4
MAX_MESSAGE_LENGTH = 2000
MAX_HISTORY_ITEMS = 20

SYSTEM_PROMPT = """你是知识消化平台的智能助手，可以帮用户管理笔记、RSS 订阅、待处理内容和知识问答。
规则：
1. 需要实时数据、系统状态，或会改变系统状态时，必须调用工具，不能编造。
2. 查询类问题直接给简洁结果；操作完成后明确说明成功或失败。
3. 待处理内容必须先预览；只有用户明确说“保存”后才能保存。
4. 不执行删除操作。用户要求删除时，提醒用户到对应页面手动确认删除。
5. 回答使用中文，简洁自然，不要长篇大论。"""

FALLBACK_PLAN_PROMPT = """你运行在不支持 Function Calling 的模型上。
请判断用户是否需要调用工具。只能输出 JSON，不要 Markdown，不要解释。
可用工具：search_notes、create_note、add_rss_source、fetch_rss、get_stats、get_pending、process_pending、save_pending_item、answer_question。
格式：
- 不需要工具：{"calls":[]}
- 需要一个工具：{"calls":[{"name":"工具名","arguments":{...}}]}
一次只能调用一个工具；需要多个动作时返回 {"calls":[]}。"""

FALLBACK_STRICT_PROMPT = """上一次输出不是合法 JSON。这次只能输出一个 JSON 对象，格式必须是 {"calls":[]} 或 {"calls":[{"name":"工具名","arguments":{}}]}。不要输出其他任何内容。"""


class AgentError(Exception):
    """Agent 编排失败，信息可直接展示。"""


# ============ 工具定义 ============
def _string_property(description: str) -> dict[str, str]:
    return {"type": "string", "description": description}


def list_tools() -> list[dict[str, Any]]:
    """返回 OpenAI Function Calling 工具定义。"""
    return [
        {
            "type": "function",
            "function": {
                "name": "search_notes",
                "description": "搜索笔记库。用户要找笔记、资料、文章或主题内容时使用。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": _string_property("搜索关键词或要查找的主题"),
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "create_note",
                "description": "用户明确提供标题和内容，要求新建一篇笔记时使用。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "title": _string_property("笔记标题"),
                        "content": _string_property("笔记正文"),
                        "tags": {
                            "type": "array",
                            "description": "标签数组",
                            "items": {"type": "string"},
                        },
                    },
                    "required": ["title"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "add_rss_source",
                "description": "用户要求添加 RSS 订阅源时使用。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": _string_property("RSS 源名称"),
                        "url": _string_property("RSS 地址"),
                    },
                    "required": ["name", "url"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "fetch_rss",
                "description": "用户要求抓取 RSS，或抓取所有 RSS 时使用。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "source_id": _string_property("RSS 源 ID；抓取全部时传 all"),
                    },
                    "required": ["source_id"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_stats",
                "description": "用户询问笔记数量、今日新增、待处理数、RSS 源数量时使用。",
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_pending",
                "description": "用户询问待处理队列、待处理内容时使用。",
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "process_pending",
                "description": "用户要求处理某条待处理内容并生成预览时使用。不会自动保存。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "item_id": {"type": "integer", "description": "待处理内容 ID"},
                    },
                    "required": ["item_id"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "save_pending_item",
                "description": "用户确认保存待处理内容预览时使用。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "item_id": {"type": "integer", "description": "待处理内容 ID"},
                        "title": _string_property("预览标题"),
                        "content": _string_property("预览正文"),
                        "tags": {
                            "type": "array",
                            "description": "标签数组",
                            "items": {"type": "string"},
                        },
                    },
                    "required": ["item_id", "title", "content"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "answer_question",
                "description": "用户提出知识性问题，并希望只基于笔记库回答时使用。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "question": _string_property("用户的问题"),
                    },
                    "required": ["question"],
                    "additionalProperties": False,
                },
            },
        },
    ]


# ============ 模型调用 ============
def _client() -> Any:
    """创建 OpenAI 兼容客户端；配置缺失时抛 AiError。"""
    settings = ai_service.get_config()
    base_url = settings.get("base_url", "").strip()
    model = settings.get("model", "").strip()
    api_key = ai_service.get_api_key()
    if not base_url:
        raise ai_service.AiError("请先在设置页填写 Base URL 并保存")
    if not model:
        raise ai_service.AiError("请先在设置页填写模型名称并保存")
    if not api_key:
        raise ai_service.AiError("请先在设置页填写 API Key 并保存")
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise AgentError("当前环境缺少 openai 库") from exc
    return OpenAI(
        base_url=base_url,
        api_key=api_key,
        timeout=30,
        max_retries=1,
    ), model


def _create_completion(messages: list[dict[str, Any]], use_tools: bool) -> Any:
    """调用聊天模型。"""
    client, model = _client()
    kwargs: dict[str, Any] = {"model": model, "messages": messages}
    if use_tools:
        kwargs["tools"] = list_tools()
        kwargs["tool_choice"] = "auto"
    try:
        return client.chat.completions.create(**kwargs)
    except Exception as exc:  # noqa: BLE001 - SDK 异常类型不统一
        message = str(exc)
        logger.warning("Agent 模型调用失败：%s", message)
        # 统一封装成 AiError；错误文案保留，由主流程判断是否降级
        raise ai_service.AiError(f"AI 调用失败：{message}") from exc


def _unsupported_tools(message: str) -> bool:
    """判断错误是否明确表示不支持 Function Calling。"""
    lower = message.lower()
    return any(text in lower for text in ("tools", "tool_calls", "function calling", "function_calling"))


# ============ 工具执行 ============
def _safe_tags(value: Any) -> list[str]:
    """兼容数组、逗号字符串和空值。"""
    if isinstance(value, list):
        raw = value
    elif isinstance(value, str):
        raw = re.split(r"[,，]", value)
    else:
        raw = []
    result: list[str] = []
    for item in raw:
        text = str(item).strip()
        if text and text not in result:
            result.append(text)
    return result


def _compact_note(note: Any, score: float | None = None) -> dict[str, Any]:
    """输出精简笔记，避免大量正文进入模型上下文。"""
    data = note_service.serialize_note(note)
    data["content"] = data.get("content", "")[:500]
    if score is not None:
        data["score"] = score
    return data


def _tool_search_notes(session: Session, arguments: dict[str, Any]) -> dict[str, Any]:
    """语义搜索优先，失败或无结果时降级关键词搜索。"""
    query = str(arguments.get("query") or "").strip()
    if not query:
        raise AgentError("搜索内容不能为空")

    try:
        vector_hits = vector_service.search_notes(query, n_results=10)
        items = []
        for hit in vector_hits:
            try:
                note = note_service.get_note_by_id(session, hit["id"])
            except note_service.NoteNotFound:
                # 向量里残留的失效 ID 跳过；全部失效时降级关键词搜索
                continue
            items.append(_compact_note(note, hit["score"]))
        if items:
            return {"items": items, "mode": "semantic"}
    except vector_service.VectorError as exc:
        logger.info("语义搜索不可用，降级关键词：%s", exc)

    notes, total = note_service.list_notes(session, keyword=query, page=1, size=10)
    return {
        "items": [_compact_note(note) for note in notes],
        "total": total,
        "mode": "keyword",
    }


def _tool_create_note(session: Session, arguments: dict[str, Any]) -> dict[str, Any]:
    """创建笔记。"""
    title = str(arguments.get("title") or "").strip()
    if not title:
        raise AgentError("创建笔记需要标题")
    note = note_service.create_note(
        session=session,
        title=title,
        content=str(arguments.get("content") or ""),
        tags=_safe_tags(arguments.get("tags")),
    )
    return _compact_note(note)


def _tool_add_rss(session: Session, arguments: dict[str, Any]) -> dict[str, Any]:
    """添加 RSS 源。"""
    source = rss_service.add_source(
        session,
        str(arguments.get("name") or ""),
        str(arguments.get("url") or ""),
    )
    return rss_service.serialize_source(source)


def _tool_fetch_rss(session: Session, arguments: dict[str, Any]) -> dict[str, Any]:
    """抓取单源或全部 RSS。"""
    raw_source_id = str(arguments.get("source_id") or "").strip().lower()
    if not raw_source_id:
        raise AgentError("缺少 RSS 源 ID；抓取全部时传 all")
    if raw_source_id == "all":
        results = rss_service.fetch_all_sources(session, log_summary=False)
        return {"results": results, "total_added": sum(int(item.get("added", 0)) for item in results)}
    if not raw_source_id.isdigit():
        raise AgentError("RSS 源 ID 必须是数字，或传 all")
    added = rss_service.fetch_source(session, int(raw_source_id))
    return {"added": added}


def _tool_get_stats(session: Session, arguments: dict[str, Any]) -> dict[str, Any]:
    """读取综合统计。"""
    stats = note_service.get_stats(session)
    stats["rss_source_count"] = int(session.scalar(
        select(func.count()).select_from(RssSource)
    ) or 0)
    return stats


def _tool_get_pending(session: Session, arguments: dict[str, Any]) -> dict[str, Any]:
    """读取待处理和已跳过队列。"""
    items = rss_service.list_pending(session)
    return {"items": [rss_service.serialize_pending(item) for item in items]}


def _tool_process_pending(session: Session, arguments: dict[str, Any]) -> dict[str, Any]:
    """处理待处理内容，但只返回预览。"""
    item_id = arguments.get("item_id")
    if not isinstance(item_id, int):
        raise AgentError("item_id 必须是整数")
    return rss_service.process_pending_item(session, item_id)


def _tool_save_pending(session: Session, arguments: dict[str, Any]) -> dict[str, Any]:
    """用户确认后保存待处理预览。"""
    item_id = arguments.get("item_id")
    if not isinstance(item_id, int):
        raise AgentError("item_id 必须是整数")
    note = rss_service.save_processed_item(
        session,
        item_id,
        str(arguments.get("title") or ""),
        str(arguments.get("content") or ""),
        _safe_tags(arguments.get("tags")),
    )
    return _compact_note(note)


def _tool_answer_question(session: Session, arguments: dict[str, Any]) -> dict[str, Any]:
    """基于笔记库进行 RAG 问答。"""
    question = str(arguments.get("question") or "").strip()
    if not question:
        raise AgentError("问题不能为空")
    return rag_service.answer_question(question, session)


_TOOL_FUNCTIONS = {
    "search_notes": _tool_search_notes,
    "create_note": _tool_create_note,
    "add_rss_source": _tool_add_rss,
    "fetch_rss": _tool_fetch_rss,
    "get_stats": _tool_get_stats,
    "get_pending": _tool_get_pending,
    "process_pending": _tool_process_pending,
    "save_pending_item": _tool_save_pending,
    "answer_question": _tool_answer_question,
}


def execute_tool(session: Session, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """执行工具；异常转换成错误结果，不打断 Agent 主流程。"""
    function = _TOOL_FUNCTIONS.get(name)
    if function is None:
        return {"ok": False, "error": f"未知工具：{name}", "summary": "工具不存在"}
    try:
        data = function(session, arguments if isinstance(arguments, dict) else {})
        return {"ok": True, "data": data, "summary": summarize_result(name, data)}
    except Exception as exc:  # noqa: BLE001 - 所有工具错误都要返回给模型
        logger.warning("工具 %s 执行失败：%s", name, exc)
        return {"ok": False, "error": str(exc), "summary": summarize_error(name, str(exc))}


def summarize_result(name: str, data: Any) -> str:
    """生成给前端展示的简短中文结果。"""
    if name == "search_notes":
        count = len(data.get("items", [])) if isinstance(data, dict) else 0
        return f"找到 {count} 条笔记"
    if name == "create_note":
        return f"已创建笔记：{data.get('title', '无标题')}"
    if name == "add_rss_source":
        return f"已添加 RSS 源：{data.get('name', '')}"
    if name == "fetch_rss":
        if "results" in data:
            return f"全部抓取完成，新增 {data.get('total_added', 0)} 条"
        return f"抓取完成，新增 {data.get('added', 0)} 条"
    if name == "get_stats":
        return "已获取统计数据"
    if name == "get_pending":
        return f"待处理队列共 {len(data.get('items', []))} 条"
    if name == "process_pending":
        return "已生成预览，等待确认"
    if name == "save_pending_item":
        return "已保存预览并标记完成"
    if name == "answer_question":
        return "已基于笔记库回答"
    return "执行完成"


def summarize_error(name: str, error: str) -> str:
    """生成失败摘要。"""
    if name == "add_rss_source":
        return f"添加 RSS 源失败：{error}"
    if name == "fetch_rss":
        return f"RSS 抓取失败：{error}"
    if name == "process_pending":
        return f"处理失败：{error}"
    return f"{name} 失败：{error}"


# ============ 历史和主流程 ============
def _clean_history(history: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    """清理前端历史，只保留普通用户和助手消息。"""
    cleaned: list[dict[str, str]] = []
    for item in history or []:
        role = item.get("role")
        content = item.get("content")
        if role in ("user", "assistant") and isinstance(content, str) and content.strip():
            cleaned.append({"role": role, "content": content[:MAX_MESSAGE_LENGTH]})
    return cleaned[-MAX_HISTORY_ITEMS:]


def chat(user_message: str, history: list[dict[str, Any]] | None, session: Session) -> dict[str, Any]:
    """Agent 主入口：Function Calling、工具执行、自然语言总结。"""
    message = (user_message or "").strip()
    if not message:
        raise AgentError("消息不能为空")

    messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(_clean_history(history))
    messages.append({"role": "user", "content": message[:MAX_MESSAGE_LENGTH]})

    actions: list[dict[str, Any]] = []
    try:
        for _round in range(MAX_TOOL_ROUNDS):
            completion = _create_completion(messages, use_tools=True)
            response_message = completion.choices[0].message
            tool_calls = getattr(response_message, "tool_calls", None)

            if not tool_calls:
                answer = response_message.content
                if not answer:
                    raise AgentError("AI 返回内容为空")
                return {"answer": answer.strip(), "actions": actions}

            assistant_message: dict[str, Any] = {
                "role": "assistant",
                "content": response_message.content,
                "tool_calls": [_tool_call_dict(call) for call in tool_calls],
            }
            messages.append(assistant_message)

            for call in tool_calls:
                name, arguments, parse_error = _parse_tool_call(call)
                if parse_error:
                    result = {
                        "ok": False,
                        "error": parse_error,
                        "summary": "工具参数解析失败",
                    }
                else:
                    result = execute_tool(session, name, arguments)
                actions.append({
                    "name": name,
                    "arguments": _safe_arguments(arguments),
                    "ok": result["ok"],
                    "summary": result.get("summary", ""),
                    **({"error": result["error"]} if not result["ok"] else {}),
                })
                messages.append({
                    "role": "tool",
                    "tool_call_id": getattr(call, "id", ""),
                    "name": name,
                    "content": _json_dumps(result),
                })

            if _round == MAX_TOOL_ROUNDS - 1:
                return {
                    "answer": "操作步骤太多，请分成两步告诉我。",
                    "actions": actions,
                }
    except ai_service.AiError as exc:
        if _unsupported_tools(str(exc)):
            return _fallback_without_tools(message, history, session)
        raise

    raise AgentError("Agent 执行异常")


def _tool_call_dict(call: Any) -> dict[str, Any]:
    """把 SDK tool_call 转成下一轮请求可用的字典。"""
    function = getattr(call, "function", None)
    return {
        "id": getattr(call, "id", ""),
        "type": getattr(call, "type", "function"),
        "function": {
            "name": getattr(function, "name", ""),
            "arguments": getattr(function, "arguments", "{}"),
        },
    }


def _parse_tool_call(call: Any) -> tuple[str, dict[str, Any], str | None]:
    """解析单次工具名和参数；参数 JSON 损坏时返回错误说明。"""
    function = getattr(call, "function", None)
    name = getattr(function, "name", "")
    raw_arguments = getattr(function, "arguments", "{}")
    try:
        arguments = json.loads(raw_arguments or "{}")
    except json.JSONDecodeError as exc:
        return name, {}, f"工具参数不是合法 JSON：{exc}"
    if not isinstance(arguments, dict):
        return name, {}, "工具参数必须是 JSON 对象"
    return name, arguments, None


def _safe_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    """操作记录中不保留敏感键名，且保证可 JSON 序列化。"""
    result: dict[str, Any] = {}
    for key, value in arguments.items():
        if "key" in str(key).lower():
            continue
        result[key] = value
    return result


def _json_dumps(value: Any) -> str:
    """JSON 序列化；日期等复杂类型转字符串。"""
    return json.dumps(value, ensure_ascii=False, default=str)


# ============ 无 Function Calling 降级 ============
def _fallback_without_tools(
    user_message: str,
    history: list[dict[str, Any]] | None,
    session: Session,
) -> dict[str, Any]:
    """提示词解析模式：一次最多执行一个工具。"""
    base_messages: list[dict[str, Any]] = [
        {"role": "system", "content": FALLBACK_PLAN_PROMPT},
        *_clean_history(history),
        {"role": "user", "content": user_message[:MAX_MESSAGE_LENGTH]},
    ]

    plan_text = _fallback_plan_text(base_messages, strict=False)
    calls = _parse_fallback_plan(plan_text)
    if calls is None:
        base_messages[0] = {"role": "system", "content": FALLBACK_STRICT_PROMPT}
        plan_text = _fallback_plan_text(base_messages, strict=True)
        calls = _parse_fallback_plan(plan_text)
    if calls is None:
        raise AgentError("当前模型不支持 Function Calling，且指令解析失败，请更换模型")
    if not calls:
        answer = _fallback_plain_answer(user_message, history)
        return {"answer": answer, "actions": []}
    if len(calls) > 1:
        return {
            "answer": "当前模型的兼容模式一次只能执行一个操作，请分两步告诉我。",
            "actions": [],
        }

    call = calls[0]
    name = str(call.get("name") or "")
    arguments = call.get("arguments") if isinstance(call.get("arguments"), dict) else {}
    result = execute_tool(session, name, arguments)
    action = {
        "name": name,
        "arguments": _safe_arguments(arguments),
        "ok": result["ok"],
        "summary": result.get("summary", ""),
    }
    if not result["ok"]:
        action["error"] = result["error"]

    answer_messages = [
        {"role": "system", "content": "根据工具执行结果，用一句到几句中文简洁回复用户。"},
        {"role": "user", "content": f"工具结果：{_json_dumps(result)}\n用户原始请求：{user_message}"},
    ]
    completion = _create_completion(answer_messages, use_tools=False)
    answer = completion.choices[0].message.content or "操作已完成"
    return {"answer": answer.strip(), "actions": [action]}


def _fallback_plan_text(messages: list[dict[str, Any]], strict: bool) -> str:
    """请求降级模式的工具计划。"""
    try:
        completion = _create_completion(messages, use_tools=False)
        return completion.choices[0].message.content or ""
    except ai_service.AiError as exc:
        raise AgentError(str(exc)) from exc


def _parse_fallback_plan(text: str) -> list[dict[str, Any]] | None:
    """解析降级计划 JSON。"""
    cleaned = (text or "").strip()
    cleaned = re.sub(r"^```(?:json)?|```$", "", cleaned, flags=re.MULTILINE).strip()
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        return None
    calls = payload.get("calls")
    if not isinstance(calls, list):
        return None
    return calls


def _fallback_plain_answer(user_message: str, history: list[dict[str, Any]] | None) -> str:
    """无需工具时的普通回答。"""
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": "你是知识消化平台的智能助手，用中文简洁自然地回复。"},
        *_clean_history(history),
        {"role": "user", "content": user_message},
    ]
    completion = _create_completion(messages, use_tools=False)
    return (completion.choices[0].message.content or "你好，我在。").strip()