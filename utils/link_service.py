# -*- coding: utf-8 -*-
"""
收藏模块业务服务。

Streamlit 页面只负责交互和展示；链接的增删改查、URL 校验、
分类/关键词过滤、软删除快照等逻辑集中放在这里。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from models.models import Link, TrashItem

# 与计划、笔记模块保持一致的固定分类
LINK_CATEGORIES = ("日程安排", "学习", "旅行", "会议", "工作", "生活", "默认")

CATEGORY_ICONS = {
    "日程安排": "🗓",
    "学习": "📚",
    "旅行": "✈️",
    "会议": "📋",
    "工作": "💼",
    "生活": "🏠",
    "默认": "📌",
}


class LinkValidationError(ValueError):
    """收藏链接数据不合法。"""


def category_icon(category: str | None) -> str:
    """返回分类图标；未知分类使用普通别针图标。"""
    return CATEGORY_ICONS.get(category or "默认", "📌")


def category_label(category: str | None) -> str:
    """返回带图标的分类名称。"""
    category = category or "默认"
    return f"{category_icon(category)} {category}"


def normalize_url(raw: str) -> str:
    """
    校验并归一 URL。

    只接受 http:// 或 https:// 开头的地址，不自动补全协议，
    顺带挡住 javascript: 等危险协议；再用 urlsplit 校验能解析出网络地址。
    """
    url = (raw or "").strip()
    if not url:
        raise LinkValidationError("请填写 URL。")
    if not url.lower().startswith(("http://", "https://")):
        raise LinkValidationError("链接需以 http:// 或 https:// 开头。")

    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        raise LinkValidationError("URL 格式不正确。")
    return url


def is_safe_url(url: str | None) -> bool:
    """判断链接是否允许渲染成可点击的 http/https 锚点。"""
    return bool(url) and url.strip().lower().startswith(("http://", "https://"))


def list_links(
    session: Any,
    category: str | None = None,
    keyword: str = "",
) -> list[Link]:
    """
    查询收藏链接。

    分类精确匹配；关键词只在名称和备注里做大小写不敏感包含（本版不搜 URL）。
    排序固定为创建时间倒序、ID 倒序。
    """
    links = session.query(Link).order_by(Link.created_at.desc(), Link.id.desc()).all()
    query = (keyword or "").strip().lower()

    result: list[Link] = []
    for link in links:
        if category and category != "全部" and (link.category or "默认") != category:
            continue
        if query:
            haystack = f"{link.name or ''}\n{link.note or ''}".lower()
            if query not in haystack:
                continue
        result.append(link)
    return result


def create_link(
    session: Any,
    name: str,
    url: str,
    category: str,
    note: str = "",
) -> Link:
    """新建收藏链接；名称和 URL 必填。"""
    name = (name or "").strip()
    note = (note or "").strip()
    category = category or "默认"
    if not name:
        raise LinkValidationError("请填写名称。")
    url = normalize_url(url)

    link = Link(
        name=name,
        url=url,
        note=note or None,
        category=category,
    )
    session.add(link)
    session.commit()
    session.refresh(link)
    return link


def update_link(
    session: Any,
    link_id: int,
    name: str,
    url: str,
    category: str,
    note: str = "",
) -> Link:
    """更新链接的名称、URL、备注和分类；links 表无 updated_at，创建时间保持不变。"""
    link = session.get(Link, link_id)
    if link is None:
        raise LinkValidationError("要编辑的链接不存在，可能已被删除。")

    name = (name or "").strip()
    note = (note or "").strip()
    category = category or "默认"
    if not name:
        raise LinkValidationError("请填写名称。")
    url = normalize_url(url)

    link.name = name
    link.url = url
    link.note = note or None
    link.category = category
    session.commit()
    session.refresh(link)
    return link


def _datetime_to_text(value: datetime | None) -> str | None:
    """JSON 快照中的日期时间序列化。"""
    return value.isoformat(timespec="seconds") if value else None


def _link_snapshot(link: Link) -> dict[str, Any]:
    """把收藏链接序列化成以后可还原的 JSON 快照结构。"""
    return {
        "schema_version": 1,
        "link": {
            "id": link.id,
            "name": link.name,
            "url": link.url,
            "note": link.note,
            "category": link.category,
            "created_at": _datetime_to_text(link.created_at),
        },
    }


def soft_delete_link(session: Any, link_id: int) -> None:
    """软删除链接：先把全字段写入 trash_items 的 JSON 快照，再删除 links 行。"""
    link = session.get(Link, link_id)
    if link is None:
        raise LinkValidationError("要删除的链接不存在，可能已经删除。")

    trash_item = TrashItem(
        item_type="link",
        item_id=str(link.id),
        snapshot=json.dumps(_link_snapshot(link), ensure_ascii=False),
    )
    session.add(trash_item)
    session.delete(link)
    session.commit()
