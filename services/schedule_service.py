# -*- coding: utf-8 -*-
"""日程计划管理服务。"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, object_session

from models.models import Note, Schedule

VALID_TYPES = {"日常", "旅行", "工作", "其他"}
TYPE_COLORS = {
    "日常": "#06b6d4",
    "旅行": "#8b5cf6",
    "工作": "#3b82f6",
    "其他": "#64748b",
}
COLOR_PATTERN = re.compile(r"^#[0-9a-fA-F]{6}$")


class ScheduleError(Exception):
    """日程参数错误。"""


class ScheduleNotFound(Exception):
    """日程不存在。"""


def parse_date(value: Any, field_name: str = "日期") -> date:
    """解析 YYYY-MM-DD 日期。"""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value).strip(), "%Y-%m-%d").date()
    except ValueError as exc:
        raise ScheduleError(f"{field_name}格式不正确") from exc


def parse_datetime(value: Any, field_name: str = "时间", required: bool = True) -> datetime | None:
    """解析 datetime-local 或 ISO 时间。"""
    if value in (None, ""):
        if required:
            raise ScheduleError(f"{field_name}不能为空")
        return None
    if isinstance(value, datetime):
        return value
    raw = str(value).strip()
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ScheduleError(f"{field_name}格式不正确") from exc


def validate_note(session: Session, note_id: Any) -> int | None:
    """校验关联笔记存在；空值表示不关联。"""
    if note_id in (None, ""):
        return None
    try:
        note_id = int(note_id)
    except (TypeError, ValueError) as exc:
        raise ScheduleError("关联笔记 ID 不正确") from exc
    if session.get(Note, note_id) is None:
        raise ScheduleError("关联笔记不存在")
    return note_id


def validate_color(color: Any) -> str:
    """校验十六进制颜色。"""
    color = str(color or "").strip()
    if not COLOR_PATTERN.fullmatch(color):
        raise ScheduleError("颜色必须是 #RRGGBB 格式")
    return color


def validate_common(
    title: Any,
    schedule_type: Any,
    start_time: Any,
    end_time: Any,
    color: Any,
    session: Session,
    note_id: Any = None,
) -> tuple[str, str, datetime, datetime | None, str, int | None]:
    """校验日程通用字段。"""
    title = str(title or "").strip()
    if not title:
        raise ScheduleError("日程标题不能为空")
    schedule_type = str(schedule_type or "日常").strip()
    if schedule_type not in VALID_TYPES:
        raise ScheduleError("日程类型只能是日常、旅行、工作或其他")

    start = parse_datetime(start_time, "开始时间", required=True)
    end = parse_datetime(end_time, "结束时间", required=False)
    if end is not None and end <= start:
        raise ScheduleError("结束时间必须晚于开始时间")

    return (
        title,
        schedule_type,
        start,
        end,
        validate_color(color or TYPE_COLORS[schedule_type]),
        validate_note(session, note_id),
    )


def day_bounds(day: date) -> tuple[datetime, datetime]:
    """返回某一天的开始和结束时间。"""
    start = datetime.combine(day, datetime.min.time())
    return start, start + timedelta(days=1) - timedelta(microseconds=1)


def create_schedule(
    session: Session,
    title: str,
    schedule_type: str = "日常",
    start_time: Any = None,
    end_time: Any = None,
    location: str | None = None,
    description: str | None = None,
    note_id: Any = None,
    color: str | None = None,
) -> Schedule:
    """创建日程。"""
    title, schedule_type, start, end, color, note_id = validate_common(
        title, schedule_type, start_time, end_time, color, session, note_id
    )
    schedule = Schedule(
        title=title,
        schedule_type=schedule_type,
        start_time=start,
        end_time=end,
        location=str(location or "").strip() or None,
        description=str(description or "").strip() or None,
        note_id=note_id,
        color=color,
        created_at=datetime.now(),
    )
    session.add(schedule)
    session.commit()
    session.refresh(schedule)
    return schedule


def list_schedules(session: Session, start_date: Any, end_date: Any) -> list[Schedule]:
    """查询日期范围内与时间范围重叠的日程。"""
    start_day = parse_date(start_date, "开始日期")
    end_day = parse_date(end_date, "结束日期")
    if end_day < start_day:
        raise ScheduleError("结束日期不能早于开始日期")
    range_start, _ = day_bounds(start_day)
    _, range_end = day_bounds(end_day)
    statement = select(Schedule)
    # 使用 SQLAlchemy func 标准 COALESCE 函数，空结束时间按开始时间处理。
    statement = statement.where(
        Schedule.start_time <= range_end,
        func.coalesce(Schedule.end_time, Schedule.start_time) >= range_start,
    ).order_by(Schedule.start_time.asc(), Schedule.id.asc())
    return list(session.scalars(statement).all())


def get_schedule(session: Session, schedule_id: int) -> Schedule:
    """获取单个日程。"""
    schedule = session.get(Schedule, schedule_id)
    if schedule is None:
        raise ScheduleNotFound("日程不存在或已被删除")
    return schedule


def update_schedule(session: Session, schedule_id: int, **kwargs: Any) -> Schedule:
    """更新日程；空字符串的结束时间表示清空。"""
    schedule = get_schedule(session, schedule_id)

    if "title" in kwargs:
        title = str(kwargs.get("title") or "").strip()
        if not title:
            raise ScheduleError("日程标题不能为空")
        schedule.title = title
    if "schedule_type" in kwargs:
        schedule_type = str(kwargs.get("schedule_type") or "").strip()
        if schedule_type not in VALID_TYPES:
            raise ScheduleError("日程类型只能是日常、旅行、工作或其他")
        schedule.schedule_type = schedule_type
    if "start_time" in kwargs:
        schedule.start_time = parse_datetime(kwargs.get("start_time"), "开始时间", True)
    if "end_time" in kwargs:
        schedule.end_time = parse_datetime(kwargs.get("end_time"), "结束时间", False)
    if "location" in kwargs:
        schedule.location = str(kwargs.get("location") or "").strip() or None
    if "description" in kwargs:
        schedule.description = str(kwargs.get("description") or "").strip() or None
    if "note_id" in kwargs:
        schedule.note_id = validate_note(session, kwargs.get("note_id"))
    if "color" in kwargs:
        schedule.color = validate_color(kwargs.get("color"))

    if schedule.end_time is not None and schedule.end_time <= schedule.start_time:
        raise ScheduleError("结束时间必须晚于开始时间")

    session.commit()
    session.refresh(schedule)
    return schedule


def delete_schedule(session: Session, schedule_id: int) -> None:
    """删除日程。"""
    schedule = get_schedule(session, schedule_id)
    session.delete(schedule)
    session.commit()


def get_today_schedules(session: Session) -> list[Schedule]:
    """获取今天的日程。"""
    return list_schedules(session, date.today(), date.today())


def get_month_schedules(session: Session, year: int, month: int) -> list[Schedule]:
    """获取某个月的全部日程。"""
    try:
        first_day = date(int(year), int(month), 1)
    except (TypeError, ValueError) as exc:
        raise ScheduleError("年份或月份不正确") from exc
    if first_day.month == 12:
        last_day = date(first_day.year + 1, 1, 1) - timedelta(days=1)
    else:
        last_day = date(first_day.year, first_day.month + 1, 1) - timedelta(days=1)
    return list_schedules(session, first_day, last_day)


def get_today_count(session: Session) -> int:
    """获取今日日程数量。"""
    return len(get_today_schedules(session))


def serialize_schedule(schedule: Schedule) -> dict[str, Any]:
    """日程转前端字典。"""
    current_session = object_session(schedule)
    note_title = None
    if schedule.note_id is not None:
        if current_session is not None:
            note = current_session.get(Note, schedule.note_id)
            note_title = note.title if note is not None else None
        else:
            from services.db import SessionLocal

            with SessionLocal() as session:
                note = session.get(Note, schedule.note_id)
                note_title = note.title if note is not None else None

    return {
        "id": schedule.id,
        "title": schedule.title,
        "schedule_type": schedule.schedule_type,
        "start_time": schedule.start_time.isoformat(timespec="minutes") if schedule.start_time else None,
        "end_time": schedule.end_time.isoformat(timespec="minutes") if schedule.end_time else None,
        "location": schedule.location,
        "description": schedule.description,
        "note_id": schedule.note_id,
        "note_title": note_title,
        "color": schedule.color,
        "effective_color": schedule.color if COLOR_PATTERN.fullmatch(schedule.color or "") else TYPE_COLORS[schedule.schedule_type],
        "created_at": schedule.created_at.isoformat(timespec="seconds") if schedule.created_at else None,
    }
