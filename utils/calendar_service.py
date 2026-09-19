# -*- coding: utf-8 -*-
"""
日历业务服务。

只负责从 plans 表查询并整理日历页需要的数据：月历矩阵、
按月分组、日视图全天/定时拆分。不在本模块缓存任何计划数据，
每次渲染都直接查库，保证计划页改完日历立刻是最新的。
"""

from __future__ import annotations

import calendar as _calendar
from datetime import date
from typing import Any

from sqlalchemy.orm import selectinload

from models.models import Plan
from utils.plan_service import PLAN_CATEGORIES

# 周一为一周起始，与旧 HTML 日历保持一致
_WEEKDAY_LABELS = ("一", "二", "三", "四", "五", "六", "日")


def weekday_label(day: date) -> str:
    """返回某天的中文星期，如“星期六”。"""
    # Python 的 weekday()：周一为 0，正好对应 _WEEKDAY_LABELS
    return "星期" + _WEEKDAY_LABELS[day.weekday()]


def month_cells(year: int, month: int) -> list[date | None]:
    """
    返回某月的月历序列（周一为起始），前置空位用 None 补齐。
    长度为 7 的整数倍，页面侧每 7 个切一行；末尾不补空位。
    """
    first_weekday = date(year, month, 1).weekday()  # 周一为 0
    days_in_month = _calendar.monthrange(year, month)[1]
    cells: list[date | None] = [None] * first_weekday
    for day_num in range(1, days_in_month + 1):
        cells.append(date(year, month, day_num))
    return cells


def list_date_plans(
    session: Any,
    start: date,
    end: date,
    category: str | None = None,
) -> list[Plan]:
    """
    查询日期落在 [start, end] 闭区间内的计划。
    date 为空的“暂未排期”计划不进日历；category 为 None 或“全部”时不过滤。
    """
    query = (
        session.query(Plan)
        .options(selectinload(Plan.subtasks))
        .filter(Plan.date.isnot(None), Plan.date >= start, Plan.date <= end)
    )
    if category and category != "全部":
        query = query.filter(Plan.category == category)
    return list(query)


def group_by_day(plans: list[Plan]) -> dict[date, list[Plan]]:
    """把计划按日期分组：{date: [plan, ...]}。"""
    result: dict[date, list[Plan]] = {}
    for plan in plans:
        result.setdefault(plan.date, []).append(plan)
    return result


def month_cell_sort(plans: list[Plan]) -> list[Plan]:
    """
    月格子里的排序：定时计划在前（按开始时间升序），全天计划在后；
    再按创建时间、ID 兜底。对齐旧 HTML 的 calendarDaySort。
    """
    return sorted(
        plans,
        key=lambda p: (
            0 if p.start_time else 1,
            p.start_time or "",
            p.created_at or 0,
            p.id,
        ),
    )


def split_day_plans(plans: list[Plan]) -> tuple[list[Plan], list[Plan]]:
    """
    日视图拆分：返回（全天列表，定时列表）。
    全天计划保持入参顺序，定时计划按开始时间升序、创建时间兜底。
    """
    all_day = [p for p in plans if not p.start_time]
    timed = sorted(
        [p for p in plans if p.start_time],
        key=lambda p: (p.start_time, p.created_at or 0, p.id),
    )
    return all_day, timed
