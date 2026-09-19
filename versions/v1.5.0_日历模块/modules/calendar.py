# -*- coding: utf-8 -*-
"""
日历页面。

月视图用 7 列格子手绘月历（周一为起始），格子里显示当天最多 2 条计划；
点日期数字进入日视图：上方是全天计划，下方是按开始时间排序的定时时间线。
计划数据只查 plans 表，不做页面缓存。编辑/新增统一跳转到计划页完成。
"""

from datetime import date, timedelta
from textwrap import shorten

import streamlit as st

from models.models import Plan
from utils.db import SessionLocal
from utils import calendar_service as cal
from utils import plan_service as plans_service

# 月格子内单条计划标题的最大显示宽度
_CELL_TEXT_WIDTH = 12


def _init_state() -> None:
    """初始化为日历时需要的界面状态。"""
    today = date.today()
    st.session_state.setdefault("cal_year", today.year)
    st.session_state.setdefault("cal_month", today.month)
    st.session_state.setdefault("cal_selected_date", None)
    st.session_state.setdefault("cal_category", "全部")


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    """月份前移/后移，自动处理跨年。delta 取 1 或 -1。"""
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def _goto_plan_edit(plan_id: int) -> None:
    """跳到计划页并直接打开某条计划的编辑表单。"""
    st.session_state.plan_form_mode = "edit"
    st.session_state.plan_edit_id = plan_id
    st.session_state.plan_delete_confirm_id = None
    st.session_state.app_page = "📝 计划"


def _goto_plan_new(day: date) -> None:
    """跳到计划页的新增表单，并预填所选日期。"""
    st.session_state.plan_form_mode = "new"
    st.session_state.plan_edit_id = None
    st.session_state.plan_delete_confirm_id = None
    st.session_state.plan_preset_date = day
    st.session_state.app_page = "📝 计划"


def _on_plan_status_change(plan_id: int, widget_key: str) -> None:
    """日视图里勾选完成：直接写库。"""
    with SessionLocal() as session:
        plans_service.set_plan_status(session, plan_id, bool(st.session_state[widget_key]))


def _render_toolbar() -> None:
    """月视图顶部：月份切换、回到今天、分类筛选。"""
    prev_col, title_col, next_col, today_col = st.columns([0.8, 2.2, 0.8, 1.2])
    if prev_col.button("‹", key="cal_prev", use_container_width=True):
        st.session_state.cal_year, st.session_state.cal_month = _shift_month(
            st.session_state.cal_year, st.session_state.cal_month, -1
        )
        st.rerun()
    title_col.markdown(
        f"#### {st.session_state.cal_year} 年 {st.session_state.cal_month} 月"
    )
    if next_col.button("›", key="cal_next", use_container_width=True):
        st.session_state.cal_year, st.session_state.cal_month = _shift_month(
            st.session_state.cal_year, st.session_state.cal_month, 1
        )
        st.rerun()
    if today_col.button("今天", key="cal_today", use_container_width=True):
        today = date.today()
        st.session_state.cal_year = today.year
        st.session_state.cal_month = today.month
        st.session_state.cal_selected_date = today
        st.rerun()

    options = ("全部",) + plans_service.PLAN_CATEGORIES
    st.selectbox(
        "分类筛选",
        options=options,
        format_func=lambda value: value if value == "全部" else plans_service.category_label(value),
        key="cal_category",
        label_visibility="collapsed",
    )


def _cell_plan_lines(day_plans: list[Plan]) -> list[str]:
    """把当天计划转成月格子里显示的短文本，最多 2 条，超出补“等 N 项”。"""
    ordered = cal.month_cell_sort(day_plans)
    lines: list[str] = []
    for plan in ordered[:2]:
        title = shorten(plan.title or "未命名计划", width=_CELL_TEXT_WIDTH, placeholder="…")
        if plan.start_time:
            lines.append(f"{plan.start_time} {title}")
        else:
            lines.append(title)
    if len(ordered) > 2:
        lines.append(f"等 {len(ordered)} 项")
    return lines


def _render_month_view(session) -> None:
    """渲染月视图。"""
    _render_toolbar()

    year = st.session_state.cal_year
    month = st.session_state.cal_month
    category = st.session_state.cal_category
    cells = cal.month_cells(year, month)
    # 月格子可能不足整行，补齐到 7 的倍数方便逐行渲染
    while len(cells) % 7 != 0:
        cells.append(None)

    # 一次查出整月的计划，再按日期分组
    start = date(year, month, 1)
    if month == 12:
        end = date(year, 12, 31)
    else:
        end = date(year, month + 1, 1) - timedelta(days=1)
    month_plans = cal.list_date_plans(session, start, end, category)
    plans_by_day = cal.group_by_day(month_plans)

    today = date.today()

    # 星期表头
    header_cols = st.columns(7)
    for idx, col in enumerate(header_cols):
        col.markdown(f"**{'一二三四五六日'[idx]}**")

    # 每 7 个格子一行
    for row_start in range(0, len(cells), 7):
        row = cells[row_start:row_start + 7]
        cols = st.columns(7)
        for col, day in zip(cols, row):
            if day is None:
                continue
            with col:
                # 今天用主色按钮突出，其余为普通按钮；两者都可点击进入日视图
                if st.button(
                    f"{day.day}日" if day == today else str(day.day),
                    key=f"cal_day_{day.isoformat()}",
                    type="primary" if day == today else "secondary",
                    use_container_width=True,
                ):
                    st.session_state.cal_selected_date = day
                    st.rerun()
                for line in _cell_plan_lines(plans_by_day.get(day, [])):
                    st.caption(line)


def _day_plan_row(plan: Plan, timed: bool) -> None:
    """日视图里的单条精简计划：勾选 + 时间/标题 + 编辑。"""
    check_col, time_col, edit_col = st.columns([0.8, 5, 1])
    widget_key = f"cal_plan_done_{plan.id}"
    check_col.checkbox(
        "完成",
        value=plan.status == "done",
        key=widget_key,
        on_change=_on_plan_status_change,
        args=(plan.id, widget_key),
        label_visibility="collapsed",
    )

    title = plan.title or "未命名计划"
    if plan.status == "done":
        title = f"~~{title}~~"
    if timed:
        time_text = f"**{plan.start_time}**"
        if plan.end_time and plan.end_time != plan.start_time:
            time_text += f"–{plan.end_time}"
        time_col.markdown(f"{time_text}　{title}")
    else:
        time_col.markdown(title)
    st.caption(plans_service.category_label(plan.category))

    # 用 on_click 回调跳转：回调在新一轮脚本、侧边栏导航重建前执行，
    # 那时修改其绑定的 app_page 才合法（页面渲染中直接改会报错）
    edit_col.button(
        "编辑",
        key=f"cal_plan_edit_{plan.id}",
        on_click=_goto_plan_edit,
        args=(plan.id,),
        use_container_width=True,
    )


def _render_day_view(session, day: date) -> None:
    """渲染日视图：全天区在上，定时时间线在下。"""
    back_col, title_col, add_col = st.columns([1, 3, 1.2])
    if back_col.button("← 返回月视图", key="cal_back_month", use_container_width=True):
        st.session_state.cal_selected_date = None
        st.rerun()
    title_col.markdown(f"#### 📌 {day.isoformat()}（{cal.weekday_label(day)}）")
    add_col.button(
        "＋ 添加计划",
        type="primary",
        key="cal_add_plan",
        on_click=_goto_plan_new,
        args=(day,),
        use_container_width=True,
    )

    category = st.session_state.cal_category
    day_plans = cal.list_date_plans(session, day, day, category)
    all_day, timed = cal.split_day_plans(day_plans)

    if not day_plans:
        st.info("当天暂无计划，点击右上角「添加计划」开始安排。")
        return

    if all_day:
        st.markdown(f"**☀️ 全天（{len(all_day)}）**")
        for plan in all_day:
            with st.container(border=True):
                _day_plan_row(plan, timed=False)

    if timed:
        st.markdown(f"**🕐 定时（{len(timed)}）**")
        for plan in timed:
            with st.container(border=True):
                _day_plan_row(plan, timed=True)


def show() -> None:
    """渲染日历页面。"""
    _init_state()
    st.title("📅 日历")

    with SessionLocal() as session:
        selected = st.session_state.cal_selected_date
        if selected is None:
            _render_month_view(session)
        else:
            _render_day_view(session, selected)
