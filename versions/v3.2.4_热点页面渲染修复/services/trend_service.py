# -*- coding: utf-8 -*-
"""多平台热榜抓取、AI 筛选和热点笔记生成。"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import date
from typing import Any
from urllib.parse import quote

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from models.models import HotTrend
from services import ai_service, jina_service, note_service

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 10.0
PLATFORM_INTERVAL = 2.0
PLATFORMS = ("bilibili", "zhihu", "weibo")
PLATFORM_NAMES = {"bilibili": "B站", "zhihu": "知乎", "weibo": "微博"}
_platform_status = {name: {"available": None, "message": ""} for name in PLATFORMS}
_ai_failure_reported = False
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}


class TrendError(Exception):
    """热点业务错误。"""


def parse_heat(value: Any) -> int:
    """把热度数字、万/亿文本统一转成整数。"""
    if isinstance(value, (int, float)):
        return max(0, int(value))
    text = str(value or "").replace(",", "").strip()
    if not text:
        return 0
    match = re.search(r"(\d+(?:\.\d+)?)\s*([万亿]?)", text)
    if not match:
        return 0
    number = float(match.group(1))
    if match.group(2) == "万":
        number *= 10000
    elif match.group(2) == "亿":
        number *= 100000000
    return max(0, int(number))


def _get_json(url: str, referer: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    headers = dict(HEADERS)
    headers["Referer"] = referer
    try:
        response = httpx.get(url, params=params, headers=headers, timeout=REQUEST_TIMEOUT, follow_redirects=True)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("返回不是 JSON 对象")
        return payload
    except Exception as exc:  # noqa: BLE001
        logger.warning("热榜接口失败 url=%s error=%s", url, exc)
        raise TrendError(f"热榜接口失败：{exc}") from exc


def _bilibili_items() -> list[dict[str, Any]]:
    payload = _get_json("https://api.bilibili.com/x/web-interface/search/square", "https://www.bilibili.com", {"limit": 10})
    rows = (((payload.get("data") or {}).get("trending") or {}).get("list")) or []
    result = []
    for item in rows:
        title = str(item.get("show_name") or item.get("word") or item.get("keyword") or "").strip()
        if title:
            result.append({"platform": "bilibili", "title": title, "heat": 0, "url": f"https://search.bilibili.com/all?keyword={quote(title)}"})
    return result


def _zhihu_items() -> list[dict[str, Any]]:
    """知乎热榜 API 需要登录，改用公开 billboard HTML 解析。"""
    try:
        response = httpx.get("https://www.zhihu.com/billboard", headers={**HEADERS, "Referer": "https://www.zhihu.com/hot"}, timeout=REQUEST_TIMEOUT, follow_redirects=True)
        response.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        raise TrendError("知乎需要登录，暂不可用") from exc
    soup = BeautifulSoup(response.text, "html.parser")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for link in soup.find_all("a", href=True):
        href = str(link.get("href") or "")
        if "/question/" not in href:
            continue
        title = link.get_text(" ", strip=True)
        if not title or title in seen:
            continue
        seen.add(title)
        url = href if href.startswith("http") else f"https://www.zhihu.com{href}"
        result.append({"platform": "zhihu", "title": title[:500], "heat": 0, "url": url})
        if len(result) >= 10:
            break
    if not result:
        raise TrendError("知乎需要登录，暂不可用")
    return result


def _weibo_items() -> list[dict[str, Any]]:
    payload = _get_json("https://weibo.com/ajax/side/hotSearch", "https://weibo.com")
    rows = ((payload.get("data") or {}).get("realtime")) or []
    result = []
    for item in rows:
        title = str(item.get("word") or item.get("note") or "").strip()
        if title:
            result.append({"platform": "weibo", "title": title, "heat": parse_heat(item.get("num")), "url": f"https://s.weibo.com/weibo?q={quote(title)}"})
    return result


def _apply_ai(trend: HotTrend) -> None:
    global _ai_failure_reported
    prompt = f"""你是内容筛选助手。目标用户想学习AI、编程、个人成长和知识类内容。
只输出 JSON，不要 Markdown：{{\"score\":0,\"reason\":\"不超过30字\",\"category\":\"分类\"}}
平台：{PLATFORM_NAMES.get(trend.platform, trend.platform)}
标题：{trend.title}
热度：{trend.heat}
"""
    try:
        raw = ai_service.chat(prompt)
        payload = json.loads(re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip())
        trend.ai_score = max(0, min(100, int(payload.get("score", 50))))
        trend.ai_reason = str(payload.get("reason") or "").strip()[:300]
        trend.ai_category = str(payload.get("category") or "未分类").strip()[:50] or "未分类"
        _ai_failure_reported = False
    except Exception as exc:  # noqa: BLE001 - AI 失败必须降级，不影响热点入库
        if not _ai_failure_reported:
            logger.warning("热点 AI 筛选失败，后续本轮静默降级：%s", exc)
            _ai_failure_reported = True
        message = str(exc)
        trend.ai_score = 50
        trend.ai_reason = "AI未配置" if any(text in message for text in ("Base URL", "API Key", "模型名称")) else "AI 暂不可用"
        trend.ai_category = "未分类"


def refresh_trends(session: Session) -> dict[str, Any]:
    """按平台抓取热榜；单平台失败跳过，不影响其他平台。"""
    global _ai_failure_reported
    _ai_failure_reported = False
    fetchers = {"bilibili": _bilibili_items, "zhihu": _zhihu_items, "weibo": _weibo_items}
    fetched_day = date.today().isoformat()
    results: list[dict[str, Any]] = []
    total_added = total_updated = 0
    for index, platform in enumerate(PLATFORMS):
        if index:
            time.sleep(PLATFORM_INTERVAL)
        try:
            items = fetchers[platform]()
        except TrendError as exc:
            optional = platform == "zhihu"
            message = "知乎需要登录，暂不可用" if optional else str(exc)
            _platform_status[platform] = {"available": False, "message": message}
            results.append({"platform": platform, "fetched": 0, "added": 0, "updated": 0, "error": message, "available": False, "optional": optional})
            continue
        added = updated = 0
        for item in items[:10]:
            trend = session.scalar(select(HotTrend).where(HotTrend.platform == platform, HotTrend.title == item["title"][:500], HotTrend.fetched_date == fetched_day))
            if trend is None:
                trend = HotTrend(platform=platform, title=item["title"][:500], url=item["url"][:1000], heat=item["heat"], fetched_date=fetched_day)
                session.add(trend)
                session.flush()
                _apply_ai(trend)
                added += 1
            else:
                trend.heat = item["heat"]
                trend.url = item["url"][:1000]
                if trend.ai_score is None:
                    _apply_ai(trend)
                updated += 1
        session.commit()
        total_added += added
        total_updated += updated
        _platform_status[platform] = {"available": True, "message": ""}
        results.append({"platform": platform, "fetched": len(items[:10]), "added": added, "updated": updated, "error": "", "available": True, "optional": platform == "zhihu"})
    return {"results": results, "total_added": total_added, "total_updated": total_updated}


def serialize_trend(trend: HotTrend) -> dict[str, Any]:
    score = trend.ai_score if trend.ai_score is not None else 50
    decision = "值得看" if score >= 70 else ("一般" if score >= 40 else "不值得看")
    return {
        "id": trend.id, "platform": trend.platform, "platform_name": PLATFORM_NAMES.get(trend.platform, trend.platform),
        "title": trend.title, "url": trend.url, "heat": trend.heat,
        "ai_score": score, "decision": decision, "ai_reason": trend.ai_reason,
        "ai_category": trend.ai_category, "user_feedback": trend.user_feedback,
        "note_id": trend.note_id, "content_fetched": bool(trend.content_fetched), "created_at": trend.created_at.isoformat() if trend.created_at else None,
        "fetched_date": trend.fetched_date,
    }


def list_trends(session: Session, platform: str = "", quality: str = "all", feedback: str = "") -> list[HotTrend]:
    statement = select(HotTrend)
    if platform in PLATFORMS:
        statement = statement.where(HotTrend.platform == platform)
    if quality == "worth":
        statement = statement.where(HotTrend.ai_score >= 70)
    if feedback:
        statement = statement.where(HotTrend.user_feedback == feedback)
    trends = list(session.scalars(statement).all())
    trends.sort(key=lambda item: (-(item.ai_score if item.ai_score is not None else 50), -item.heat, -item.id))
    return trends


def set_feedback(session: Session, trend_id: int, feedback: str) -> HotTrend:
    if feedback not in ("待确认", "值得看", "不值得看"):
        raise TrendError("反馈只能是 待确认、值得看 或 不值得看")
    trend = session.get(HotTrend, trend_id)
    if trend is None:
        raise TrendError("热点不存在")
    trend.user_feedback = feedback
    session.commit()
    session.refresh(trend)
    return trend


def get_stats(session: Session) -> dict[str, Any]:
    today = date.today().isoformat()
    return {
        "today_count": int(session.scalar(select(func.count()).select_from(HotTrend).where(HotTrend.fetched_date == today)) or 0),
        "worth_count": int(session.scalar(select(func.count()).select_from(HotTrend).where(HotTrend.fetched_date == today, HotTrend.ai_score >= 70)) or 0),
        "feedback_count": int(session.scalar(select(func.count()).select_from(HotTrend).where(HotTrend.user_feedback != "待确认")) or 0),
        "platform_status": {name: dict(status) for name, status in _platform_status.items()},
    }


def generate_note(session: Session, trend_id: int) -> tuple[Any, bool]:
    trend = session.get(HotTrend, trend_id)
    if trend is None:
        raise TrendError("热点不存在")
    if trend.note_id:
        try:
            return note_service.get_note_by_id(session, trend.note_id), False
        except note_service.NoteNotFound:
            trend.note_id = None

    article = jina_service.fetch_article_text(trend.url)
    platform_name = PLATFORM_NAMES.get(trend.platform, trend.platform)
    content_fetched = bool(article)
    if article:
        prompt = f"""请只根据下面的热点原文生成中文笔记，不能编造原文没有的信息。
只输出 JSON，不要 Markdown：{{"summary":"一句话概括","core_points":["核心内容"],"knowledge_points":["知识点"],"quotes":["金句"],"actions":["行动清单"]}}
标题：{trend.title}
平台：{platform_name}
原文：
{article[:8000]}
"""
        try:
            raw = ai_service.chat(prompt)
            payload = json.loads(re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip())
            note_data = {
                "summary": str(payload.get("summary") or "").strip()[:300],
                "core_points": [str(item).strip() for item in payload.get("core_points", []) if str(item).strip()][:12],
                "knowledge_points": [str(item).strip() for item in payload.get("knowledge_points", []) if str(item).strip()][:12],
                "quotes": [str(item).strip() for item in payload.get("quotes", []) if str(item).strip()][:8],
                "actions": [str(item).strip() for item in payload.get("actions", []) if str(item).strip()][:8],
            }
        except (ai_service.AiError, json.JSONDecodeError, TypeError, ValueError) as exc:
            logger.warning("热点笔记 AI 生成失败，使用原文摘要：%s", exc)
            note_data = {"summary": article[:200], "core_points": [article[:800]], "knowledge_points": [], "quotes": [], "actions": []}
    else:
        prompt = f"""请只根据下面的热榜标题生成简短中文笔记，不能编造新闻事实。
只输出 JSON，不要 Markdown：{{"summary":"一句话概括","core_points":["思考方向"],"knowledge_points":["知识点"],"quotes":[],"actions":["建议查看原文"]}}
标题：{trend.title}
平台：{platform_name}
热度：{trend.heat}
"""
        try:
            raw = ai_service.chat(prompt)
            payload = json.loads(re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip())
            note_data = {
                "summary": str(payload.get("summary") or trend.title).strip()[:300],
                "core_points": [str(item).strip() for item in payload.get("core_points", []) if str(item).strip()][:12],
                "knowledge_points": [str(item).strip() for item in payload.get("knowledge_points", []) if str(item).strip()][:12],
                "quotes": [str(item).strip() for item in payload.get("quotes", []) if str(item).strip()][:8],
                "actions": [str(item).strip() for item in payload.get("actions", []) if str(item).strip()][:8],
            }
        except (ai_service.AiError, json.JSONDecodeError, TypeError, ValueError) as exc:
            logger.warning("热点标题笔记 AI 生成失败：%s", exc)
            note_data = {"summary": trend.title, "core_points": ["信息有限，建议查看原文"], "knowledge_points": [], "quotes": [], "actions": ["阅读原文后再补充笔记"]}

    def bullet(items: list[str], default: str = "- 暂无") -> str:
        return "\n".join(f"- {item}" for item in items) or default

    lines = []
    if not content_fetched:
        lines.extend(["> ⚠️ 未获取到原文，基于标题生成。", ""])
    lines.extend([
        "## 一句话概括", note_data["summary"], "",
        "## 核心内容", bullet(note_data["core_points"]), "",
        "## 关键知识点", bullet(note_data["knowledge_points"]), "",
        "## 金句", bullet(note_data["quotes"]), "",
        "## 行动清单", bullet(note_data["actions"]), "",
        f"来源：{platform_name} {trend.url}",
    ])
    note = note_service.create_note(
        session, trend.title, "\n".join(lines), original_url=trend.url,
        tags=[trend.platform, "热点"], source="热点",
        category=trend.ai_category or platform_name,
    )
    trend.note_id = note.id
    trend.content_fetched = content_fetched
    session.commit()
    return note, True
