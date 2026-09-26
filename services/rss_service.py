# -*- coding: utf-8 -*-
"""
RSS 订阅与待处理队列服务。

- RSS 源验证、添加、删除、抓取
- 新条目按链接精确去重后写入 pending_items
- 待处理条目先抓取正文并 AI 改写，用户确认保存后才写入 notes
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import feedparser
import httpx
from sqlalchemy import select

from models.models import Note, PendingItem, RssSource
from services import ai_service, crawler_service, note_service

# 抓取 RSS 的超时时间
RSS_TIMEOUT = httpx.Timeout(15.0, connect=10.0)

# 复用网页抓取的浏览器标识
USER_AGENT = crawler_service.USER_AGENT


class RssError(Exception):
    """RSS 业务失败，message 为可直接展示的中文信息。"""


class RssSourceNotFound(Exception):
    """RSS 源不存在。"""


class PendingNotFound(Exception):
    """待处理内容不存在。"""


def _read_feed(url: str) -> feedparser.FeedParserDict:
    """先用 httpx 控制超时，再交给 feedparser 解析。"""
    url = (url or "").strip()
    if not url:
        raise RssError("请输入 RSS 地址")
    if not url.lower().startswith(("http://", "https://")):
        raise RssError("RSS 地址需以 http:// 或 https:// 开头")

    try:
        with httpx.Client(
            headers={"User-Agent": USER_AGENT},
            timeout=RSS_TIMEOUT,
            follow_redirects=True,
        ) as client:
            response = client.get(url)
    except httpx.TimeoutException as exc:
        raise RssError("RSS 抓取超时，请稍后重试") from exc
    except httpx.HTTPError as exc:
        raise RssError("RSS 地址打不开，请检查链接或网络") from exc

    if response.status_code >= 400:
        raise RssError(f"RSS 返回错误状态码 {response.status_code}")

    parsed = feedparser.parse(response.content)
    # bozo 表示源可能格式错误；若同时没有任何可用条目，就判定无效
    if parsed.bozo and not parsed.entries:
        raise RssError("RSS 解析失败，请确认地址是有效的订阅源")
    if not parsed.feed.get("title") and not parsed.entries:
        raise RssError("地址内容不是有效的 RSS 订阅源")
    return parsed


def add_source(session: Any, name: str, url: str) -> RssSource:
    """验证并添加 RSS 源；相同地址不重复添加。"""
    name = (name or "").strip()
    url = (url or "").strip()
    if not name:
        raise RssError("请填写 RSS 源名称")
    if not url:
        raise RssError("请填写 RSS 地址")

    exists = session.scalar(select(RssSource).where(RssSource.url == url))
    if exists:
        raise RssError("这个 RSS 源已经添加过了")

    # 先确认地址可访问、可解析，再入库
    _read_feed(url)
    source = RssSource(name=name, url=url, created_at=datetime.now())
    session.add(source)
    session.commit()
    session.refresh(source)
    return source


def list_sources(session: Any) -> list[RssSource]:
    """返回全部 RSS 源，新添加的在前。"""
    return list(session.scalars(
        select(RssSource).order_by(
            RssSource.created_at.desc(), RssSource.id.desc()
        )
    ).all())


def get_source_by_id(session: Any, source_id: int) -> RssSource:
    """按主键读取 RSS 源。"""
    source = session.get(RssSource, source_id)
    if source is None:
        raise RssSourceNotFound("RSS 源不存在或已被删除")
    return source


def delete_source(session: Any, source_id: int) -> None:
    """删除 RSS 源；已进入待处理队列的内容保留。"""
    source = get_source_by_id(session, source_id)
    session.delete(source)
    session.commit()


def _entry_time(entry: Any) -> datetime:
    """读取 RSS 条目的发布时间；缺失或异常时用当前时间。"""
    for field in ("published_parsed", "updated_parsed"):
        value = entry.get(field)
        if not value:
            continue
        try:
            return datetime(*value[:6])
        except (TypeError, ValueError):
            continue
    return datetime.now()


def _log_fetch_result(
    source_name: str,
    status: str,
    message: str,
    new_count: int,
) -> None:
    """记录单个源或整批抓取结果。"""
    from services import scheduler_service

    scheduler_service.add_log(source_name, status, message, new_count)


def fetch_source(session: Any, source_id: int) -> int:
    """抓取单个 RSS 源的新条目，返回本次新增条数。"""
    source = get_source_by_id(session, source_id)
    try:
        parsed = _read_feed(source.url)

        pending_urls = set(session.scalars(select(PendingItem.url)).all())
        note_urls = {
            url for url in session.scalars(select(Note.original_url)).all()
            if url
        }
        known_urls = pending_urls | note_urls
        feed_urls: set[str] = set()
        now = datetime.now()

        for entry in parsed.entries:
            link = (entry.get("link") or "").strip()
            if not link or link in known_urls or link in feed_urls:
                continue
            feed_urls.add(link)
            session.add(PendingItem(
                url=link,
                title=(entry.get("title") or "").strip() or "无标题",
                source=source.name,
                status="pending",
                created_at=_entry_time(entry),
            ))

        source.last_fetched = now
        session.commit()
    except Exception as exc:
        session.rollback()
        _log_fetch_result(source.name, "failed", f"抓取失败：{exc}", 0)
        raise

    _log_fetch_result(
        source.name,
        "success",
        f"抓取完成，新增 {len(feed_urls)} 条",
        len(feed_urls),
    )
    return len(feed_urls)


def fetch_all_sources(session: Any, log_summary: bool = True) -> list[dict[str, Any]]:
    """抓取全部源；单个源失败只影响该源。"""
    results: list[dict[str, Any]] = []
    for source in list_sources(session):
        try:
            added = fetch_source(session, source.id)
            results.append({
                "id": source.id,
                "name": source.name,
                "added": added,
                "error": "",
            })
        except Exception as exc:
            results.append({
                "id": source.id,
                "name": source.name,
                "added": 0,
                "error": str(exc) or "抓取失败",
            })

    if log_summary:
        total_added = sum(int(item["added"]) for item in results)
        failed_count = sum(1 for item in results if item["error"])
        status = "failed" if failed_count else "success"
        if failed_count:
            message = f"全部抓取完成，新增 {total_added} 条，{failed_count} 个源失败"
        else:
            message = f"全部抓取完成，新增 {total_added} 条"
        _log_fetch_result("全部", status, message, total_added)
    return results


def list_pending(session: Any) -> list[PendingItem]:
    """返回待处理和已跳过内容；已完成内容不显示。"""
    return list(session.scalars(
        select(PendingItem)
        .where(PendingItem.status.in_(("pending", "skipped")))
        .order_by(PendingItem.created_at.desc(), PendingItem.id.desc())
    ).all())


def get_pending_by_id(session: Any, item_id: int) -> PendingItem:
    """按主键读取待处理项。"""
    item = session.get(PendingItem, item_id)
    if item is None:
        raise PendingNotFound("待处理内容不存在或已被删除")
    return item


def process_pending_item(session: Any, item_id: int) -> dict[str, Any]:
    """抓取条目正文并 AI 改写，返回预览数据；本函数不保存笔记。"""
    item = get_pending_by_id(session, item_id)
    if item.status == "done":
        raise RssError("这条内容已经处理完成")

    # 已跳过的条目重新处理时，先恢复为待处理
    if item.status == "skipped":
        item.status = "pending"
        session.commit()

    html_bytes, final_url = crawler_service.fetch_url(item.url)
    extracted = crawler_service.extract_content(html_bytes, final_url)
    title = item.title or extracted["title"] or "无标题"
    content = ai_service.rewrite_to_plain(title, extracted["content"])
    tags = note_service.clean_tag_list(ai_service.generate_tags(title, extracted["content"]))
    return {
        "item_id": item.id,
        "title": title,
        "content": content,
        "tags": tags,
        "original_url": item.url,
        "source": item.source,
    }


def save_processed_item(
    session: Any,
    item_id: int,
    title: str,
    content: str,
    tags: list[str],
) -> Note:
    """保存预览笔记，并把队列项在同一事务中标记为完成。"""
    item = get_pending_by_id(session, item_id)
    if item.status == "done":
        raise RssError("这条内容已经保存过了")

    already = session.scalar(select(Note).where(Note.original_url == item.url))
    if already:
        raise RssError("这个链接对应的笔记已经存在")

    note = note_service.create_note(
        session=session,
        title=title,
        content=content,
        original_url=item.url,
        tags=tags,
        source=item.source or "RSS",
        category="默认",
        commit=False,
    )
    item.status = "done"
    session.commit()
    session.refresh(note)
    # 队列状态和笔记在同一事务提交后，再建立向量索引
    note_service.index_note(note)
    return note


def skip_pending_item(session: Any, item_id: int) -> None:
    """把待处理项标记为跳过。"""
    item = get_pending_by_id(session, item_id)
    if item.status == "done":
        raise RssError("已完成内容不能跳过")
    item.status = "skipped"
    session.commit()


def delete_pending_item(session: Any, item_id: int) -> None:
    """删除待处理项。"""
    item = get_pending_by_id(session, item_id)
    session.delete(item)
    session.commit()


def serialize_source(source: RssSource) -> dict[str, Any]:
    """RSS 源转前端字典。"""
    return {
        "id": source.id,
        "name": source.name,
        "url": source.url,
        "last_fetched": source.last_fetched.isoformat() if source.last_fetched else None,
        "created_at": source.created_at.isoformat() if source.created_at else None,
    }


def serialize_pending(item: PendingItem) -> dict[str, Any]:
    """待处理项转前端字典。"""
    return {
        "id": item.id,
        "url": item.url,
        "title": item.title,
        "source": item.source,
        "status": item.status,
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }
