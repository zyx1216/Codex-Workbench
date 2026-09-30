# -*- coding: utf-8 -*-
"""
首页数据看板数据聚合（全部走 SQLAlchemy ORM）。

- get_summary：4 张统计卡的当前值与统一周环比
- get_trend：最近 N 天每天新增笔记数量（供原生 Canvas 折线图）
- latest_auto_backup：最近一次启动自动备份时间（设置页展示）
周以周一 0 点为起点。
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any

from sqlalchemy import func, select

from models.models import Note, PendingItem, Schedule, Task
from services import schedule_service, task_service


def _week_bounds(now: datetime) -> tuple[datetime, datetime]:
    """返回“本周一0点、上周一0点”。"""
    today = now.date()
    this_monday = datetime.combine(today - timedelta(days=today.weekday()), time.min)
    return this_monday, this_monday - timedelta(days=7)


def _count_created(session: Any, model: Any, start: datetime,
                   end: datetime | None = None) -> int:
    """统计 created_at 在 [start, end) 区间内的记录数；end 为空表示不设上界。"""
    stmt = select(func.count()).select_from(model).where(model.created_at >= start)
    if end is not None:
        stmt = stmt.where(model.created_at < end)
    return int(session.scalar(stmt) or 0)


def _delta(current: int, previous: int) -> float | int | None:
    """统一周环比百分比；上周为 0 时：本周>0 返回 None（前端显示“新增”），均为 0 返回 0。"""
    if previous == 0:
        return None if current > 0 else 0
    return round((current - previous) / previous * 100, 1)


def get_trend(session: Any, days: int = 7) -> list[dict[str, int]]:
    """最近 days 天每天新增笔记数量，返回 [{date:"MM/DD", count:n}]。"""
    now = datetime.now()
    start_date = now.date() - timedelta(days=days - 1)
    start_dt = datetime.combine(start_date, time.min)
    # 一次取窗口内全部创建时间，本地按天分组（本地数据量小）
    created_rows = session.scalars(
        select(Note.created_at).where(Note.created_at >= start_dt)
    ).all()
    counts = {start_date + timedelta(days=i): 0 for i in range(days)}
    for created in created_rows:
        day = created.date()
        if day in counts:
            counts[day] += 1
    return [
        {"date": day.strftime("%m/%d"), "count": count}
        for day, count in sorted(counts.items())
    ]


def get_summary(session: Any) -> dict[str, Any]:
    """4 张统计卡数据：notes/tasks/schedules/pending，统一 {value, delta_percent, sub}。"""
    now = datetime.now()
    today_start = datetime.combine(now.date(), time.min)
    this_monday, last_monday = _week_bounds(now)

    # 笔记：总数 + 本周/上周新建
    notes_total = int(session.scalar(select(func.count()).select_from(Note)) or 0)
    notes_week = _count_created(session, Note, this_monday)
    notes_last = _count_created(session, Note, last_monday, this_monday)

    # 待处理：当前 pending 数 + 本周/上周新建
    pending_total = int(session.scalar(
        select(func.count()).select_from(PendingItem)
        .where(PendingItem.status == "pending")
    ) or 0)
    pending_week = _count_created(session, PendingItem, this_monday)
    pending_last = _count_created(session, PendingItem, last_monday, this_monday)

    # 任务：今日待办（今日到期+逾期未完成）+ 今日已完成 + 本周/上周新建
    today_tasks = len(task_service.get_today_tasks(session))
    today_done = int(session.scalar(
        select(func.count()).select_from(Task).where(Task.completed_at >= today_start)
    ) or 0)
    tasks_week = _count_created(session, Task, this_monday)
    tasks_last = _count_created(session, Task, last_monday, this_monday)

    # 日程：今日数量 + 本周/上周新建
    today_schedules = schedule_service.get_today_count(session)
    schedules_week = _count_created(session, Schedule, this_monday)
    schedules_last = _count_created(session, Schedule, last_monday, this_monday)

    return {
        "notes": {"value": notes_total, "delta_percent": _delta(notes_week, notes_last)},
        "pending": {"value": pending_total, "delta_percent": _delta(pending_week, pending_last)},
        "tasks": {
            "value": today_tasks,
            "delta_percent": _delta(tasks_week, tasks_last),
            "sub": today_done,
        },
        "schedules": {
            "value": today_schedules,
            "delta_percent": _delta(schedules_week, schedules_last),
        },
        "auto_backup": latest_auto_backup(),
    }


def latest_auto_backup() -> str | None:
    """最近一次启动自动备份的修改时间（ISO 字符串）；无备份返回 None。"""
    import config
    files = [p for p in config.AUTO_BACKUP_DIR.glob("auto_*.db") if p.is_file()]
    if not files:
        return None
    latest = max(files, key=lambda p: p.stat().st_mtime)
    return datetime.fromtimestamp(latest.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
