# -*- coding: utf-8 -*-
"""
笔记模块业务服务。

Streamlit 页面只负责交互和展示；笔记的增删改查、标签解析、
分类/标签/关键词过滤、软删除快照等逻辑集中放在这里。
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from models.models import Note, TrashItem

# 与计划模块保持一致的固定分类；“默认”用于未分类笔记
NOTE_CATEGORIES = ("日程安排", "学习", "旅行", "会议", "工作", "生活", "默认")

CATEGORY_ICONS = {
    "日程安排": "🗓",
    "学习": "📚",
    "旅行": "✈️",
    "会议": "📋",
    "工作": "💼",
    "生活": "🏠",
    "默认": "📌",
}


class NoteValidationError(ValueError):
    """笔记数据不合法，例如要操作的笔记不存在。"""


def category_icon(category: str | None) -> str:
    """返回分类图标；未知分类使用普通别针图标。"""
    return CATEGORY_ICONS.get(category or "默认", "📌")


def category_label(category: str | None) -> str:
    """返回带图标的分类名称。"""
    category = category or "默认"
    return f"{category_icon(category)} {category}"


def parse_tags(text: str | list[str] | None) -> list[str]:
    """
    把逗号分隔的标签文本解析成数组。

    支持中英文逗号，自动去空白、去重并保持首次出现的顺序。
    """
    if isinstance(text, (list, tuple)):
        raw_items = text
    else:
        raw_items = re.split(r"[,，]", str(text or ""))

    tags: list[str] = []
    for item in raw_items:
        tag = str(item).strip()
        if tag and tag not in tags:
            tags.append(tag)
    return tags


def tags_to_text(tags: str | list[str] | None) -> str:
    """把标签数组还原成输入框里的逗号分隔文本。"""
    if isinstance(tags, str):
        try:
            tags = json.loads(tags or "[]")
        except (ValueError, TypeError):
            tags = parse_tags(tags)
    return ", ".join(str(tag).strip() for tag in (tags or []) if str(tag).strip())


def load_tags(note: Note) -> list[str]:
    """安全读取笔记的标签数组；历史脏数据按空标签处理。"""
    try:
        tags = json.loads(note.tags or "[]")
    except (ValueError, TypeError):
        return []
    return [str(tag).strip() for tag in tags if str(tag).strip()]


def all_tags(session: Any) -> list[str]:
    """汇总全部笔记里出现过的标签，按名称排序后返回。"""
    tag_set: set[str] = set()
    for note in session.query(Note).all():
        tag_set.update(load_tags(note))
    return sorted(tag_set)


def list_notes(
    session: Any,
    category: str | None = None,
    tag: str | None = None,
    keyword: str = "",
) -> list[Note]:
    """
    查询笔记。

    本地单机数据量小，分类、标签和关键词在 Python 侧过滤：
    分类精确匹配；标签精确匹配；关键词对标题和正文做大小写不敏感包含。
    排序固定为更新时间倒序、ID 倒序兜底。
    """
    notes = session.query(Note).order_by(Note.updated_at.desc(), Note.id.desc()).all()
    query = (keyword or "").strip().lower()

    result: list[Note] = []
    for note in notes:
        if category and category != "全部" and (note.category or "默认") != category:
            continue
        if tag and tag not in load_tags(note):
            continue
        if query:
            haystack = f"{note.title or ''}\n{note.content or ''}".lower()
            if query not in haystack:
                continue
        result.append(note)
    return result


def create_note(
    session: Any,
    title: str,
    content: str,
    tag_text: str | list[str],
    category: str,
) -> Note:
    """新建笔记；标题允许为空，列表中以“无标题”展示。"""
    title = (title or "").strip()
    category = category or "默认"
    note = Note(
        title=title,
        content=content or "",
        tags=json.dumps(parse_tags(tag_text), ensure_ascii=False),
        category=category,
        spaced=False,
        spaced_level=0,
        spaced_next=None,
    )
    session.add(note)
    session.commit()
    session.refresh(note)
    return note


def update_note(
    session: Any,
    note_id: int,
    title: str,
    content: str,
    tag_text: str | list[str],
    category: str,
) -> Note:
    """更新笔记的标题、正文、标签和分类；不修改间隔复习状态。"""
    note = session.get(Note, note_id)
    if note is None:
        raise NoteValidationError("要编辑的笔记不存在，可能已被删除。")

    note.title = (title or "").strip()
    note.content = content or ""
    note.tags = json.dumps(parse_tags(tag_text), ensure_ascii=False)
    note.category = category or "默认"
    note.updated_at = datetime.now()
    session.commit()
    session.refresh(note)
    return note


def _datetime_to_text(value: datetime | None) -> str | None:
    """JSON 快照中的日期时间序列化。"""
    return value.isoformat(timespec="seconds") if value else None


def _note_snapshot(note: Note) -> dict[str, Any]:
    """把笔记序列化成以后可还原的 JSON 快照结构（含间隔复习字段）。"""
    return {
        "schema_version": 1,
        "note": {
            "id": note.id,
            "title": note.title,
            "content": note.content,
            "tags": note.tags,
            "category": note.category,
            "spaced": bool(note.spaced),
            "spaced_level": note.spaced_level,
            "spaced_next": note.spaced_next.isoformat() if note.spaced_next else None,
            "created_at": _datetime_to_text(note.created_at),
            "updated_at": _datetime_to_text(note.updated_at),
        },
    }


def soft_delete_note(session: Any, note_id: int) -> None:
    """
    软删除笔记。

    先把笔记全字段写入 trash_items 的 JSON 快照，再删除 notes 行。
    """
    note = session.get(Note, note_id)
    if note is None:
        raise NoteValidationError("要删除的笔记不存在，可能已经删除。")

    trash_item = TrashItem(
        item_type="note",
        item_id=str(note.id),
        snapshot=json.dumps(_note_snapshot(note), ensure_ascii=False),
    )
    session.add(trash_item)
    session.delete(note)
    session.commit()
