# -*- coding: utf-8 -*-
"""
知识库页面。

左侧是文件夹树（根目录 + 多层嵌套，支持新建子文件夹、重命名、级联删除），
右侧是当前文件夹的文档列表、全文搜索结果或文档编辑器。
页面只处理 Streamlit 交互；具体数据库操作放在 utils.kb_service。
"""

import streamlit as st

from models.models import KbDoc, KbFolder
from utils.db import SessionLocal
from utils import kb_service as service


def _init_state() -> None:
    """初始化知识库页需要的临时界面状态（键名避开任何 widget key）。"""
    st.session_state.setdefault("kb_current_folder", None)   # None 表示根目录
    st.session_state.setdefault("kb_doc_mode", None)         # new / edit / None
    st.session_state.setdefault("kb_doc_id", None)
    st.session_state.setdefault("kb_folder_form", None)      # (动作, 目标文件夹 id)
    st.session_state.setdefault("kb_delete_folder_id", None)
    st.session_state.setdefault("kb_delete_doc_id", None)


def _back_to_list() -> None:
    """退出文档编辑器，回到文件夹文档列表。"""
    st.session_state.kb_doc_mode = None
    st.session_state.kb_doc_id = None
    st.session_state.kb_delete_doc_id = None


def _folder_form_box(session) -> None:
    """渲染文件夹新建 / 重命名小表单。"""
    form_state = st.session_state.kb_folder_form
    if not form_state:
        return

    action, target_id = form_state
    existing_name = ""
    if action == "rename" and target_id:
        folder = session.get(KbFolder, target_id)
        existing_name = folder.name if folder else ""

    with st.container(border=True):
        st.caption("重命名文件夹" if action == "rename" else "新建文件夹")
        with st.form(f"kb_folder_form_{action}_{target_id}"):
            name = st.text_input(
                "文件夹名称",
                value=existing_name,
                placeholder="请输入文件夹名称",
                label_visibility="collapsed",
            )
            ok_col, cancel_col = st.columns(2)
            confirmed = ok_col.form_submit_button("确认", type="primary", use_container_width=True)
            cancelled = cancel_col.form_submit_button("取消", use_container_width=True)

    if cancelled:
        st.session_state.kb_folder_form = None
        st.rerun()
    if confirmed:
        try:
            if action == "rename":
                service.rename_folder(session, target_id, name)
                st.toast("文件夹已重命名")
            elif action == "new_root":
                service.create_folder(session, name, None)
                st.toast("文件夹已创建")
            else:  # new_child
                service.create_folder(session, name, target_id)
                st.toast("子文件夹已创建")
            st.session_state.kb_folder_form = None
            st.rerun()
        except service.KbValidationError as exc:
            st.error(str(exc))


def _render_tree(session, folders: list[KbFolder], parent_id: int | None, depth: int) -> None:
    """递归渲染一层文件夹；用全角空格表示缩进，避免深层嵌套 columns。"""
    children = [f for f in folders if f.parent_id == parent_id]
    current_id = st.session_state.kb_current_folder
    for folder in children:
        indent = "　" * depth
        name_col, add_col, ren_col, del_col = st.columns([6, 1, 1, 1])
        label = f"{indent}📁 {folder.name}"
        if name_col.button(
            label,
            key=f"kb_open_{folder.id}",
            type="primary" if current_id == folder.id else "secondary",
            use_container_width=True,
        ):
            st.session_state.kb_current_folder = folder.id
            _back_to_list()
            st.session_state.kb_delete_folder_id = None
            st.rerun()
        if add_col.button("➕", key=f"kb_child_{folder.id}", help="新建子文件夹", use_container_width=True):
            st.session_state.kb_folder_form = ("new_child", folder.id)
            st.rerun()
        if ren_col.button("✏️", key=f"kb_ren_{folder.id}", help="重命名", use_container_width=True):
            st.session_state.kb_folder_form = ("rename", folder.id)
            st.rerun()
        if del_col.button("🗑", key=f"kb_del_{folder.id}", help="删除文件夹", use_container_width=True):
            st.session_state.kb_delete_folder_id = folder.id
            st.rerun()
        _render_tree(session, folders, folder.id, depth + 1)


def _render_folder_panel(session, current_id: int | None) -> None:
    """渲染左栏：根目录入口、新建根文件夹、文件夹树和删除确认。"""
    root_col, new_col = st.columns([3, 1])
    if root_col.button(
        "🗄 根目录",
        type="primary" if current_id is None else "secondary",
        use_container_width=True,
    ):
        st.session_state.kb_current_folder = None
        _back_to_list()
        st.rerun()
    if new_col.button("＋ 根文件夹", use_container_width=True):
        st.session_state.kb_folder_form = ("new_root", None)
        st.rerun()

    _folder_form_box(session)

    folders = service.list_folders(session)
    if not folders and current_id is None:
        st.caption("还没有文件夹，先点右上角新建一个。")
    _render_tree(session, folders, None, 0)

    # 文件夹删除二次确认（级联数量提前统计给用户看）
    delete_id = st.session_state.kb_delete_folder_id
    if delete_id is not None:
        folder = session.get(KbFolder, delete_id)
        if folder is not None:
            st.divider()
            try:
                sub_count, doc_count = service.folder_cascade_counts(session, delete_id)
            except service.KbValidationError:
                st.session_state.kb_delete_folder_id = None
                st.rerun()
            st.warning(
                f"确定删除文件夹「{folder.name}」？将连带删除 {sub_count} 个子文件夹和 "
                f"{doc_count} 篇文档，全部进入回收站。"
            )
            ok_col, cancel_col = st.columns(2)
            if ok_col.button("确认删除", key="kb_folder_delete_ok", type="primary", use_container_width=True):
                service.delete_folder_cascade(session, delete_id)
                if st.session_state.kb_current_folder == delete_id:
                    st.session_state.kb_current_folder = None
                st.session_state.kb_delete_folder_id = None
                _back_to_list()
                st.toast("文件夹及其中内容已删除")
                st.rerun()
            if cancel_col.button("取消", key="kb_folder_delete_cancel", use_container_width=True):
                st.session_state.kb_delete_folder_id = None
                st.rerun()


def _doc_editor(session, folder_id: int, doc: KbDoc | None) -> None:
    """渲染新建 / 编辑文档表单。"""
    is_edit = doc is not None
    with st.container(border=True):
        with st.form(f"kb_doc_form_{'edit_' + str(doc.id) if is_edit else 'new'}"):
            st.subheader("编辑文档" if is_edit else "新建文档")
            name = st.text_input(
                "文档名称",
                value=doc.name if is_edit else "",
                placeholder="例如：会议纪要.md",
            )
            content = st.text_area(
                "文档内容（支持 Markdown）",
                value=doc.content if is_edit else "",
                height=420,
            )
            save_col, cancel_col = st.columns(2)
            saved = save_col.form_submit_button("💾 保存", type="primary", use_container_width=True)
            cancelled = cancel_col.form_submit_button("取消", use_container_width=True)

    if cancelled:
        _back_to_list()
        st.rerun()
    if saved:
        try:
            if is_edit:
                service.update_doc(session, doc.id, name, content)
                st.toast("文档已保存")
            else:
                new_doc = service.create_doc(session, folder_id, name, content)
                st.session_state.kb_doc_id = new_doc.id
                st.toast("文档已创建")
            _back_to_list()
            st.rerun()
        except service.KbValidationError as exc:
            st.error(str(exc))


def _doc_list(session, folder: KbFolder) -> None:
    """渲染当前文件夹下的文档列表。"""
    docs = service.list_docs(session, folder.id)
    st.caption(f"📂 {folder.name}（{len(docs)} 个文档）")
    if st.button("＋ 新建文档", type="primary", use_container_width=True):
        st.session_state.kb_doc_mode = "new"
        st.session_state.kb_doc_id = None
        st.rerun()

    if not docs:
        st.info("该文件夹暂无文档，点击「新建文档」开始记录。")
        return

    for doc in docs:
        with st.container(border=True):
            name_col, del_col = st.columns([6, 1])
            if name_col.button(f"📄 {doc.name}", key=f"kb_doc_open_{doc.id}", use_container_width=True):
                st.session_state.kb_doc_mode = "edit"
                st.session_state.kb_doc_id = doc.id
                st.session_state.kb_delete_doc_id = None
                st.rerun()
            if del_col.button("删除", key=f"kb_doc_del_{doc.id}", use_container_width=True):
                st.session_state.kb_delete_doc_id = doc.id
                st.rerun()
            st.caption(f"更新于 {doc.updated_at:%Y-%m-%d %H:%M}")

            if st.session_state.kb_delete_doc_id == doc.id:
                st.warning(f"确定删除文档「{doc.name}」？删除后会进入回收站。")
                ok_col, cancel_col = st.columns(2)
                if ok_col.button("确认删除", key=f"kb_doc_delete_ok_{doc.id}", type="primary", use_container_width=True):
                    service.soft_delete_doc(session, doc.id)
                    st.session_state.kb_delete_doc_id = None
                    _back_to_list()
                    st.toast("已删除")
                    st.rerun()
                if cancel_col.button("取消", key=f"kb_doc_delete_cancel_{doc.id}", use_container_width=True):
                    st.session_state.kb_delete_doc_id = None
                    st.rerun()


def _search_results(session, keyword: str) -> None:
    """渲染跨文件夹全文搜索结果。"""
    hits = service.search_docs(session, keyword)
    st.caption(f"🔍 找到 {len(hits)} 个匹配文档")
    if not hits:
        st.info("没有匹配名称或内容的文档。")
        return
    for doc, path_text in hits:
        with st.container(border=True):
            st.caption(f"📁 {path_text}")
            if st.button(f"📄 {doc.name}", key=f"kb_search_open_{doc.id}", use_container_width=True):
                st.session_state.kb_current_folder = doc.folder_id
                st.session_state.kb_doc_mode = "edit"
                st.session_state.kb_doc_id = doc.id
                st.rerun()


def show() -> None:
    """渲染知识库页面。"""
    _init_state()
    st.title("📚 知识库")

    left_col, right_col = st.columns([1, 2])

    with SessionLocal() as session:
        with left_col:
            current_id = st.session_state.kb_current_folder
            _render_folder_panel(session, current_id)

        with right_col:
            keyword = st.text_input(
                "全文搜索",
                placeholder="🔍 按文档名称 / 内容搜索（留空显示当前文件夹）",
                label_visibility="collapsed",
            )

            # 打开了文档时优先显示编辑器；其次是搜索视图；最后是文件夹视图
            doc_mode = st.session_state.kb_doc_mode
            doc_id = st.session_state.kb_doc_id
            editing_doc = session.get(KbDoc, doc_id) if doc_mode == "edit" and doc_id else None
            if doc_mode == "edit" and editing_doc is None:
                _back_to_list()
                st.rerun()

            if doc_mode in ("new", "edit"):
                if st.button("← 返回列表", key="kb_doc_back"):
                    _back_to_list()
                    st.rerun()
                target_folder_id = editing_doc.folder_id if editing_doc else st.session_state.kb_current_folder
                _doc_editor(session, target_folder_id, editing_doc)
            elif keyword.strip():
                _search_results(session, keyword.strip())
            else:
                current_id = st.session_state.kb_current_folder
                if current_id is None:
                    st.caption("📂 根目录")
                    st.info("根目录用于管理文件夹。请在左侧进入一个文件夹，或先新建文件夹后再创建文档。")
                else:
                    folder = session.get(KbFolder, current_id)
                    if folder is None:
                        st.session_state.kb_current_folder = None
                        st.rerun()
                    st.caption(service.folder_path(session, current_id))
                    _doc_list(session, folder)
