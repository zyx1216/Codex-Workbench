# -*- coding: utf-8 -*-
"""
知识库业务服务。

Streamlit 页面只负责交互和展示；文件夹树、文档增删改查、全文搜索、
级联删除和回收站快照等逻辑集中放在这里。

约定：根目录不是一条数据，用 parent_id 为空表示；根目录下只能建文件夹，
文档必须挂在某个具体文件夹下（kb_docs.folder_id 非空）。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy.orm import selectinload

from models.models import KbDoc, KbFolder, TrashItem


class KbValidationError(ValueError):
    """知识库数据不合法，例如名称为空或对象不存在。"""


def _require_name(name: str, label: str = "名称") -> str:
    """统一的非空名称校验。"""
    name = (name or "").strip()
    if not name:
        raise KbValidationError(f"请填写{label}。")
    return name


# ==================== 文件夹 ====================

def list_folders(session: Any) -> list[KbFolder]:
    """返回全部文件夹，按名称排序；页面侧自行拼成树。"""
    return session.query(KbFolder).order_by(KbFolder.name, KbFolder.id).all()


def create_folder(session: Any, name: str, parent_id: int | None = None) -> KbFolder:
    """新建文件夹；parent_id 为空表示建在根目录。"""
    name = _require_name(name, "文件夹名称")
    if parent_id is not None and session.get(KbFolder, parent_id) is None:
        raise KbValidationError("上级文件夹不存在。")

    folder = KbFolder(name=name, parent_id=parent_id)
    session.add(folder)
    session.commit()
    session.refresh(folder)
    return folder


def rename_folder(session: Any, folder_id: int, name: str) -> KbFolder:
    """重命名文件夹，不改变层级关系。"""
    folder = session.get(KbFolder, folder_id)
    if folder is None:
        raise KbValidationError("要重命名的文件夹不存在。")
    folder.name = _require_name(name, "文件夹名称")
    session.commit()
    session.refresh(folder)
    return folder


def descendant_folder_ids(session: Any, folder_id: int) -> set[int]:
    """返回含自身在内的整棵子树的文件夹 id 集合。"""
    if session.get(KbFolder, folder_id) is None:
        raise KbValidationError("要删除的文件夹不存在。")

    folders = session.query(KbFolder).all()
    children_map: dict[int | None, list[int]] = {}
    for folder in folders:
        children_map.setdefault(folder.parent_id, []).append(folder.id)

    result: set[int] = set()
    stack = [folder_id]
    while stack:
        current = stack.pop()
        if current in result:
            continue
        result.add(current)
        stack.extend(children_map.get(current, []))
    return result


def folder_cascade_counts(session: Any, folder_id: int) -> tuple[int, int]:
    """统计删除文件夹时连带的子文件夹数量（不含自身）和文档数量，用于删除确认提示。"""
    ids = descendant_folder_ids(session, folder_id)
    subfolder_count = len(ids) - 1
    doc_count = (
        session.query(KbDoc)
        .filter(KbDoc.folder_id.in_(ids))
        .count()
    )
    return subfolder_count, doc_count


# ==================== 文档 ====================

def list_docs(session: Any, folder_id: int) -> list[KbDoc]:
    """返回某文件夹下的直接文档，按更新时间倒序、ID 倒序。"""
    return (
        session.query(KbDoc)
        .filter(KbDoc.folder_id == folder_id)
        .order_by(KbDoc.updated_at.desc(), KbDoc.id.desc())
        .all()
    )


def create_doc(session: Any, folder_id: int | None, name: str, content: str = "") -> KbDoc:
    """在指定文件夹下新建文档；根目录（folder_id 为空）不允许直接放文档。"""
    name = _require_name(name, "文档名称")
    if folder_id is None or session.get(KbFolder, folder_id) is None:
        raise KbValidationError("请先进入一个文件夹再新建文档。")

    doc = KbDoc(folder_id=folder_id, name=name, content=content or "")
    session.add(doc)
    session.commit()
    session.refresh(doc)
    return doc


def update_doc(session: Any, doc_id: int, name: str, content: str) -> KbDoc:
    """更新文档名称和正文，显式刷新 updated_at。"""
    doc = session.get(KbDoc, doc_id)
    if doc is None:
        raise KbValidationError("要编辑的文档不存在。")
    doc.name = _require_name(name, "文档名称")
    doc.content = content or ""
    doc.updated_at = datetime.now()
    session.commit()
    session.refresh(doc)
    return doc


def folder_path(session: Any, folder_id: int | None) -> str:
    """沿 parent_id 拼出“根目录 / A / B”面包屑。"""
    if folder_id is None:
        return "根目录"
    parts: list[str] = []
    current_id: int | None = folder_id
    seen: set[int] = set()
    while current_id is not None and current_id not in seen:
        folder = session.get(KbFolder, current_id)
        if folder is None:
            break
        seen.add(current_id)
        parts.append(folder.name)
        current_id = folder.parent_id
    parts.append("根目录")
    return " / ".join(reversed(parts))


def search_docs(session: Any, keyword: str) -> list[tuple[KbDoc, str]]:
    """
    跨文件夹全文搜索：在文档名称和正文里做大小写不敏感包含。
    返回 (文档, 所属文件夹路径) 列表，按更新时间倒序。
    """
    query = (keyword or "").strip().lower()
    if not query:
        return []

    docs = session.query(KbDoc).order_by(KbDoc.updated_at.desc(), KbDoc.id.desc()).all()
    hits: list[tuple[KbDoc, str]] = []
    for doc in docs:
        haystack = f"{doc.name or ''}\n{doc.content or ''}".lower()
        if query in haystack:
            hits.append((doc, folder_path(session, doc.folder_id)))
    return hits


# ==================== 快照与删除 ====================

def _dt(value: datetime | None) -> str | None:
    """JSON 快照中的日期时间序列化。"""
    return value.isoformat(timespec="seconds") if value else None


def _doc_snapshot_dict(doc: KbDoc) -> dict[str, Any]:
    """单篇文档的快照结构（folder 级联删除和单篇删除共用）。"""
    return {
        "id": doc.id,
        "folder_id": doc.folder_id,
        "name": doc.name,
        "content": doc.content,
        "created_at": _dt(doc.created_at),
        "updated_at": _dt(doc.updated_at),
    }


def soft_delete_doc(session: Any, doc_id: int) -> None:
    """软删除单篇文档：先写 doc 快照到回收站，再删 kb_docs 行。"""
    doc = session.get(KbDoc, doc_id)
    if doc is None:
        raise KbValidationError("要删除的文档不存在。")

    snapshot = {"schema_version": 1, "doc": _doc_snapshot_dict(doc)}
    session.add(TrashItem(
        item_type="doc",
        item_id=str(doc.id),
        snapshot=json.dumps(snapshot, ensure_ascii=False),
    ))
    session.delete(doc)
    session.commit()


def delete_folder_cascade(session: Any, folder_id: int) -> tuple[int, int]:
    """
    级联删除文件夹。

    子树里每篇文档各写一条 doc 快照，整棵子树再写一条 folder 快照
    （含层级关系和文档归属，供以后回收站还原）；随后删除根文件夹，
    子文件夹和文档由 ORM/外键级联删除。
    返回 (子文件夹数, 文档数)。
    """
    root = (
        session.query(KbFolder)
        .options(selectinload(KbFolder.children), selectinload(KbFolder.docs))
        .filter(KbFolder.id == folder_id)
        .one_or_none()
    )
    if root is None:
        raise KbValidationError("要删除的文件夹不存在。")

    ids = descendant_folder_ids(session, folder_id)
    subfolders = (
        session.query(KbFolder)
        .filter(KbFolder.id.in_(ids))
        .order_by(KbFolder.id)
        .all()
    )
    docs = (
        session.query(KbDoc)
        .filter(KbDoc.folder_id.in_(ids))
        .order_by(KbDoc.id)
        .all()
    )

    # 每篇文档一条 doc 快照，将来可以单篇还原
    for doc in docs:
        snapshot = {"schema_version": 1, "doc": _doc_snapshot_dict(doc)}
        session.add(TrashItem(
            item_type="doc",
            item_id=str(doc.id),
            snapshot=json.dumps(snapshot, ensure_ascii=False),
        ))

    # 一条 folder 快照，保存整棵子树的层级关系和文档归属
    folder_snapshot = {
        "schema_version": 1,
        "root_folder_id": root.id,
        "folders": [
            {
                "id": folder.id,
                "name": folder.name,
                "parent_id": folder.parent_id,
                "created_at": _dt(folder.created_at),
            }
            for folder in subfolders
        ],
        "docs": [_doc_snapshot_dict(doc) for doc in docs],
    }
    session.add(TrashItem(
        item_type="folder",
        item_id=str(root.id),
        snapshot=json.dumps(folder_snapshot, ensure_ascii=False),
    ))

    session.delete(root)
    session.commit()
    return len(ids) - 1, len(docs)
