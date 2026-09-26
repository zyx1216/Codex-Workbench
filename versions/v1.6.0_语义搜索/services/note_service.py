# -*- coding: utf-8 -*-
"""
笔记数据管理。

所有函数接收外部传入的 SQLAlchemy session，由 API 层控制事务。
- create_note：新建笔记，标签存 JSON 字符串
- list_notes：关键词、标签、分类过滤，分页，创建时间倒序
- update_note：更新标题、正文、标签、分类
- all_tags / all_categories：聚合标签和分类及数量
- recent_notes / get_stats：首页数据
- get_note_by_id / delete_note
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any

from sqlalchemy import select

from models.models import Note, PendingItem
from services import vector_service

logger = logging.getLogger(__name__)

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


def tags_to_text(tags: list[str]) -> str:
    """标签列表转逗号分隔字符串，用于编辑表单回填。"""
    return ", ".join(tags or [])


def _note_tags(note: Note) -> list[str]:
    """读取单条笔记的标签 JSON，损坏时返回空列表。"""
    try:
        tags = json.loads(note.tags) if note.tags else []
    except json.JSONDecodeError:
        return []
    return tags if isinstance(tags, list) else []


def index_note(note: Note) -> tuple[bool, str]:
    """把已提交笔记写入向量库；失败时只设置警告，不回滚笔记。"""
    warning = ""
    try:
        vector_service.add_note(
            note.id, note.title, note.content, _note_tags(note)
        )
        indexed = True
    except vector_service.VectorError as exc:
        indexed = False
        warning = str(exc)
        logger.warning("笔记保留成功，但向量索引失败：%s", exc)
    setattr(note, "vector_indexed", indexed)
    setattr(note, "vector_warning", warning)
    return indexed, warning


def create_note(
    session: Any,
    title: str,
    content: str,
    original_url: str | None = None,
    tags: list[str] | None = None,
    source: str = "手动输入",
    category: str = "默认",
    commit: bool = True,
) -> Note:
    """新建笔记。commit=False 时由调用方和其他改动一起提交。"""
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
    if not commit:
        return note
    session.commit()
    session.refresh(note)
    index_note(note)
    return note


def list_notes(
    session: Any,
    keyword: str = "",
    tag: str = "",
    category: str = "",
    page: int = 1,
    size: int = 20,
) -> tuple[list[Note], int]:
    """查询笔记，返回（当前页笔记列表, 总数）。

    过滤在 Python 侧完成：标签是 JSON 字符串，SQL LIKE 会子串误匹配。
    """
    keyword = (keyword or "").strip().lower()
    tag = (tag or "").strip()
    category = (category or "").strip()

    all_notes = list(session.scalars(select(Note)).all())

    def matched(note: Note) -> bool:
        if keyword and not (
            keyword in note.title.lower() or keyword in note.content.lower()
        ):
            return False
        if tag and tag not in _note_tags(note):
            return False
        if category and note.category != category:
            return False
        return True

    all_notes = [note for note in all_notes if matched(note)]

    # 创建时间倒序，ID 兜底保证稳定
    all_notes.sort(key=lambda note: (note.created_at, note.id), reverse=True)

    total = len(all_notes)
    page = max(page, 1)
    size = max(size, 1)
    start = (page - 1) * size
    return all_notes[start:start + size], total


def update_note(
    session: Any,
    note_id: int,
    title: str,
    content: str,
    tags: list[str],
    category: str,
) -> Note:
    """更新标题、正文、标签、分类；原文链接和来源不动，刷新 updated_at。"""
    note = get_note_by_id(session, note_id)
    note.title = (title or "").strip() or "无标题"
    note.content = content or ""
    note.tags = json.dumps(tags or [], ensure_ascii=False)
    note.category = (category or "").strip() or "默认"
    note.updated_at = datetime.now()
    session.commit()
    session.refresh(note)

    warning = ""
    try:
        vector_service.update_note(
            note.id, note.title, note.content, tags or []
        )
        setattr(note, "vector_indexed", True)
    except vector_service.VectorError as exc:
        warning = str(exc)
        setattr(note, "vector_indexed", False)
        logger.warning("笔记更新成功，但向量索引更新失败：%s", exc)
    setattr(note, "vector_warning", warning)
    return note


def all_tags(session: Any) -> list[dict[str, Any]]:
    """聚合全部笔记标签，返回 [{name, count}]，按数量降序、名称兜底。"""
    counter: dict[str, int] = {}
    for note in session.scalars(select(Note)).all():
        for tag in _note_tags(note):
            counter[tag] = counter.get(tag, 0) + 1
    items = [{"name": name, "count": count} for name, count in counter.items()]
    items.sort(key=lambda item: (-item["count"], item["name"]))
    return items


def all_categories(session: Any) -> list[dict[str, Any]]:
    """聚合分类，返回 [{name, count}]，按数量降序。"""
    counter: dict[str, int] = {}
    for note in session.scalars(select(Note)).all():
        counter[note.category] = counter.get(note.category, 0) + 1
    items = [{"name": name, "count": count} for name, count in counter.items()]
    items.sort(key=lambda item: (-item["count"], item["name"]))
    return items


def recent_notes(session: Any, limit: int = 5) -> list[Note]:
    """返回最近 N 条笔记，排序与列表一致。"""
    notes, _ = list_notes(session, page=1, size=max(limit, 1))
    return notes


def get_stats(session: Any) -> dict[str, int]:
    """首页统计：笔记总数、今日新增、待处理数、去重标签数。"""
    notes = list(session.scalars(select(Note)).all())
    today = datetime.now().date()
    today_notes = sum(
        1 for note in notes
        if note.created_at and note.created_at.date() == today
    )
    # 待处理数：用 ORM 聚合函数统计 pending 状态条数
    from sqlalchemy import func

    pending_count = session.scalar(
        select(func.count()).select_from(PendingItem)
        .where(PendingItem.status == "pending")
    )
    tag_names = {tag for note in notes for tag in _note_tags(note)}
    return {
        "total_notes": len(notes),
        "today_notes": today_notes,
        "pending_count": int(pending_count or 0),
        "tags_count": len(tag_names),
    }


def get_note_by_id(session: Any, note_id: int) -> Note:
    """按主键查笔记，不存在抛 NoteNotFound。"""
    note = session.get(Note, note_id)
    if note is None:
        raise NoteNotFound("笔记不存在或已被删除")
    return note


def delete_note(session: Any, note_id: int) -> dict[str, str | bool]:
    """直接真删笔记（本版无回收站）；不存在抛 NoteNotFound。"""
    note = get_note_by_id(session, note_id)
    session.delete(note)
    session.commit()

    warning = ""
    indexed = True
    try:
        vector_service.delete_note(note_id)
    except vector_service.VectorError as exc:
        indexed = False
        warning = str(exc)
        logger.warning("笔记已删除，但向量索引删除失败：%s", exc)
    return {"indexed": indexed, "warning": warning}


def serialize_note(note: Note) -> dict[str, Any]:
    """笔记转前端字典：标签还原成数组，时间转 ISO 字符串。"""
    return {
        "id": note.id,
        "title": note.title,
        "content": note.content,
        "original_url": note.original_url,
        "source": note.source,
        "tags": _note_tags(note),
        "category": note.category,
        "created_at": note.created_at.isoformat() if note.created_at else None,
        "updated_at": note.updated_at.isoformat() if note.updated_at else None,
        "vector_indexed": bool(getattr(note, "vector_indexed", True)),
        "vector_warning": str(getattr(note, "vector_warning", "")),
    }
