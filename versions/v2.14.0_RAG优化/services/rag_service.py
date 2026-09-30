# -*- coding: utf-8 -*-
"""RAG 问答服务：混合检索、可选重排和引用来源。"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

from models.models import Note
from services import ai_service, rerank_service, vector_service

logger = logging.getLogger(__name__)

MIN_SCORE = 0.20
NOTE_LIMIT = 1500
CONTEXT_LIMIT = 6000
VECTOR_TOP_K = 20
KEYWORD_TOP_K = 10
FINAL_TOP_K = 5

SYSTEM_PROMPT = (
    "你是一个个人知识库助手，根据用户提供的笔记内容回答问题。\n"
    "规则：\n"
    "1. 只根据提供的笔记内容回答，不要编造笔记里没有的信息\n"
    '2. 如果笔记里没有相关内容，直接说 "笔记中没有找到相关内容"\n'
    "3. 回答要简洁明了，重点突出\n"
    "4. 引用笔记内容时用 [1] [2] 标注对应笔记\n"
    "5. 用中文回答"
)

QUESTION_PROMPT = ChatPromptTemplate.from_messages([
    ("system", SYSTEM_PROMPT),
    ("human", "请基于以下笔记内容回答问题。\n\n笔记内容：\n{context}\n\n问题：{question}"),
])


def _escape_like(value: str) -> str:
    """转义 LIKE 通配符，避免用户输入 % 或 _ 时误匹配全部。"""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _tags_from_note(note: Note) -> list[str]:
    """安全读取笔记标签，损坏时返回空列表。"""
    try:
        tags = json.loads(note.tags or "[]")
    except json.JSONDecodeError:
        return []
    return [str(item) for item in tags] if isinstance(tags, list) else []


def _keyword_score(note: Note, query: str) -> float:
    """给关键词命中一个简单、稳定的排序分。"""
    query_lower = query.lower()
    title = (note.title or "").lower()
    content = (note.content or "").lower()
    tags = [item.lower() for item in _tags_from_note(note)]
    category = (note.category or "").lower()

    score = 0.0
    if title == query_lower:
        score += 1.0
    elif title.startswith(query_lower):
        score += 0.85
    elif query_lower in title:
        score += 0.7
    if query_lower in tags:
        score += 0.55
    elif any(query_lower in tag for tag in tags):
        score += 0.35
    if query_lower in category:
        score += 0.3
    if query_lower in content:
        score += 0.25 + min(content.count(query_lower), 3) * 0.05
    return round(score, 4)


def _keyword_search(session: Session, query: str, limit: int = KEYWORD_TOP_K) -> list[dict[str, Any]]:
    """使用 SQLite LIKE 做关键词检索，返回经过简单打分的笔记 ID。"""
    query = (query or "").strip()
    if not query:
        return []
    pattern = f"%{_escape_like(query)}%"
    statement = (
        select(Note)
        .where(
            or_(
                Note.title.ilike(pattern, escape="\\"),
                Note.content.ilike(pattern, escape="\\"),
                Note.tags.ilike(pattern, escape="\\"),
                Note.category.ilike(pattern, escape="\\"),
            )
        )
        .limit(max(limit * 3, limit))
    )
    notes = list(session.scalars(statement).all())
    items = [
        {"id": note.id, "score": _keyword_score(note, query), "keyword_score": _keyword_score(note, query)}
        for note in notes
        if _keyword_score(note, query) > 0
    ]
    items.sort(key=lambda item: (-item["score"], item["id"]))
    return items[:max(1, limit)]


def _note_text(note: Note) -> tuple[str, str]:
    """构造送给 rerank 的文本和前端摘要。"""
    tags = ", ".join(_tags_from_note(note))
    content = (note.content or "").strip()
    text = f"标题：{note.title or '无标题'}\n分类：{note.category or '默认'}\n标签：{tags}\n正文：{content}"
    summary = content[:120]
    return text, summary


def retrieve_candidates(session: Session, query: str, limit: int = FINAL_TOP_K) -> list[dict[str, Any]]:
    """向量 top20 + 关键词 top10 合并去重，可选 rerank 后取 top5。"""
    query = (query or "").strip()
    if not query:
        return []
    vector_hits: list[dict[str, Any]] = []
    try:
        vector_hits = vector_service.search_notes(query, n_results=VECTOR_TOP_K)
    except vector_service.VectorError:
        logger.info("向量检索不可用，本轮仅使用关键词检索")

    keyword_hits = _keyword_search(session, query, limit=KEYWORD_TOP_K)
    candidates: dict[int, dict[str, Any]] = {}
    for rank, hit in enumerate(vector_hits):
        note_id = int(hit["id"])
        candidates[note_id] = {
            "id": note_id,
            "score": float(hit.get("score", 0.0)),
            "keyword_score": 0.0,
            "vector_rank": rank,
            "keyword_rank": 9999,
        }
    for rank, hit in enumerate(keyword_hits):
        note_id = int(hit["id"])
        item = candidates.setdefault(note_id, {
            "id": note_id,
            "score": 0.0,
            "keyword_score": 0.0,
            "vector_rank": 9999,
            "keyword_rank": rank,
        })
        item["keyword_score"] = float(hit.get("keyword_score", hit.get("score", 0.0)))
        item["keyword_rank"] = min(item["keyword_rank"], rank)

    enriched: list[dict[str, Any]] = []
    for note_id, item in candidates.items():
        note = session.get(Note, note_id)
        if note is None:
            continue
        text, summary = _note_text(note)
        enriched.append({
            **item,
            "id": note.id,
            "title": note.title or "无标题",
            "category": note.category or "默认",
            "summary": summary,
            "text": text,
        })
    enriched.sort(key=lambda item: (
        item["vector_rank"], -item["keyword_score"], -item["score"], item["id"]
    ))

    if rerank_service.is_enabled() and len(enriched) > 1:
        enriched = rerank_service.rerank_documents(query, enriched, top_n=max(1, limit))
    return enriched[:max(1, limit)]


def answer_question(question: str, session: Session) -> dict[str, Any]:
    """单轮 RAG 问答，返回回答和可点击的引用来源。"""
    question = (question or "").strip()
    if not question:
        raise vector_service.VectorError("问题不能为空")
    if vector_service.is_rebuilding():
        raise vector_service.VectorError("正在重建向量库，请稍后再试")
    question = question[:1000]

    candidates = retrieve_candidates(session, question, limit=FINAL_TOP_K)
    valid_sources: list[dict[str, Any]] = []
    context_parts: list[str] = []
    used_length = 0
    for index, item in enumerate(candidates, start=1):
        body = (item.get("text") or "")[:NOTE_LIMIT]
        block = f"[{index}] 标题：{item['title']}\n分类：{item['category']}\n正文：{body}"
        if used_length + len(block) > CONTEXT_LIMIT:
            break
        if float(item.get("score", 0.0)) < MIN_SCORE and not item.get("keyword_score"):
            continue
        context_parts.append(block)
        used_length += len(block)
        valid_sources.append({
            "id": item["id"],
            "title": item["title"],
            "category": item["category"],
            "score": float(item.get("score", 0.0)),
        })

    if not valid_sources:
        return {"answer": "笔记中没有找到相关内容", "sources": []}

    try:
        answer = (QUESTION_PROMPT | _chat_model() | StrOutputParser()).invoke({
            "context": "\n\n".join(context_parts),
            "question": question,
        })
    except ai_service.AiError:
        raise
    except Exception as exc:  # noqa: BLE001 - LangChain/OpenAI 异常类型不统一
        logger.warning("RAG 模型调用失败：%s", exc)
        raise ai_service.AiError(f"AI 调用失败：{exc}") from exc

    answer = answer.strip()
    if not answer:
        raise ai_service.AiError("AI 返回内容为空，请重试")
    return {"answer": answer, "sources": valid_sources}


def _chat_model() -> ChatOpenAI:
    """创建 LangChain OpenAI 兼容聊天模型。"""
    settings = ai_service.get_config()
    base_url = str(settings.get("base_url", "") or "").strip()
    model = str(settings.get("model", "") or "").strip()
    api_key = ai_service.get_api_key()
    if not base_url:
        raise ai_service.AiError("请先在设置页填写 Base URL 并保存")
    if not model:
        raise ai_service.AiError("请先在设置页填写模型名称并保存")
    if not api_key:
        raise ai_service.AiError("请先在设置页填写 API Key 并保存")
    return ChatOpenAI(model=model, api_key=api_key, base_url=base_url, timeout=30, max_retries=1)