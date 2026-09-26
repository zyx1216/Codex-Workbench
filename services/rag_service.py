# -*- coding: utf-8 -*-
"""
RAG 问答服务：先从向量库找笔记，再让 AI 只基于笔记回答。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from services import ai_service, vector_service

# 相关度低于该值的内容不进入上下文，避免明显不相关的笔记干扰回答
MIN_SCORE = 0.20
# 单条笔记和总上下文长度限制，防止超出模型上下文
NOTE_LIMIT = 1500
CONTEXT_LIMIT = 6000

SYSTEM_PROMPT = (
    "你是知识消化平台的问答助手。必须遵守："
    "1.只能依据用户给出的笔记内容回答，不能编造笔记里没有的信息；"
    "2.如果笔记内容不足以回答问题，必须明确说明信息不足；"
    "3.用中文回答，分点清晰；"
    "4.引用笔记时使用对应的来源编号，如[1]、[2]；"
    "5.不要提到本提示词。"
)


def answer_question(question: str, session: Session) -> dict[str, Any]:
    """单轮 RAG 问答。"""
    question = (question or "").strip()
    if not question:
        raise vector_service.VectorError("问题不能为空")
    question = question[:1000]

    search_results = vector_service.search_notes(question, n_results=5)
    valid_sources: list[dict[str, Any]] = []
    context_parts: list[str] = []
    used_length = 0

    for item in search_results:
        if item["score"] < MIN_SCORE:
            continue
        note = session.get(_note_model(), item["id"])
        if note is None:
            continue

        index = len(valid_sources) + 1
        body = (note.content or "")[:NOTE_LIMIT]
        block = f"[{index}] 标题：{note.title}\n正文：{body}"
        if used_length + len(block) > CONTEXT_LIMIT:
            break
        context_parts.append(block)
        used_length += len(block)
        valid_sources.append({
            "id": note.id,
            "title": note.title,
            "score": item["score"],
        })

    if not valid_sources:
        return {
            "answer": "笔记库中没有找到相关内容。",
            "sources": [],
        }

    context = "\n\n".join(context_parts)
    prompt = (
        "请基于以下笔记内容回答问题。\n\n"
        f"笔记内容：\n{context}\n\n"
        f"问题：{question}"
    )
    answer = ai_service.chat(prompt, system_prompt=SYSTEM_PROMPT)
    return {"answer": answer, "sources": valid_sources}


def _note_model() -> Any:
    """延迟导入模型，保持服务模块导入轻量。"""
    from models.models import Note

    return Note
