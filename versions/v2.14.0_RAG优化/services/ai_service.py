# -*- coding: utf-8 -*-
"""
AI 配置与调用封装（v1.1 完善真实调用）。

- get_config / save_config：非敏感配置存 ai_config.json，Key 存 keyring
- chat：调用 OpenAI 兼容接口，30 秒超时，重试 1 次
- rewrite_to_plain：改写成通俗小白笔记
- generate_tags：生成标签，失败降级空列表
- generate_title：根据手动粘贴正文生成标题
- evaluate_quality：AI 评估改写质量
- rewrite_with_style：按指定风格重新改写
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import config

logger = logging.getLogger(__name__)

KEYRING_SERVICE = "knowledge-digest"
KEYRING_USERNAME = "ai_api_key"

# 配置默认值：ai_config.json 不存在或字段缺失时回退
DEFAULT_CONFIG: dict[str, Any] = {
    "base_url": "",
    "model": "",
    "embedding_base_url": "https://ark.cn-beijing.volces.com/api/v3",
    "embedding_model": "doubao-embedding-text-240715",
    "use_local_embedding": False,
    "rerank_enabled": False,
    "rerank_model": "doubao-rerank-32k",
}

# 模型调用超时 30 秒，SDK 内部重试 1 次（共最多请求 2 次）
CHAT_TIMEOUT = 30.0
CHAT_MAX_RETRIES = 1

# 改写笔记的系统提示词（严格按需求原文）
REWRITE_SYSTEM_PROMPT = (
    "你是一个知识科普专家，擅长把复杂内容改写成零基础小白也能看懂的通俗笔记。"
    "要求：1.用大白话，避免专业术语，必须用术语时要解释；"
    "2.分点说明，逻辑清晰；3.保留核心信息，不丢失关键内容；"
    "4.字数300-800字；5.结尾加一句“一句话总结”。"
)

# 生成标签的提示词：要求只输出逗号分隔的标签，方便解析
TAGS_PROMPT = (
    "请根据下面文章的标题和正文，生成3-5个概括主题的标签。"
    "只输出标签本身，用英文逗号分隔，不要编号、不要解释、不要加“标签：”等前缀。\n\n"
    "标题：{title}\n\n正文：{content}"
)


# 根据正文生成标题：限制长度，方便手动粘贴时自动补标题
TITLE_PROMPT = (
    "请根据下面正文生成一个中文标题，不超过20个字，"
    "只输出标题，不要解释、不要标点结尾。\n\n正文：{content}"
)


# 质量评估提示：强制输出 JSON，方便后端解析
QUALITY_PROMPT = (
    "请从准确性、通俗性、完整性三个维度，给下面笔记的改写质量打分。"
    "分数必须是1到5的数字，允许一位小数。"
    "只输出 JSON，格式为："
    '{{"score": 4, "reason": "不超过50字的简短理由"}}'
    "不要输出 Markdown，不要解释。\n\n"
    "笔记标题：{title}\n\n笔记内容：{content}"
)

# 会议整理提示词：强制输出固定结构的 JSON，缺的信息留空、禁止编造
ORGANIZE_MEETING_PROMPT = (
    "请把下面的原始会议记录整理成结构化会议纪要。只输出 JSON，不要输出 Markdown 或解释。"
    "JSON 格式为："
    '{{"topic": "会议主题", "attendees": "参会人，逗号分隔", "meeting_time": "YYYY-MM-DD HH:MM 或留空", '
    '"discussion": "议题讨论内容", "decisions": ["决议1"], "todos": [{{"content": "待办内容", "assignee": "负责人或留空"}}]}}'
    "要求：1.原文没有的信息留空，禁止编造；2.discussion 用条理清晰的中文概述；"
    "3.待办尽量写明负责人；4.所有内容使用中文。\n\n原始记录：{raw_text}"
)

# 相关性筛选提示词：强制只输出 JSON，便于程序判定
RELEVANCE_PROMPT = (
    "请判断下面这篇文章是否与给定的关注主题相关。只输出 JSON，不要输出其他内容。"
    "JSON 格式为："
    '{{"relevant": true, "reason": "不超过50字的简短原因"}}\n'
    "关注主题：{focus_topics}\n"
    "文章标题：{title}\n"
    "文章摘要：{content}"
)

# 风格重写的系统提示词，键名固定为前端三种选项
STYLE_SYSTEM_PROMPTS = {
    "通俗": (
        "你是知识科普专家。用零基础小白能听懂的大白话改写，"
        "分点清楚，保留核心信息，字数300-800字。"
    ),
    "精简": (
        "你是笔记整理专家。把内容改成要点式笔记，"
        "只保留关键信息，总字数不超过300字。"
    ),
    "详细": (
        "你是知识讲解老师。围绕主题展开解释，必要时补充生活化类比，"
        "内容完整，总字数不少于800字。"
    ),
}


class AiError(Exception):
    """AI 调用失败，message 为可直接展示给用户的中文信息。"""


def _load_keyring() -> Any:
    """延迟导入 keyring，缺失时给出明确错误。"""
    try:
        import keyring  # noqa: WPS433
        return keyring
    except ImportError as exc:
        raise RuntimeError("当前环境缺少 keyring 库，无法读取密钥。") from exc


def get_config() -> dict[str, Any]:
    """读取非敏感 AI 配置；旧配置缺字段时自动补默认值。"""
    if not config.AI_CONFIG_PATH.exists():
        return DEFAULT_CONFIG.copy()
    try:
        raw = json.loads(config.AI_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("ai_config.json 读取失败：%s", exc)
        return DEFAULT_CONFIG.copy()
    if not isinstance(raw, dict):
        return DEFAULT_CONFIG.copy()

    result = DEFAULT_CONFIG.copy()
    for key, default in DEFAULT_CONFIG.items():
        value = raw.get(key, default)
        if isinstance(default, bool):
            if isinstance(value, bool):
                result[key] = value
            elif isinstance(value, str):
                result[key] = value.strip().lower() in ("1", "true", "yes", "on")
        elif isinstance(default, str) and isinstance(value, str):
            result[key] = value
    return result


def save_config(
    base_url: str,
    model: str,
    api_key: str,
    embedding_base_url: str | None = None,
    embedding_model: str | None = None,
    use_local_embedding: bool | None = None,
    rerank_enabled: bool | None = None,
    rerank_model: str | None = None,
) -> bool:
    """写入非敏感 AI 配置；api_key 非空时同步写入 keyring。"""
    config.ensure_dirs()
    payload = get_config()
    payload.update({
        "base_url": (base_url or "").strip(),
        "model": (model or "").strip(),
    })
    if embedding_base_url is not None:
        payload["embedding_base_url"] = embedding_base_url.strip()
    if embedding_model is not None:
        payload["embedding_model"] = embedding_model.strip()
    if use_local_embedding is not None:
        payload["use_local_embedding"] = bool(use_local_embedding)
    if rerank_enabled is not None:
        payload["rerank_enabled"] = bool(rerank_enabled)
    if rerank_model is not None:
        payload["rerank_model"] = rerank_model.strip()
    config.AI_CONFIG_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    key_saved = False
    if api_key:
        try:
            _load_keyring().set_password(KEYRING_SERVICE, KEYRING_USERNAME, api_key)
            key_saved = True
        except Exception as exc:  # noqa: BLE001 - 跨平台 keyring 异常类型不统一
            logger.warning("keyring 写入失败（沙箱环境常见）：%s", exc)
    return key_saved

def get_api_key() -> str:
    """从 keyring 读取 API Key；读取失败或未配置返回空字符串。"""
    try:
        return _load_keyring().get_password(KEYRING_SERVICE, KEYRING_USERNAME) or ""
    except Exception as exc:  # noqa: BLE001 - keyring 后端异常类型不统一
        logger.warning("keyring 读取失败：%s", exc)
        return ""


def test_connection() -> str:
    """v1.0 占位文案，真实测试连接后续版本再做。"""
    return "测试功能v1.1实现"


def chat(prompt: str, system_prompt: str | None = None) -> str:
    """调用 OpenAI 兼容接口，返回模型生成文本。

    配置缺失或调用失败抛 AiError，不返回错误字符串冒充正文。
    """
    settings = get_config()
    base_url = settings.get("base_url", "").strip()
    model = settings.get("model", "").strip()
    api_key = get_api_key()

    if not base_url:
        raise AiError("请先在设置页填写 Base URL 并保存")
    if not model:
        raise AiError("请先在设置页填写模型名称并保存")
    if not api_key:
        raise AiError("请先在设置页填写 API Key 并保存")

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    try:
        from openai import OpenAI  # 延迟导入，缺库时给出明确提示
    except ImportError as exc:
        raise RuntimeError("当前环境缺少 openai 库，无法调用大模型。") from exc

    try:
        client = OpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=CHAT_TIMEOUT,
            max_retries=CHAT_MAX_RETRIES,
        )
        completion = client.chat.completions.create(
            model=model,
            messages=messages,
        )
    except Exception as exc:  # noqa: BLE001 - SDK 异常类型随版本变化，统一转换
        logger.warning("模型调用失败：%s", exc)
        raise AiError(f"AI 调用失败：{exc}") from exc

    text = completion.choices[0].message.content
    if not text:
        raise AiError("AI 返回内容为空，请重试")
    return text.strip()


def rewrite_to_plain(title: str, content: str) -> str:
    """把文章改写成通俗易懂的小白笔记。失败抛 AiError。"""
    user_prompt = (
        "把下面这篇文章改写成通俗笔记：\n\n"
        f"标题：{title}\n\n正文：{content}"
    )
    return chat(user_prompt, system_prompt=REWRITE_SYSTEM_PROMPT)


def generate_tags(title: str, content: str) -> list[str]:
    """AI 生成 3-5 个标签；失败时降级返回空列表，不拖垮主流程。"""
    # 为省 token，送入的正文再限一次长度
    short_content = (content or "")[:3000]
    prompt = TAGS_PROMPT.format(title=title or "无标题", content=short_content)
    try:
        raw = chat(prompt)
    except AiError as exc:
        logger.warning("标签生成失败，降级为空标签：%s", exc)
        return []

    # 复用笔记服务的标签清洗逻辑
    from services.note_service import parse_tags

    tags = parse_tags(raw)
    # 只保留前 5 个，防止模型不听话输出一大串
    return tags[:5]


def generate_title(content: str) -> str:
    """根据手动粘贴正文生成标题；失败抛 AiError。"""
    short_content = (content or "")[:3000]
    title = chat(TITLE_PROMPT.format(content=short_content))
    return title.strip()[:20]

def evaluate_quality(title: str, content: str) -> dict[str, Any]:
    """调用 AI 给笔记质量打分，返回评分和简短理由。"""
    short_content = (content or "")[:3000]
    raw = chat(QUALITY_PROMPT.format(
        title=title or "无标题",
        content=short_content,
    ))
    cleaned = re.sub(
        r"^```(?:json)?|```$",
        "",
        raw.strip(),
        flags=re.MULTILINE,
    ).strip()
    try:
        payload = json.loads(cleaned)
        score = float(payload.get("score"))
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise AiError("质量评估结果格式错误，请重试") from exc

    if score < 1 or score > 5:
        raise AiError("质量评分必须在1到5之间")
    reason = str(payload.get("reason") or "AI 未给出理由").strip()
    return {"score": round(score, 1), "reason": reason[:200]}


def rewrite_with_style(title: str, content: str, style: str) -> str:
    """按指定风格重新改写正文。"""
    system_prompt = STYLE_SYSTEM_PROMPTS.get(style)
    if system_prompt is None:
        raise AiError("不支持的改写风格，只能选择通俗、精简或详细")

    prompt = (
        "请按要求改写下面的笔记。\n\n"
        f"标题：{title or '无标题'}\n\n正文：{content}"
    )
    return chat(prompt, system_prompt=system_prompt)

def organize_meeting(raw_text: str) -> dict[str, Any]:
    """把原始会议记录交给 AI 整理成结构化纪要。"""
    raw_text = str(raw_text or "").strip()
    if not raw_text:
        raise AiError("请粘贴会议记录内容")
    # 超长内容截断，保护模型上下文
    raw_text = raw_text[:6000]
    raw = chat(ORGANIZE_MEETING_PROMPT.format(raw_text=raw_text))
    cleaned = re.sub(
        r"^```(?:json)?|```$",
        "",
        raw.strip(),
        flags=re.MULTILINE,
    ).strip()
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise AiError("会议整理结果格式错误，请重试") from exc
    if not isinstance(payload, dict):
        raise AiError("会议整理结果格式错误，请重试")

    def clean_text(value: Any) -> str:
        return str(value or "").strip()

    decisions = payload.get("decisions")
    todos = payload.get("todos")
    result = {
        "topic": clean_text(payload.get("topic")),
        "attendees": clean_text(payload.get("attendees")),
        "meeting_time": clean_text(payload.get("meeting_time")),
        "discussion": clean_text(payload.get("discussion")),
        "decisions": decisions if isinstance(decisions, list) else [],
        "todos": [],
    }
    if isinstance(todos, list):
        for todo in todos:
            if not isinstance(todo, dict):
                continue
            content = clean_text(todo.get("content"))
            if not content:
                continue
            result["todos"].append({
                "content": content,
                "assignee": clean_text(todo.get("assignee")),
            })
    return result

def is_relevant(title: str, content: str, focus_topics: str) -> dict[str, Any]:
    """判断文章是否与关注主题相关；任何 AI/解析失败都默认相关，避免漏内容。"""
    focus_topics = str(focus_topics or "").strip()
    if not focus_topics:
        # 没有主题不做筛选，调用方本不该走到这里
        return {"relevant": True, "reason": "未设置关注主题，默认相关"}

    try:
        raw = chat(RELEVANCE_PROMPT.format(
            focus_topics=focus_topics,
            title=str(title or "无标题"),
            content=str(content or "")[:500],
        ))
    except AiError as exc:
        logger.warning("相关性 AI 调用失败，默认按相关处理：%s", exc)
        return {"relevant": True, "reason": "AI判断失败，默认按相关处理"}

    cleaned = re.sub(
        r"^```(?:json)?|```$",
        "",
        raw.strip(),
        flags=re.MULTILINE,
    ).strip()
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        logger.warning("相关性结果解析失败，默认按相关处理：%s", exc)
        return {"relevant": True, "reason": "AI判断失败，默认按相关处理"}

    if not isinstance(payload, dict) or not isinstance(payload.get("relevant"), bool):
        logger.warning("相关性结果格式错误，默认按相关处理")
        return {"relevant": True, "reason": "AI判断失败，默认按相关处理"}

    reason = str(payload.get("reason") or "").strip()[:100]
    return {"relevant": payload["relevant"], "reason": reason or "AI 未说明原因"}
