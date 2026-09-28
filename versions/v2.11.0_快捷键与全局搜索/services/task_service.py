# -*- coding: utf-8 -*-
"""日常/工作任务管理服务。"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, object_session

from models.models import Note, Task

VALID_CATEGORIES = {"日常", "工作"}
VALID_PRIORITIES = {"高", "中", "低"}


class TaskError(Exception):
    """任务参数错误。"""


class TaskNotFound(Exception):
    """任务不存在。"""


def _normalize_due_date(value: Any) -> datetime | None:
    """把前端日期值统一成当天零点。"""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return datetime(value.year, value.month, value.day)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)

    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.strptime(text[:10], "%Y-%m-%d")
    except ValueError as exc:
        raise TaskError("截止日期格式不正确") from exc
    return parsed


def _validate_note(session: Session, note_id: Any) -> int | None:
    """校验关联笔记存在；空值表示不关联。"""
    if note_id in (None, ""):
        return None
    try:
        note_id = int(note_id)
    except (TypeError, ValueError) as exc:
        raise TaskError("关联笔记 ID 不正确") from exc
    note = session.get(Note, note_id)
    if note is None:
        raise TaskError("关联笔记不存在")
    return note_id


def _base_filters(
    statement,
    category: str | None = None,
    completed: bool | None = None,
):
    """给查询追加分类和完成状态过滤。"""
    if category:
        if category not in VALID_CATEGORIES:
            raise TaskError("任务分类只能是日常或工作")
        statement = statement.where(Task.category == category)
    if completed is not None:
        statement = statement.where(Task.completed == completed)
    return statement


def _ordered(statement):
    """统一任务排序：未完成在前、截止时间早的在前、无截止时间靠后。"""
    return statement.order_by(
        Task.completed.asc(),
        case((Task.due_date.is_(None), 1), else_=0),
        Task.due_date.asc(),
        Task.created_at.desc(),
        Task.id.desc(),
    )


def create_task(
    session: Session,
    title: str,
    category: str = "日常",
    priority: str = "中",
    due_date: Any = None,
    note_id: Any = None,
) -> Task:
    """创建任务。"""
    title = (title or "").strip()
    if not title:
        raise TaskError("任务标题不能为空")
    if category not in VALID_CATEGORIES:
        raise TaskError("任务分类只能是日常或工作")
    if priority not in VALID_PRIORITIES:
        raise TaskError("任务优先级只能是高、中或低")

    task = Task(
        title=title,
        category=category,
        priority=priority,
        due_date=_normalize_due_date(due_date),
        completed=False,
        note_id=_validate_note(session, note_id),
        created_at=datetime.now(),
        completed_at=None,
    )
    session.add(task)
    session.commit()
    session.refresh(task)
    return task


def list_tasks(
    session: Session,
    category: str | None = None,
    completed: bool | None = None,
) -> list[Task]:
    """查询任务列表。"""
    statement = _base_filters(select(Task), category, completed)
    return list(session.scalars(_ordered(statement)).all())


def get_task(session: Session, task_id: int) -> Task:
    """获取单个任务。"""
    task = session.get(Task, task_id)
    if task is None:
        raise TaskNotFound("任务不存在或已被删除")
    return task


def update_task(session: Session, task_id: int, **kwargs: Any) -> Task:
    """更新任务基础信息。"""
    task = get_task(session, task_id)

    if "title" in kwargs:
        title = (kwargs.get("title") or "").strip()
        if not title:
            raise TaskError("任务标题不能为空")
        task.title = title
    if "category" in kwargs:
        category = kwargs.get("category")
        if category not in VALID_CATEGORIES:
            raise TaskError("任务分类只能是日常或工作")
        task.category = category
    if "priority" in kwargs:
        priority = kwargs.get("priority")
        if priority not in VALID_PRIORITIES:
            raise TaskError("任务优先级只能是高、中或低")
        task.priority = priority
    if "due_date" in kwargs:
        task.due_date = _normalize_due_date(kwargs.get("due_date"))
    if "note_id" in kwargs:
        task.note_id = _validate_note(session, kwargs.get("note_id"))

    session.commit()
    session.refresh(task)
    return task


def complete_task(session: Session, task_id: int) -> Task:
    """标记完成并记录完成时间。"""
    task = get_task(session, task_id)
    if not task.completed:
        task.completed = True
        task.completed_at = datetime.now()
        session.commit()
        session.refresh(task)
    return task


def uncomplete_task(session: Session, task_id: int) -> Task:
    """取消完成并清空完成时间。"""
    task = get_task(session, task_id)
    if task.completed:
        task.completed = False
        task.completed_at = None
        session.commit()
        session.refresh(task)
    return task


def delete_task(session: Session, task_id: int) -> None:
    """删除任务。"""
    task = get_task(session, task_id)
    session.delete(task)
    session.commit()


def get_today_tasks(session: Session) -> list[Task]:
    """获取今天到期或已过期且未完成的任务。"""
    today_end = datetime.now().replace(hour=23, minute=59, second=59, microsecond=999999)
    statement = select(Task).where(
        Task.completed.is_(False),
        Task.due_date.is_not(None),
        Task.due_date <= today_end,
    )
    return list(session.scalars(_ordered(statement)).all())


def get_upcoming_tasks(session: Session, days: int = 7) -> list[Task]:
    """获取明天起未来若干天内到期的未完成任务。"""
    now = datetime.now()
    start = datetime(now.year, now.month, now.day) + timedelta(days=1)
    end = start + timedelta(days=days) - timedelta(microseconds=1)
    statement = select(Task).where(
        Task.completed.is_(False),
        Task.due_date >= start,
        Task.due_date <= end,
    )
    return list(session.scalars(_ordered(statement)).all())


def get_stats(session: Session) -> dict[str, int]:
    """获取任务统计。"""
    total = session.scalar(select(func.count(Task.id))) or 0
    completed = session.scalar(
        select(func.count(Task.id)).where(Task.completed.is_(True))
    ) or 0
    return {
        "total_tasks": total,
        "completed_tasks": completed,
        "incomplete_tasks": total - completed,
        "today_tasks": len(get_today_tasks(session)),
        "upcoming_tasks": len(get_upcoming_tasks(session)),
    }


def serialize_task(task: Task) -> dict[str, Any]:
    """任务转前端字典。"""
    current_session = object_session(task)

    def read_note_title() -> str | None:
        """优先复用当前会话；脱离会话时临时打开一个连接。"""
        if task.note_id is None:
            return None
        if current_session is not None:
            note = current_session.get(Note, task.note_id)
            return note.title if note is not None else None

        from services.db import SessionLocal

        with SessionLocal() as db:
            note = db.get(Note, task.note_id)
            return note.title if note is not None else None

    note_title = read_note_title()
    is_overdue = bool(
        not task.completed
        and task.due_date is not None
        and task.due_date < datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    )

    return {
        "id": task.id,
        "title": task.title,
        "category": task.category,
        "priority": task.priority,
        "due_date": task.due_date.strftime("%Y-%m-%d") if task.due_date else None,
        "completed": task.completed,
        "note_id": task.note_id,
        "note_title": note_title,
        "is_overdue": is_overdue,
        "created_at": task.created_at.isoformat(timespec="seconds") if task.created_at else None,
        "completed_at": task.completed_at.isoformat(timespec="seconds") if task.completed_at else None,
    }
