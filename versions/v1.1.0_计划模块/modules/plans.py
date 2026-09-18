# -*- coding: utf-8 -*-
"""
计划页面。

提供计划增删改查、完成状态切换、子任务维护、分类筛选和软删除。
页面只处理 Streamlit 交互；具体数据库操作放在 utils.plan_service。
"""

from datetime import date

import streamlit as st

from models.models import Plan
from utils.db import SessionLocal
from utils import plan_service as service


def _init_state() -> None:
    """初始化计划页需要的临时界面状态。"""
    st.session_state.setdefault("plan_form_mode", None)
    st.session_state.setdefault("plan_edit_id", None)
    st.session_state.setdefault("plan_delete_confirm_id", None)


def _close_form() -> None:
    """退出新增/编辑表单。"""
    st.session_state.plan_form_mode = None
    st.session_state.plan_edit_id = None


def _on_plan_status_change(plan_id: int, widget_key: str) -> None:
    """checkbox 回调：切换计划完成状态。"""
    with SessionLocal() as session:
        service.set_plan_status(session, plan_id, bool(st.session_state[widget_key]))


def _on_subtask_done_change(subtask_id: int, widget_key: str) -> None:
    """checkbox 回调：切换子任务完成状态。"""
    with SessionLocal() as session:
        service.set_subtask_done(session, subtask_id, bool(st.session_state[widget_key]))


def _plan_form(session, plan: Plan | None) -> None:
    """渲染新增或编辑计划表单。"""
    is_edit = plan is not None
    with st.container(border=True):
        with st.form(f"plan_form_{'edit_' + str(plan.id) if is_edit else 'new'}"):
            st.subheader("编辑计划" if is_edit else "新增计划")
            title = st.text_input(
                "标题 *",
                value=plan.title if is_edit else "",
                placeholder="请输入计划标题",
            )
            description = st.text_area(
                "描述（选填）",
                value=plan.description if is_edit and plan.description else "",
                height=90,
            )
            plan_date = st.date_input(
                "日期",
                value=plan.date if is_edit and plan.date else date.today(),
            )

            start_col, end_col = st.columns(2)
            with start_col:
                # value=None 时控件保持空值，表示这是一个全天计划
                start_time_value = st.time_input(
                    "开始时间（可选）",
                    value=service.parse_time_str(plan.start_time) if is_edit else None,
                    step=900,
                )
            with end_col:
                end_time_value = st.time_input(
                    "结束时间（可选）",
                    value=service.parse_time_str(plan.end_time) if is_edit else None,
                    step=900,
                )

            category_index = 0
            if is_edit and plan.category in service.FORM_CATEGORIES:
                category_index = service.FORM_CATEGORIES.index(plan.category)
            category = st.selectbox(
                "分类",
                options=service.FORM_CATEGORIES,
                index=category_index,
                format_func=service.category_label,
            )

            save_col, cancel_col = st.columns(2)
            save_clicked = save_col.form_submit_button("💾 保存", type="primary", use_container_width=True)
            cancel_clicked = cancel_col.form_submit_button("取消", use_container_width=True)

    if cancel_clicked:
        _close_form()
        st.rerun()

    if save_clicked:
        start_text = service.time_to_str(start_time_value)
        end_text = service.time_to_str(end_time_value)
        try:
            if is_edit:
                service.update_plan(
                    session,
                    plan.id,
                    title=title,
                    plan_date=plan_date,
                    start_time=start_text,
                    end_time=end_text,
                    category=category,
                    description=description,
                )
                st.toast("计划已更新")
            else:
                service.create_plan(
                    session,
                    title=title,
                    plan_date=plan_date,
                    start_time=start_text,
                    end_time=end_text,
                    category=category,
                    description=description,
                )
                st.toast("计划已添加")
            _close_form()
            st.rerun()
        except service.PlanValidationError as exc:
            st.error(str(exc))


def _subtask_panel(session, plan: Plan) -> None:
    """渲染单个计划的子任务列表和新增输入。"""
    done_count = sum(1 for subtask in plan.subtasks if subtask.done)
    with st.expander(f"子任务（{done_count}/{len(plan.subtasks)}）", expanded=False):
        if not plan.subtasks:
            st.caption("暂无子任务")

        for subtask in plan.subtasks:
            check_col, text_col, delete_col = st.columns([1, 5, 1])
            check_key = f"subtask_done_{subtask.id}"
            check_col.checkbox(
                "完成",
                value=bool(subtask.done),
                key=check_key,
                on_change=_on_subtask_done_change,
                args=(subtask.id, check_key),
                label_visibility="collapsed",
            )
            if subtask.done:
                text_col.markdown(f"~~{subtask.text}~~")
            else:
                text_col.write(subtask.text)
            if delete_col.button("删除", key=f"subtask_delete_{subtask.id}", use_container_width=True):
                service.delete_subtask(session, subtask.id)
                st.toast("子任务已删除")
                st.rerun()

        st.divider()
        # 独立表单提交后自动清空输入，不会残留待添加文本
        with st.form(f"subtask_add_form_{plan.id}", clear_on_submit=True):
            input_col, button_col = st.columns([4, 1])
            new_text = input_col.text_input(
                "新增子任务",
                label_visibility="collapsed",
                placeholder="输入子任务内容",
            )
            submitted = button_col.form_submit_button("＋ 添加子任务", use_container_width=True)
        if submitted:
            try:
                service.add_subtask(session, plan.id, new_text)
                st.toast("子任务已添加")
                st.rerun()
            except service.PlanValidationError as exc:
                st.error(str(exc))


def _plan_card(session, plan: Plan) -> None:
    """渲染单条计划。"""
    with st.container(border=True):
        check_col, title_col, edit_col, delete_col = st.columns([0.7, 5, 1, 1])
        check_key = f"plan_done_{plan.id}"
        check_col.checkbox(
            "完成",
            value=plan.status == "done",
            key=check_key,
            on_change=_on_plan_status_change,
            args=(plan.id, check_key),
            label_visibility="collapsed",
        )

        if plan.status == "done":
            title_col.markdown(f"**~~{plan.title}~~**")
        else:
            title_col.markdown(f"**{plan.title}**")

        if edit_col.button("编辑", key=f"plan_edit_{plan.id}", use_container_width=True):
            st.session_state.plan_form_mode = "edit"
            st.session_state.plan_edit_id = plan.id
            st.session_state.plan_delete_confirm_id = None
            st.rerun()
        if delete_col.button("删除", key=f"plan_delete_{plan.id}", use_container_width=True):
            st.session_state.plan_delete_confirm_id = plan.id
            st.rerun()

        meta_parts = [
            f"📅 {plan.date.isoformat() if plan.date else '未排期'}",
            service.category_label(plan.category),
            "✅ 已完成" if plan.status == "done" else "🕒 未完成",
        ]
        time_text = service.format_time_range(plan)
        if time_text:
            meta_parts.append(time_text)
        st.caption("　｜　".join(meta_parts))

        if plan.description:
            st.write(plan.description)

        _subtask_panel(session, plan)

        if st.session_state.plan_delete_confirm_id == plan.id:
            st.warning("确定删除该计划及其子任务？删除后会进入回收站。")
            confirm_col, cancel_col = st.columns(2)
            if confirm_col.button("确认删除", key=f"plan_delete_confirm_{plan.id}", type="primary", use_container_width=True):
                service.soft_delete_plan(session, plan.id)
                st.session_state.plan_delete_confirm_id = None
                _close_form()
                st.toast("已删除")
                st.rerun()
            if cancel_col.button("取消", key=f"plan_delete_cancel_{plan.id}", use_container_width=True):
                st.session_state.plan_delete_confirm_id = None
                st.rerun()


def show() -> None:
    """渲染计划页面。"""
    _init_state()
    st.title("📝 计划")

    action_col, filter_col = st.columns([1, 2])
    if action_col.button("＋ 新增计划", type="primary", use_container_width=True):
        st.session_state.plan_form_mode = "new"
        st.session_state.plan_edit_id = None
        st.session_state.plan_delete_confirm_id = None
        st.rerun()

    filter_options = ("全部",) + service.PLAN_CATEGORIES
    category = filter_col.selectbox(
        "分类筛选",
        options=filter_options,
        format_func=lambda value: value if value == "全部" else service.category_label(value),
        label_visibility="collapsed",
    )

    with SessionLocal() as session:
        plans = service.list_plans(session, category)
        stats = service.plan_stats(plans)

        total_col, done_col, rate_col = st.columns(3)
        total_col.metric("计划总数", stats["total"])
        done_col.metric("已完成", stats["done"])
        rate_col.metric("完成率", f"{stats['rate']}%")
        st.divider()

        editing_plan = None
        if st.session_state.plan_form_mode == "edit" and st.session_state.plan_edit_id:
            editing_plan = session.get(Plan, st.session_state.plan_edit_id)
            if editing_plan is None:
                _close_form()
                st.rerun()
        if st.session_state.plan_form_mode in ("new", "edit"):
            _plan_form(session, editing_plan)
            st.divider()

        if not plans:
            empty_text = (
                f"「{category}」分类下暂无计划，点击「新增计划」创建。"
                if category != "全部"
                else "还没有计划，点击「新增计划」开始吧。"
            )
            st.info(empty_text)
        else:
            for plan in plans:
                _plan_card(session, plan)