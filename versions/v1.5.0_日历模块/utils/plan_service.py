# -*- coding: utf-8 -*-
"""
计划模块业务服务。

Streamlit 页面只负责交互和展示；数据库增删改查、排序、校验、
软删除快照等逻辑集中放在这里，便于后续给今日页和日历页复用。
"""

from __future__ import annotations

import json
from datetime import date, datetime, time
from typing import Any

from sqlalchemy import case, func
from sqlalchemy.orm import selectinload

from models.models import Plan, Subtask, TrashItem
from utils.db import SessionLocal

# 计划页固定筛选分类；新建表单额外保留“默认”，用于兼容旧数据
PLAN_CATEGORIES = ("日程安排", "学习", "旅行", "会议", "工作", "生活")
FORM_CATEGORIES = PLAN_CATEGORIES + ("默认",)

CATEGORY_ICONS = {
    "日程安排": "🗓",
    "学习": "📚",
    "旅行": "✈️",
    "会议": "📋",
    "工作": "💼",
    "生活": "🏠",
    "默认": "📌",
}


class PlanValidationError(ValueError):
    """计划表单数据不合法。"""


def category_icon(category: str | None) -> str:
    """返回分类图标；未知分类使用普通别针图标。"""
    return CATEGORY_ICONS.get(category or "默认", "📌")


def category_label(category: str | None) -> str:
    """返回带图标的分类名称。"""
    category = category or "默认"
    return f"{category_icon(category)} {category}"


def time_to_str(value: time | None) -> str | None:
    """把 time_input 返回的时间对象转换成 HH:MM 字符串。"""
    if value is None:
        return None
    return value.strftime("%H:%M")


def parse_time_str(value: str | None) -> time | None:
    """把数据库中的 HH:MM 字符串转回 time_input 需要的 time 对象。"""
    if not value:
        return None
    return datetime.strptime(value, "%H:%M").time()


def validate_time_range(start_time: str | None, end_time: str | None) -> None:
    """校验可选时间范围；时间相等允许，跨天暂不支持。"""
    if end_time and not start_time:
        raise PlanValidationError("填写结束时间前，请先填写开始时间。")
    if start_time and end_time and end_time < start_time:
        raise PlanValidationError("结束时间不能早于开始时间。")


def format_time_range(plan: Plan) -> str:
    """按 HTML 旧版格式显示计划时间。"""
    if not plan.start_time:
        return ""
    if plan.end_time:
        return f"🕐 {plan.start_time} — {plan.end_time}"
    return f"🕐 {plan.start_time}"


def list_plans(session: Any, category: str | None = None) -> list[Plan]:
    """
    查询计划。

    排序：未完成在前、已完成在后；同状态下有日期在前、无日期在后，
    再按日期、创建时间、ID 稳定排序。
    """
    query = session.query(Plan).options(selectinload(Plan.subtasks))
    if category and category != "全部":
        query = query.filter(Plan.category == category)

    done_order = case((Plan.status == "done", 1), else_=0)
    no_date_order = case((Plan.date.is_(None), 1), else_=0)
    return (
        query.order_by(
            done_order,
            no_date_order,
            Plan.date,
            Plan.created_at,
            Plan.id,
        )
        .all()
    )


def plan_stats(plans: list[Plan]) -> dict[str, int]:
    """统计当前列表的计划总数、完成数和完成率。"""
    total = len(plans)
    done = sum(1 for plan in plans if plan.status == "done")
    rate = round(done / total * 100) if total else 0
    return {"total": total, "done": done, "rate": rate}


def create_plan(
    session: Any,
    title: str,
    plan_date: date | None,
    start_time: str | None,
    end_time: str | None,
    category: str,
    description: str = "",
) -> Plan:
    """新建计划。"""
    title = (title or "").strip()
    description = (description or "").strip()
    category = category or "默认"
    if not title:
        raise PlanValidationError("请填写标题。")
    validate_time_range(start_time, end_time)

    plan = Plan(
        title=title,
        description=description or None,
        date=plan_date,
        start_time=start_time,
        end_time=end_time,
        category=category,
        status="pending",
    )
    session.add(plan)
    session.commit()
    session.refresh(plan)
    return plan


def update_plan(
    session: Any,
    plan_id: int,
    title: str,
    plan_date: date | None,
    start_time: str | None,
    end_time: str | None,
    category: str,
    description: str = "",
) -> Plan:
    """更新计划的基本信息，不处理子任务勾选状态。"""
    plan = session.get(Plan, plan_id)
    if plan is None:
        raise PlanValidationError("要编辑的计划不存在，可能已被删除。")

    title = (title or "").strip()
    description = (description or "").strip()
    category = category or "默认"
    if not title:
        raise PlanValidationError("请填写标题。")
    validate_time_range(start_time, end_time)

    plan.title = title
    plan.description = description or None
    plan.date = plan_date
    plan.start_time = start_time
    plan.end_time = end_time
    plan.category = category
    plan.updated_at = datetime.now()
    session.commit()
    session.refresh(plan)
    return plan


def set_plan_status(session: Any, plan_id: int, done: bool) -> None:
    """在 pending / done 之间切换计划状态。"""
    plan = session.get(Plan, plan_id)
    if plan is None:
        return
    plan.status = "done" if done else "pending"
    plan.updated_at = datetime.now()
    session.commit()


def add_subtask(session: Any, plan_id: int, text: str) -> None:
    """给计划追加子任务，order 自动排在最后。"""
    text = (text or "").strip()
    if not text:
        raise PlanValidationError("请输入子任务内容。")
    if session.get(Plan, plan_id) is None:
        raise PlanValidationError("所属计划不存在。")

    max_order = (
        session.query(func.coalesce(func.max(Subtask.order), 0))
        .filter(Subtask.plan_id == plan_id)
        .scalar()
    )
    subtask = Subtask(
        plan_id=plan_id,
        text=text,
        done=False,
        order=(max_order or 0) + 1,
        note_ids="[]",
        link_ids="[]",
        doc_ids="[]",
    )
    session.add(subtask)
    session.commit()


def set_subtask_done(session: Any, subtask_id: int, done: bool) -> None:
    """切换子任务完成状态。"""
    subtask = session.get(Subtask, subtask_id)
    if subtask is None:
        return
    subtask.done = done
    session.commit()


def delete_subtask(session: Any, subtask_id: int) -> None:
    """删除一条子任务。"""
    subtask = session.get(Subtask, subtask_id)
    if subtask is not None:
        session.delete(subtask)
        session.commit()


def _datetime_to_text(value: datetime | None) -> str | None:
    """JSON 快照中的日期时间序列化。"""
    return value.isoformat(timespec="seconds") if value else None


def _plan_snapshot(plan: Plan) -> dict[str, Any]:
    """把计划和子任务序列化成以后可还原的 JSON 快照结构。"""
    return {
        "schema_version": 1,
        "plan": {
            "id": plan.id,
            "title": plan.title,
            "description": plan.description,
            "date": plan.date.isoformat() if plan.date else None,
            "start_time": plan.start_time,
            "end_time": plan.end_time,
            "category": plan.category,
            "status": plan.status,
            "created_at": _datetime_to_text(plan.created_at),
            "updated_at": _datetime_to_text(plan.updated_at),
        },
        "subtasks": [
            {
                "id": subtask.id,
                "plan_id": subtask.plan_id,
                "text": subtask.text,
                "done": bool(subtask.done),
                "order": subtask.order,
                "note_ids": subtask.note_ids,
                "link_ids": subtask.link_ids,
                "doc_ids": subtask.doc_ids,
                "created_at": _datetime_to_text(subtask.created_at),
            }
            for subtask in plan.subtasks
        ],
    }


def soft_delete_plan(session: Any, plan_id: int) -> None:
    """
    软删除计划。

    先把计划和子任务写入 trash_items 的 JSON 快照，再删除计划表记录；
    subtasks 通过外键级联删除。
    """
    plan = (
        session.query(Plan)
        .options(selectinload(Plan.subtasks))
        .filter(Plan.id == plan_id)
        .one_or_none()
    )
    if plan is None:
        raise PlanValidationError("要删除的计划不存在，可能已经删除。")

    snapshot = _plan_snapshot(plan)
    trash_item = TrashItem(
        item_type="plan",
        item_id=str(plan.id),
        snapshot=json.dumps(snapshot, ensure_ascii=False),
    )
    session.add(trash_item)
    session.delete(plan)
    session.commit()