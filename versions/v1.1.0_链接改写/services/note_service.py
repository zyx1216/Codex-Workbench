# -*- coding: utf-8 -*-
"""
笔记数据管理（v1.1 新增）。

所有函数接收外部传入的 SQLAlchemy session，由 API 层控制事务。
- create_note：新建笔记，标签存 JSON 字符串
- list_notes：标题+内容关键词过滤，分页，创建时间倒序
- get_note_by_id / delete_note
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from sqlalchemy import select

from models.models import Note

# 中英文逗号都作为标签分隔符
_TAG_SPLIT = re.compile(r"[,，]")


class NoteNotFound(Exception):
    """笔记不存在，message 为中文提示。"""


def parse_tags(text: str) -> list[str]:
    """标签字符串切分：去空白、去重保序。"""
    result: list[str] = []
    for part in _TAG_SPLIT.split(text or ""):
        tag = part.strip()
        if tag and tag not in result:
            result.append(tag)
    return result


def create_note(
    session: Any,
    title: str,
    content: str,
    original_url: str | None = None,
    tags: list[str] | None = None,
    source: str = "手动输入",
    category: str = "默认",
) -> Note:
    """新建笔记并写入数据库。标题为空时用"无标题"，与旧版习惯一致。"""
    now = datetime.now()
    note = Note(
        title=(title or "").strip() or "无标题",
        content=content or "",
        original_url=(original_url or "").strip() or None,
        source=source or "手动输入",
        tags=json.dumps(tags or [], ensure_ascii=False),
        category=category or "默认",
        created_at=now,
        updated_at=now,
    )
    session.add(note)
    session.commit()
    session.refresh(note)
    return note


def list_notes(
    session: Any,
    keyword: str = "",
    page: int = 1,
    size: int = 20,
) -> tuple[list[Note], int]:
    """查询笔记，返回（当前页笔记列表, 总数）。

    过滤在 Python 侧完成：本地单机数据量小，没必要上复杂 SQL。
    """
    keyword = (keyword or "").strip().lower()
    all_notes = list(session.scalars(select(Note)).all())

    if keyword:
        all_notes = [
            note
            for note in all_notes
            if keyword in note.title.lower() or keyword in note.content.lower()
        ]

    # 创建时间倒序，ID 兜底保证稳定
    all_notes.sort(key=lambda note: (note.created_at, note.id), reverse=True)

    total = len(all_notes)
    page = max(page, 1)
    size = max(size, 1)
    start = (page - 1) * size
    return all_notes[start:start + size], total


def get_note_by_id(session: Any, note_id: int) -> Note:
    """按主键查笔记，不存在抛 NoteNotFound。"""
    note = session.get(Note, note_id)
    if note is None:
        raise NoteNotFound("笔记不存在或已被删除")
    return note


def delete_note(session: Any, note_id: int) -> None:
    """直接真删笔记（本版无回收站）；不存在抛 NoteNotFound。"""
    note = get_note_by_id(session, note_id)
    session.delete(note)
    session.commit()


def serialize_note(note: Note) -> dict[str, Any]:
    """笔记转前端字典：标签还原成数组，时间转 ISO 字符串。"""
    try:
        tags = json.loads(note.tags) if note.tags else []
    except json.JSONDecodeError:
        tags = []
    return {
        "id": note.id,
        "title": note.title,
        "content": note.content,
        "original_url": note.original_url,
        "source": note.source,
        "tags": tags if isinstance(tags, list) else [],
        "category": note.category,
        "created_at": note.created_at.isoformat() if note.created_at else None,
        "updated_at": note.updated_at.isoformat() if note.updated_at else None,
    }
