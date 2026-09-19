# -*- coding: utf-8 -*-
"""
收藏页面。

提供链接收藏的增删改查、URL 校验、分类筛选、名称/备注搜索和软删除。
页面只处理 Streamlit 交互；具体数据库操作放在 utils.link_service。
"""

import html as html_lib

import streamlit as st

from models.models import Link
from utils.db import SessionLocal
from utils import link_service as service


def _init_state() -> None:
    """初始化收藏页需要的临时界面状态（键名避开任何 widget key）。"""
    st.session_state.setdefault("link_form_mode", None)
    st.session_state.setdefault("link_edit_id", None)
    st.session_state.setdefault("link_delete_confirm_id", None)


def _close_form() -> None:
    """退出新增/编辑表单。"""
    st.session_state.link_form_mode = None
    st.session_state.link_edit_id = None


def _link_form(session, link: Link | None) -> None:
    """渲染新增或编辑链接表单。"""
    is_edit = link is not None
    category = link.category if is_edit and link.category in service.LINK_CATEGORIES else "默认"

    with st.container(border=True):
        with st.form(f"link_form_{'edit_' + str(link.id) if is_edit else 'new'}"):
            st.subheader("编辑链接" if is_edit else "新增链接")
            name = st.text_input(
                "名称 *",
                value=link.name if is_edit else "",
                placeholder="例如：GitHub",
            )
            url = st.text_input(
                "URL *",
                value=link.url if is_edit else "",
                placeholder="https://example.com",
            )
            category_index = service.LINK_CATEGORIES.index(category)
            category = st.selectbox(
                "分类",
                options=service.LINK_CATEGORIES,
                index=category_index,
                format_func=service.category_label,
            )
            note = st.text_area(
                "备注（选填）",
                value=link.note if is_edit and link.note else "",
                height=90,
            )

            save_col, cancel_col = st.columns(2)
            save_clicked = save_col.form_submit_button("💾 保存", type="primary", use_container_width=True)
            cancel_clicked = cancel_col.form_submit_button("取消", use_container_width=True)

    if cancel_clicked:
        _close_form()
        st.rerun()

    if save_clicked:
        try:
            if is_edit:
                service.update_link(session, link.id, name, url, category, note)
                st.toast("链接已更新")
            else:
                service.create_link(session, name, url, category, note)
                st.toast("链接已添加")
            _close_form()
            st.rerun()
        except service.LinkValidationError as exc:
            st.error(str(exc))


def _link_card(session, link: Link) -> None:
    """渲染单条收藏链接。"""
    with st.container(border=True):
        link_col, edit_col, delete_col = st.columns([5, 1, 1])

        # href 和文本都做 HTML 转义；只允许 http/https，防止注入和危险协议
        safe_name = html_lib.escape(link.name)
        safe_url = html_lib.escape(link.url, quote=True)
        link_col.markdown(
            f'<a href="{safe_url}" target="_blank" rel="noopener noreferrer">🔗 {safe_name}</a>',
            unsafe_allow_html=True,
        )

        if edit_col.button("编辑", key=f"link_edit_{link.id}", use_container_width=True):
            st.session_state.link_form_mode = "edit"
            st.session_state.link_edit_id = link.id
            st.session_state.link_delete_confirm_id = None
            st.rerun()
        if delete_col.button("删除", key=f"link_delete_{link.id}", use_container_width=True):
            st.session_state.link_delete_confirm_id = link.id
            st.rerun()

        st.caption(f"{service.category_label(link.category)}　｜　{link.url}")
        if link.note:
            st.write(link.note)

        if st.session_state.link_delete_confirm_id == link.id:
            st.warning("确定删除该收藏链接？删除后会进入回收站。")
            confirm_col, cancel_col = st.columns(2)
            if confirm_col.button("确认删除", key=f"link_delete_confirm_{link.id}", type="primary", use_container_width=True):
                service.soft_delete_link(session, link.id)
                st.session_state.link_delete_confirm_id = None
                _close_form()
                st.toast("已删除")
                st.rerun()
            if cancel_col.button("取消", key=f"link_delete_cancel_{link.id}", use_container_width=True):
                st.session_state.link_delete_confirm_id = None
                st.rerun()


def show() -> None:
    """渲染收藏页面。"""
    _init_state()
    st.title("🔗 收藏")

    action_col, search_col, filter_col = st.columns([1, 2, 2])
    if action_col.button("＋ 新增链接", type="primary", use_container_width=True):
        st.session_state.link_form_mode = "new"
        st.session_state.link_edit_id = None
        st.session_state.link_delete_confirm_id = None
        st.rerun()
    keyword = search_col.text_input(
        "搜索",
        placeholder="按名称 / 备注搜索",
        label_visibility="collapsed",
    )
    category = filter_col.selectbox(
        "分类筛选",
        options=("全部",) + service.LINK_CATEGORIES,
        format_func=lambda value: value if value == "全部" else service.category_label(value),
        label_visibility="collapsed",
    )

    with SessionLocal() as session:
        editing_link = None
        if st.session_state.link_form_mode == "edit" and st.session_state.link_edit_id:
            editing_link = session.get(Link, st.session_state.link_edit_id)
            if editing_link is None:
                _close_form()
                st.rerun()
        if st.session_state.link_form_mode in ("new", "edit"):
            _link_form(session, editing_link)
            st.divider()

        links = service.list_links(session, category=category, keyword=keyword)
        if not links:
            if keyword:
                empty_text = "没有匹配名称或备注的链接。"
            elif category != "全部":
                empty_text = f"「{category}」分类下暂无链接，点击「新增链接」收藏。"
            else:
                empty_text = "还没有收藏链接，点击「新增链接」收藏常用网站。"
            st.info(empty_text)
        else:
            for link in links:
                _link_card(session, link)
