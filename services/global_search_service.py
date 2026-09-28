# -*- coding: utf-8 -*-
"""全局搜索服务：同时检索笔记、任务和日程。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select

from models.models import Note, Schedule, Task

# 每条搜索结果摘要最多保留的字数
SUMMARY_LIMIT = 120
# 每组最多返回数量
GROUP_LIMIT = 5


class GlobalSearchError(Exception):
    """全局搜索参数错误。"""


def _plain_text(value: Any) -> str:
    """把内容压成单行纯文本。"""
    return " ".join(str(value or "").split())


def _short_text(value: Any) -> str:
    """截取纯文本摘要。"""
    return _plain_text(value)[:SUMMARY_LIMIT]


def _time_text(value: datetime | None) -> str | None:
    """时间转前端可直接显示的 ISO 字符串。"""
    if not value:
        return None
    return value.isoformat(timespec="minutes")


def _title_rank(title: str, keyword: str) -> int:
    """计算标题命中优先级，数字越小越靠前。"""
    title_lower = title.lower()
    keyword_lower = keyword.lower()
    if title_lower == keyword_lower:
        return 0
    if title_lower.startswith(keyword_lower):
        return 1
    if keyword_lower in title_lower:
        return 2
    return 3


def _note_result(note: Note, keyword: str) -> dict[str, Any]:
    """组装笔记搜索结果。"""
    return {
        "id": note.id,
        "type": "note",
        "title": note.title or "无标题",
        "summary": _short_text(note.content),
        "meta": {"category": note.category or "默认"},
        "time": _time_text(note.created_at),
        "_rank": _title_rank(note.title or "", keyword),
        "_sort_time": note.created_at or datetime.min,
    }


def _task_result(task: Task, keyword: str) -> dict[str, Any]:
    """组装任务搜索结果。"""
    return {
        "id": task.id,
        "type": "task",
        "title": task.title or "无标题",
        "summary": _short_text(task.title),
        "meta": {
            "category": task.category,
            "priority": task.priority,
            "due_date": _time_text(task.due_date),
        },
        "time": _time_text(task.due_date or task.created_at),
        "_rank": _title_rank(task.title or "", keyword),
        "_sort_time": task.due_date or task.created_at or datetime.min,
    }


def _schedule_result(schedule: Schedule, keyword: str) -> dict[str, Any]:
    """组装日程搜索结果。"""
    return {
        "id": schedule.id,
        "type": "schedule",
        "title": schedule.title or "无标题",
        "summary": _short_text(schedule.description or schedule.location),
        "meta": {
            "schedule_type": schedule.schedule_type,
            "location": schedule.location,
            "start_time": _time_text(schedule.start_time),
            "end_time": _time_text(schedule.end_time),
        },
        "time": _time_text(schedule.start_time),
        "_rank": _title_rank(schedule.title or "", keyword),
        "_sort_time": schedule.start_time or schedule.created_at or datetime.min,
    }


def _clean_item(item: dict[str, Any]) -> dict[str, Any]:
    """删除服务层排序用的临时字段。"""
    item.pop("_rank", None)
    item.pop("_sort_time", None)
    return item


def _limit(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按标题命中和时间倒序排序后截取前五条。"""
    items.sort(key=lambda item: (item["_rank"], datetime.max - item["_sort_time"]))
    return [_clean_item(item) for item in items[:GROUP_LIMIT]]


def search_global(session: Any, keyword: str) -> dict[str, list[dict[str, Any]]]:
    """按关键词检索笔记、任务和日程。"""
    keyword = _plain_text(keyword)
    if not keyword:
        raise GlobalSearchError("请输入搜索关键词")

    like = f"%{keyword}%"

    notes = list(session.scalars(
        select(Note).where(
            (Note.title.ilike(like))
            | (Note.content.ilike(like))
            | (Note.tags.ilike(like))
            | (Note.category.ilike(like))
        )
    ).all())
    tasks = list(session.scalars(
        select(Task).where(
            (Task.title.ilike(like))
            | (Task.category.ilike(like))
            | (Task.priority.ilike(like))
        )
    ).all())
    schedules = list(session.scalars(
        select(Schedule).where(
            (Schedule.title.ilike(like))
            | (Schedule.location.ilike(like))
            | (Schedule.description.ilike(like))
            | (Schedule.schedule_type.ilike(like))
        )
    ).all())

    return {
        "notes": _limit([_note_result(item, keyword) for item in notes]),
        "tasks": _limit([_task_result(item, keyword) for item in tasks]),
        "schedules": _limit([_schedule_result(item, keyword) for item in schedules]),
    }
