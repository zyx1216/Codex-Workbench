# -*- coding: utf-8 -*-
"""
笔记页面。

左侧是搜索、分类筛选、标签快筛和笔记列表；右侧是新建/编辑表单和
Markdown 预览。页面只处理 Streamlit 交互；具体数据库操作放在 utils.note_service。
"""

import streamlit as st

from models.models import Note
from utils.db import SessionLocal
from utils import note_service as service


def _init_state() -> None:
    """初始化笔记页需要的临时界面状态。"""
    st.session_state.setdefault("note_selected_id", None)
    st.session_state.setdefault("note_form_mode", None)
    st.session_state.setdefault("note_delete_confirm_id", None)
    st.session_state.setdefault("note_active_tag", None)
    st.session_state.setdefault("note_preview", False)


def _close_form() -> None:
    """退出新建/编辑表单；预览开关由 toggle 自身管理，这里不直接写 widget key。"""
    st.session_state.note_form_mode = None


def _note_form(session, note: Note | None) -> None:
    """渲染新建或编辑笔记表单。"""
    is_edit = note is not None
    tags_value = service.tags_to_text(note.tags) if is_edit else ""
    category = "默认"
    if is_edit and note.category in service.NOTE_CATEGORIES:
        category = note.category

    with st.container(border=True):
        st.subheader("编辑笔记" if is_edit else "新增笔记")
        mode_col, preview_col = st.columns([4, 1])
        with preview_col:
            st.toggle("👁 预览", key="note_preview")

        if not st.session_state.note_preview:
            with st.form(f"note_form_{'edit_' + str(note.id) if is_edit else 'new'}"):
                title = st.text_input(
                    "标题",
                    value=note.title if is_edit else "",
                    placeholder="未填写时显示为“无标题”",
                )
                content = st.text_area(
                    "正文（支持 Markdown）",
                    value=note.content if is_edit else "",
                    height=360,
                )
                tag_text = st.text_input(
                    "标签（逗号分隔）",
                    value=tags_value,
                    placeholder="例如：复盘, 算法, 毕设",
                )
                category_index = service.NOTE_CATEGORIES.index(category)
                category = st.selectbox(
                    "分类",
                    options=service.NOTE_CATEGORIES,
                    index=category_index,
                    format_func=service.category_label,
                )

                save_col, cancel_col = st.columns(2)
                save_clicked = save_col.form_submit_button("💾 保存", type="primary", use_container_width=True)
                cancel_clicked = cancel_col.form_submit_button("取消", use_container_width=True)
        else:
            # 预览模式：直接渲染数据库中已保存的正文（不允许 HTML，无 XSS 风险）
            save_clicked = cancel_clicked = False
            st.markdown(f"## {note.title if is_edit and note.title else '（新笔记预览）'}")
            st.markdown(note.content if is_edit and note.content else "*暂无内容*")
            st.caption(f"分类：{service.category_label(category)}　｜　标签：{tags_value or '无'}")
            st.caption("再次点击顶部「👁 预览」开关可返回编辑。")

    if cancel_clicked:
        _close_form()
        st.rerun()

    if save_clicked:
        if is_edit:
            service.update_note(session, note.id, title, content, tag_text, category)
            st.toast("笔记已更新")
        else:
            new_note = service.create_note(session, title, content, tag_text, category)
            st.session_state.note_selected_id = new_note.id
            st.toast("笔记已添加")
        _close_form()
        st.rerun()


def _note_card(session, note: Note) -> None:
    """渲染左侧列表中的单条笔记。"""
    selected = st.session_state.note_selected_id == note.id
    with st.container(border=selected):
        # 按钮 label 不支持 Markdown，标题过长时截断显示，完整内容在右侧查看
        label = note.title or "无标题"
        if len(label) > 28:
            label = label[:28] + "…"
        if st.button(
            label,
            key=f"note_open_{note.id}",
            type="primary" if selected else "secondary",
            use_container_width=True,
        ):
            st.session_state.note_selected_id = note.id
            st.session_state.note_form_mode = "edit"
            st.session_state.note_delete_confirm_id = None
            st.session_state.note_preview = False
            st.rerun()

        tags = service.load_tags(note)
        tag_text = "　".join(f"#{tag}" for tag in tags)
        meta = f"{service.category_label(note.category)}　｜　{note.updated_at:%Y-%m-%d %H:%M}"
        st.caption(meta + (f"　｜　{tag_text}" if tag_text else ""))


def _note_editor(session) -> None:
    """渲染右侧编辑器区域。"""
    form_mode = st.session_state.note_form_mode
    selected_id = st.session_state.note_selected_id

    editing_note = None
    if form_mode == "edit" and selected_id:
        editing_note = session.get(Note, selected_id)
        if editing_note is None:
            _close_form()
            st.session_state.note_selected_id = None
            st.rerun()

    if form_mode in ("new", "edit"):
        _note_form(session, editing_note)
        return

    if editing_note is None and selected_id:
        editing_note = session.get(Note, selected_id)

    if editing_note is None:
        st.info("选择左侧笔记，或点击「＋ 新建笔记」开始记录。")
        return

    # 非表单态：只读展示已保存内容，并提供编辑和删除入口
    with st.container(border=True):
        st.markdown(f"## {editing_note.title or '无标题'}")
        st.caption(
            f"{service.category_label(editing_note.category)}"
            f"　｜　更新于 {editing_note.updated_at:%Y-%m-%d %H:%M}"
        )
        tags = service.load_tags(editing_note)
        if tags:
            st.caption("标签：" + "　".join(f"#{tag}" for tag in tags))
        st.divider()
        st.markdown(editing_note.content or "*暂无内容*")

        action_col, delete_col = st.columns([1, 1])
        if action_col.button("✏️ 编辑", key="note_edit_btn", type="primary", use_container_width=True):
            st.session_state.note_form_mode = "edit"
            st.session_state.note_preview = False
            st.rerun()
        if delete_col.button("🗑 删除", key="note_delete_btn", use_container_width=True):
            st.session_state.note_delete_confirm_id = editing_note.id
            st.rerun()

        if st.session_state.note_delete_confirm_id == editing_note.id:
            st.warning("确定删除该笔记？删除后会进入回收站。")
            confirm_col, cancel_col = st.columns(2)
            if confirm_col.button("确认删除", key="note_delete_confirm", type="primary", use_container_width=True):
                service.soft_delete_note(session, editing_note.id)
                st.session_state.note_delete_confirm_id = None
                st.session_state.note_selected_id = None
                _close_form()
                st.toast("已删除")
                st.rerun()
            if cancel_col.button("取消", key="note_delete_cancel", use_container_width=True):
                st.session_state.note_delete_confirm_id = None
                st.rerun()


def show() -> None:
    """渲染笔记页面。"""
    _init_state()
    st.title("📔 笔记")

    left_col, right_col = st.columns([1, 2])

    with SessionLocal() as session:
        with left_col:
            if st.button("＋ 新建笔记", type="primary", use_container_width=True):
                st.session_state.note_form_mode = "new"
                st.session_state.note_selected_id = None
                st.session_state.note_delete_confirm_id = None
                st.session_state.note_preview = False
                st.rerun()

            keyword = st.text_input("搜索（标题和内容）", placeholder="输入关键词实时过滤")
            category = st.selectbox(
                "分类筛选",
                options=("全部",) + service.NOTE_CATEGORIES,
                format_func=lambda value: value if value == "全部" else service.category_label(value),
            )

            # 标签快筛：点击只看该标签，再点一次取消
            existing_tags = service.all_tags(session)
            active_tag = st.session_state.note_active_tag
            if existing_tags:
                st.caption("标签快筛")
                chip_cols = st.columns(3)
                for index, tag in enumerate(existing_tags):
                    if chip_cols[index % 3].button(
                        f"{'✅ ' if active_tag == tag else '#'}{tag}",
                        key=f"note_tag_chip_{tag}",
                        use_container_width=True,
                    ):
                        st.session_state.note_active_tag = None if active_tag == tag else tag
                        st.rerun()
                if active_tag:
                    st.caption(f"当前标签筛选：#{active_tag}")

            notes = service.list_notes(
                session,
                category=category,
                tag=st.session_state.note_active_tag,
                keyword=keyword,
            )
            st.divider()
            if not notes:
                st.info("暂无匹配的笔记。")
            else:
                for note in notes:
                    _note_card(session, note)

        with right_col:
            _note_editor(session)
