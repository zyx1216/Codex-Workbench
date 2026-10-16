# -*- coding: utf-8 -*-
"""B站 UP 主跟踪、视频抓取、AI 筛选与 Obsidian 笔记导出。"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode, urlparse

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import config
from models.models import BilibiliCreator, BilibiliVideo
from services import ai_service
from services.note_export_service import sanitize_filename

logger = logging.getLogger(__name__)

BILIBILI_API = "https://api.bilibili.com"
BILIBILI_SPACE = "https://space.bilibili.com"
BILIBILI_VIDEO = "https://www.bilibili.com/video"
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.bilibili.com",
}
REQUEST_TIMEOUT = 10.0
REQUEST_INTERVAL = 2.0
RATE_LIMIT_COOLDOWN = 3600.0
WBI_CACHE_TTL = 3600.0
FEEDBACK_LIMIT = 20
AI_REASON_LIMIT = 30
WBI_MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]

_refresh_lock = threading.Lock()
_request_lock = threading.Lock()
_last_request_at = 0.0
_rate_limited_until = 0.0
_wbi_cache: tuple[str, str, float] | None = None
_wbi_lock = threading.Lock()


class BilibiliError(Exception):
    """B站跟踪业务错误。"""

    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code


class BilibiliRateLimited(BilibiliError):
    """B站触发风控或限流。"""


def parse_creator_uid(value: str) -> str:
    """从 UID 或空间链接中解析 B站 UID。"""
    text = str(value or "").strip()
    if not text:
        raise BilibiliError("请输入UP主 UID 或空间链接")
    if text.isdigit():
        return text
    match = re.search(r"space\.bilibili\.com/(\d+)", text)
    if match:
        return match.group(1)
    raise BilibiliError("无法识别 UID，请粘贴纯数字 UID 或 space.bilibili.com 链接")


def _to_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _parse_publish_time(value: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(value))
    except (TypeError, ValueError, OSError):
        return None


def _parse_duration(value: Any) -> int:
    if isinstance(value, (int, float)):
        return max(0, int(value))
    text = str(value or "").strip()
    if not text:
        return 0
    if text.isdigit():
        return int(text)
    parts = text.split(":")
    try:
        numbers = [int(part) for part in parts]
    except ValueError:
        return 0
    if len(numbers) == 2:
        return numbers[0] * 60 + numbers[1]
    if len(numbers) == 3:
        return numbers[0] * 3600 + numbers[1] * 60 + numbers[2]
    return 0


def _throttle() -> None:
    """所有 B站请求串行执行，任意两次请求间隔至少 2 秒。"""
    global _last_request_at
    with _request_lock:
        now = time.monotonic()
        wait = REQUEST_INTERVAL - (now - _last_request_at)
        if wait > 0:
            time.sleep(wait)
        _last_request_at = time.monotonic()


def _ensure_not_rate_limited() -> None:
    if _rate_limited_until and time.monotonic() < _rate_limited_until:
        remaining = int(_rate_limited_until - time.monotonic())
        raise BilibiliRateLimited(f"B站接口暂时限流，请 {remaining} 秒后再试", code=-412)


def _set_rate_limited() -> None:
    global _rate_limited_until
    _rate_limited_until = time.monotonic() + RATE_LIMIT_COOLDOWN


def _get_json(
    url: str,
    params: dict[str, Any] | None = None,
    *,
    allow_codes: tuple[int, ...] = (0,),
    referer: str | None = None,
) -> dict[str, Any]:
    """带限速、日志和业务 code 检查的 B站 JSON 请求。"""
    _ensure_not_rate_limited()
    _throttle()
    headers = dict(REQUEST_HEADERS)
    if referer:
        headers["Referer"] = referer
    started = time.monotonic()
    try:
        response = httpx.get(url, params=params, headers=headers, timeout=REQUEST_TIMEOUT, follow_redirects=True)
        elapsed = time.monotonic() - started
        if response.status_code == 412:
            _set_rate_limited()
            logger.warning("B站接口限流 status=412 url=%s elapsed=%.2fs", response.url, elapsed)
            raise BilibiliRateLimited("B站接口暂时限流，稍后再试", code=-412)
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError as exc:
            logger.warning("B站接口 JSON 解析失败 url=%s status=%s body=%r", response.url, response.status_code, response.text[:300])
            raise BilibiliError(f"B站接口返回无法解析：{exc}") from exc
        code = payload.get("code") if isinstance(payload, dict) else None
        message = payload.get("message") if isinstance(payload, dict) else ""
        logger.info("B站接口返回 url=%s status=%s code=%s message=%s elapsed=%.2fs", response.url, response.status_code, code, message, elapsed)
        if code == -412:
            _set_rate_limited()
            raise BilibiliRateLimited("B站接口暂时限流，稍后再试", code=code)
        if code not in allow_codes:
            raise BilibiliError(f"B站接口返回错误：{message or code}", code=code)
        if not isinstance(payload, dict):
            raise BilibiliError("B站接口返回格式异常")
        return payload
    except BilibiliError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("B站接口请求失败 url=%s error=%s", url, exc)
        raise BilibiliError(f"B站接口请求失败：{exc}") from exc


def _get_html(url: str, referer: str | None = None) -> str:
    """获取 B站 HTML，用于 WBI 校验失败后的降级解析。"""
    _ensure_not_rate_limited()
    _throttle()
    headers = dict(REQUEST_HEADERS)
    if referer:
        headers["Referer"] = referer
    try:
        response = httpx.get(url, headers=headers, timeout=REQUEST_TIMEOUT, follow_redirects=True)
        if response.status_code == 412:
            _set_rate_limited()
            logger.warning("B站 HTML 页面限流 status=412 url=%s", url)
            raise BilibiliRateLimited("B站接口暂时限流，稍后再试", code=-412)
        response.raise_for_status()
        return response.text
    except BilibiliError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("B站 HTML 请求失败 url=%s error=%s", url, exc)
        raise BilibiliError(f"B站页面请求失败：{exc}") from exc


def _extract_wbi_key(url: str) -> str:
    name = Path(urlparse(str(url or "")).path).name
    return name.rsplit(".", 1)[0] if "." in name else name


def _mixin_key(img_key: str, sub_key: str) -> str:
    raw = f"{img_key}{sub_key}"
    return "".join(raw[index] for index in WBI_MIXIN_KEY_ENC_TAB)[:32]


def _get_wbi_keys(force: bool = False) -> tuple[str, str]:
    """获取并缓存 WBI 密钥，缓存 1 小时；-352 时可强制刷新。"""
    global _wbi_cache
    with _wbi_lock:
        now = time.monotonic()
        if not force and _wbi_cache and _wbi_cache[2] > now:
            return _wbi_cache[0], _wbi_cache[1]
    payload = _get_json(f"{BILIBILI_API}/x/web-interface/nav", allow_codes=(0, -101))
    data = payload.get("data") or {}
    wbi_img = data.get("wbi_img") or {}
    img_key = _extract_wbi_key(wbi_img.get("img_url", ""))
    sub_key = _extract_wbi_key(wbi_img.get("sub_url", ""))
    if not img_key or not sub_key:
        raise BilibiliError("B站未返回 WBI 密钥")
    with _wbi_lock:
        _wbi_cache = (img_key, sub_key, time.monotonic() + WBI_CACHE_TTL)
    logger.info("WBI 密钥已更新 img_key=%s sub_key=%s", img_key[:6], sub_key[:6])
    return img_key, sub_key


def _clear_wbi_cache() -> None:
    global _wbi_cache
    with _wbi_lock:
        _wbi_cache = None


def _sign_wbi_params(params: dict[str, Any]) -> dict[str, Any]:
    img_key, sub_key = _get_wbi_keys()
    signed = dict(params)
    signed["wts"] = int(time.time())
    cleaned = []
    for key, value in signed.items():
        text = re.sub(r"[!'()*]", "", str(value))
        cleaned.append((key, text))
    cleaned.sort(key=lambda item: item[0])
    query = urlencode(cleaned, quote_via=quote, safe="")
    signed["w_rid"] = hashlib.md5(f"{query}{_mixin_key(img_key, sub_key)}".encode("utf-8")).hexdigest()
    return signed


def _get_signed_json(url: str, params: dict[str, Any], referer: str) -> dict[str, Any]:
    try:
        return _get_json(url, _sign_wbi_params(params), allow_codes=(0,), referer=referer)
    except BilibiliError as exc:
        if exc.code != -352:
            raise
        logger.warning("WBI 签名校验失败，刷新密钥后重试一次：%s", exc)
        _clear_wbi_cache()
        return _get_json(url, _sign_wbi_params(params), allow_codes=(0,), referer=referer)


def fetch_creator_info(uid: str) -> dict[str, Any]:
    """获取 UP 主名称和头像，接口失败时降级解析空间页。"""
    try:
        payload = _get_json(f"{BILIBILI_API}/x/space/acc/info", params={"mid": uid}, referer=f"{BILIBILI_SPACE}/{uid}")
        data = payload.get("data") or {}
        return {"uid": uid, "name": str(data.get("name") or f"UP主{uid}")[:200], "face": str(data.get("face") or "")[:500]}
    except BilibiliRateLimited:
        raise
    except BilibiliError as exc:
        logger.info("UP主信息接口降级 HTML：%s", exc)
    html = _get_html(f"{BILIBILI_SPACE}/{uid}", referer=f"{BILIBILI_SPACE}/{uid}")
    soup = BeautifulSoup(html, "html.parser")
    title = ""
    meta_title = soup.find("meta", attrs={"property": "og:title"})
    if meta_title:
        title = str(meta_title.get("content") or "").strip()
    if not title and soup.title:
        title = soup.title.get_text(strip=True)
    face = ""
    meta_image = soup.find("meta", attrs={"property": "og:image"})
    if meta_image:
        face = str(meta_image.get("content") or "").strip()
    return {"uid": uid, "name": (title or f"UP主{uid}")[:200], "face": face[:500]}


def _video_from_search_item(item: dict[str, Any], uid: str) -> dict[str, Any]:
    bvid = str(item.get("bvid") or "").strip()
    return {
        "bvid": bvid,
        "uid": uid,
        "title": str(item.get("title") or "无标题")[:500],
        "description": str(item.get("description") or "")[:3000],
        "publish_time": _parse_publish_time(item.get("created") or item.get("pubdate")),
        "duration": _parse_duration(item.get("length") or item.get("duration")),
        "play": _to_int(item.get("play")),
        "like": _to_int(item.get("like")),
        "coin": _to_int(item.get("coin")),
        "favorite": _to_int(item.get("favorites") or item.get("favorite")),
        "comment": _to_int(item.get("comment") or item.get("review")),
        "share": _to_int(item.get("share")),
        "cover": str(item.get("pic") or "")[:500],
    }


def _videos_from_html(uid: str) -> list[dict[str, Any]]:
    """WBI 仍失败时，从空间页提取最近视频标题和 BV 号。"""
    html = _get_html(f"{BILIBILI_SPACE}/{uid}/video", referer=f"{BILIBILI_SPACE}/{uid}")
    soup = BeautifulSoup(html, "html.parser")
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for link in soup.find_all("a", href=True):
        match = re.search(r"(BV[0-9A-Za-z]+)", link.get("href", ""))
        if not match:
            continue
        bvid = match.group(1)
        if bvid in seen:
            continue
        title = link.get_text(" ", strip=True)
        if not title:
            continue
        seen.add(bvid)
        items.append({"bvid": bvid, "uid": uid, "title": title[:500], "description": "", "publish_time": None, "duration": 0, "play": 0, "like": 0, "coin": 0, "favorite": 0, "comment": 0, "share": 0, "cover": ""})
        if len(items) >= 10:
            break
    return items


def fetch_creator_videos(uid: str) -> list[dict[str, Any]]:
    """获取 UP 主最近视频：WBI 签名优先，失败降级 HTML。"""
    try:
        payload = _get_signed_json(
            f"{BILIBILI_API}/x/space/wbi/arc/search",
            {"mid": uid, "ps": 10, "pn": 1, "order": "pubdate"},
            referer=f"{BILIBILI_SPACE}/{uid}",
        )
        data = payload.get("data") or {}
        video_list = ((data.get("list") or {}).get("vlist")) or []
        return [_video_from_search_item(item, uid) for item in video_list if isinstance(item, dict) and item.get("bvid")]
    except BilibiliRateLimited:
        raise
    except BilibiliError as exc:
        logger.warning("WBI 接口失败，降级 HTML：%s", exc)
    try:
        return _videos_from_html(uid)
    except BilibiliRateLimited:
        raise
    except BilibiliError as exc:
        logger.warning("HTML 降级也失败，本轮无新增视频：%s", exc)
        return []


def fetch_video_detail(bvid: str, fallback: dict[str, Any]) -> dict[str, Any]:
    """补充单个视频的统计信息，详情失败时保留列表数据。"""
    try:
        payload = _get_json(f"{BILIBILI_API}/x/web-interface/view", params={"bvid": bvid}, referer=f"{BILIBILI_VIDEO}/{bvid}/")
        data = payload.get("data") or {}
        stat = data.get("stat") or {}
        result = dict(fallback)
        result.update({
            "bvid": bvid,
            "title": str(data.get("title") or fallback.get("title") or "无标题")[:500],
            "description": str(data.get("desc") or fallback.get("description") or "")[:3000],
            "publish_time": _parse_publish_time(data.get("pubdate")) or fallback.get("publish_time"),
            "duration": _parse_duration(data.get("duration")) or fallback.get("duration", 0),
            "play": _to_int(stat.get("view") or fallback.get("play")),
            "like": _to_int(stat.get("like") or fallback.get("like")),
            "coin": _to_int(stat.get("coin") or fallback.get("coin")),
            "favorite": _to_int(stat.get("favorite") or fallback.get("favorite")),
            "comment": _to_int(stat.get("reply") or fallback.get("comment")),
            "share": _to_int(stat.get("share") or fallback.get("share")),
            "cover": str(data.get("pic") or fallback.get("cover") or "")[:500],
        })
        return result
    except BilibiliRateLimited:
        raise
    except BilibiliError as exc:
        logger.info("视频详情失败，使用列表数据：%s", exc)
        return fallback


def serialize_creator(creator: BilibiliCreator, session: Session | None = None) -> dict[str, Any]:
    video_count = 0
    if session is not None:
        video_count = int(session.scalar(select(func.count()).select_from(BilibiliVideo).where(BilibiliVideo.uid == creator.uid)) or 0)
    return {
        "id": creator.id,
        "uid": creator.uid,
        "name": creator.name,
        "face": creator.face,
        "added_at": creator.added_at.isoformat() if creator.added_at else None,
        "last_check_at": creator.last_check_at.isoformat() if creator.last_check_at else None,
        "enabled": bool(creator.enabled),
        "video_count": video_count,
    }


def serialize_video(video: BilibiliVideo) -> dict[str, Any]:
    like_rate = (video.like / video.play) if video.play else 0.0
    return {
        "id": video.id,
        "bvid": video.bvid,
        "uid": video.uid,
        "title": video.title,
        "description": video.description,
        "publish_time": video.publish_time.isoformat() if video.publish_time else None,
        "duration": video.duration,
        "play": video.play,
        "like": video.like,
        "coin": video.coin,
        "favorite": video.favorite,
        "comment": video.comment,
        "share": video.share,
        "cover": video.cover,
        "url": f"{BILIBILI_VIDEO}/{video.bvid}/",
        "ai_score": video.ai_score,
        "ai_decision": video.ai_decision,
        "ai_reason": video.ai_reason,
        "category": video.category,
        "user_feedback": video.user_feedback,
        "popularity_tag": video.popularity_tag,
        "popularity_reason": video.popularity_reason,
        "like_rate": round(like_rate, 4),
        "note_exported": bool(video.note_exported),
        "exported_path": video.exported_path or "",
        "created_at": video.created_at.isoformat() if video.created_at else None,
    }


def list_creators(session: Session) -> list[BilibiliCreator]:
    return list(session.scalars(select(BilibiliCreator).order_by(BilibiliCreator.added_at.desc(), BilibiliCreator.id.desc())).all())


def get_creator(session: Session, creator_id: int) -> BilibiliCreator:
    creator = session.get(BilibiliCreator, creator_id)
    if creator is None:
        raise BilibiliError("UP主不存在")
    return creator


def add_creator(session: Session, value: str) -> dict[str, Any]:
    """添加 UP 主并立即尝试抓取最近视频。"""
    uid = parse_creator_uid(value)
    creator = session.scalar(select(BilibiliCreator).where(BilibiliCreator.uid == uid))
    created = creator is None
    try:
        info = fetch_creator_info(uid)
    except BilibiliError as exc:
        logger.warning("添加UP主时信息接口失败，使用占位名称：%s", exc)
        info = {"uid": uid, "name": f"UP主{uid}", "face": ""}
    if creator is None:
        creator = BilibiliCreator(uid=uid, name=info["name"], face=info["face"], enabled=True)
        session.add(creator)
        session.commit()
        session.refresh(creator)
    else:
        creator.name = info["name"] or creator.name
        creator.face = info["face"] or creator.face
        session.commit()
    refresh_result = refresh_creator(session, creator)
    return {
        "creator": serialize_creator(creator),
        "created": created,
        "refresh": refresh_result,
    }


def delete_creator(session: Session, creator_id: int) -> None:
    creator = get_creator(session, creator_id)
    session.delete(creator)
    session.commit()


def _recalculate_popularity(session: Session, uid: str) -> None:
    """按同 UP 最近 10 条平均播放量重算爆款标签。"""
    videos = list(session.scalars(
        select(BilibiliVideo)
        .where(BilibiliVideo.uid == uid)
        .order_by(BilibiliVideo.publish_time.desc(), BilibiliVideo.id.desc())
        .limit(10)
    ).all())
    if not videos:
        return
    average = sum(video.play for video in videos) / len(videos)
    for video in videos:
        ratio = (video.play / average) if average else 0.0
        like_rate = (video.like / video.play) if video.play else 0.0
        if ratio > 2 and like_rate > 0.03:
            tag = "爆款"
            reason = f"播放量超过同UP主近10条平均的 {int((ratio - 1) * 100)}%，点赞率 {like_rate * 100:.1f}%"
        elif ratio > 1.5:
            tag = "有潜力"
            reason = f"播放量超过同UP主近10条平均的 {int((ratio - 1) * 100)}%"
        elif ratio < 0.5:
            tag = "低迷"
            reason = f"播放量低于同UP主近10条平均的 {int((1 - ratio) * 100)}%"
        else:
            tag = "正常"
            reason = f"播放量约为同UP主近10条平均的 {int(ratio * 100)}%"
        video.popularity_tag = tag
        video.popularity_reason = reason[:200]
    session.commit()


def _feedback_examples(session: Session) -> list[str]:
    rows = list(session.scalars(
        select(BilibiliVideo)
        .where(BilibiliVideo.user_feedback != "待确认")
        .order_by(BilibiliVideo.id.desc())
        .limit(FEEDBACK_LIMIT)
    ).all())
    return [f"{item.title} => {item.user_feedback}" for item in rows]


def _fallback_filter(video: dict[str, Any]) -> dict[str, Any]:
    play = _to_int(video.get("play"))
    like = _to_int(video.get("like"))
    score = min(95, 45 + min(play // 10000, 25) + min(like // 1000, 20))
    decision = "值得看" if score >= 70 else ("一般" if score >= 40 else "不值得看")
    return {
        "score": score,
        "decision": decision,
        "reason": "AI 暂不可用，按播放和点赞数据暂估",
        "category": "未分类",
    }


def apply_ai_filter(session: Session, video: BilibiliVideo, creator_name: str) -> dict[str, Any]:
    """调用 AI 判断视频是否值得看，失败时保留可用的启发式结果。"""
    examples = _feedback_examples(session)
    example_text = "\n".join(examples) if examples else "暂无"
    prompt = f"""你是一个内容筛选助手，根据视频标题和数据判断值不值得花时间看。
目标用户是想学习AI、编程、自我提升的人。
标题党、夸大其词的扣分；有实操教程、干货内容的加分。
请只输出 JSON，不要 Markdown，不要解释。格式：
{{"score": 0, "decision": "值得看", "reason": "不超过30字的理由", "category": "AI教程"}}
score 为 0-100 分；decision 只能是 值得看、一般、不值得看。
用户以往反馈示例：
{example_text}

UP主：{creator_name}
标题：{video.title}
简介：{video.description[:1000]}
播放量：{video.play}
点赞数：{video.like}
"""
    try:
        raw = ai_service.chat(prompt)
        cleaned = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
        payload = json.loads(cleaned)
        score = max(0, min(100, int(payload.get("score", 50))))
        decision = str(payload.get("decision") or "").strip()
        if decision not in ("值得看", "一般", "不值得看"):
            decision = "值得看" if score >= 70 else ("一般" if score >= 40 else "不值得看")
        result = {
            "score": score,
            "decision": decision,
            "reason": str(payload.get("reason") or "").strip()[:AI_REASON_LIMIT],
            "category": str(payload.get("category") or "未分类").strip()[:50] or "未分类",
        }
    except (ai_service.AiError, json.JSONDecodeError, TypeError, ValueError) as exc:
        logger.warning("视频 AI 筛选失败，使用启发式结果：%s", exc)
        result = _fallback_filter({"play": video.play, "like": video.like})
    video.ai_score = result["score"]
    video.ai_decision = result["decision"]
    video.ai_reason = result["reason"]
    video.category = result["category"]
    session.commit()
    return result


def refresh_creator(session: Session, creator: BilibiliCreator) -> dict[str, Any]:
    """刷新单个 UP 主的视频；单个接口失败只记录，不让整个页面崩。"""
    added = 0
    updated = 0
    errors: list[str] = []
    try:
        info = fetch_creator_info(creator.uid)
        creator.name = info["name"] or creator.name
        creator.face = info["face"] or creator.face
    except BilibiliRateLimited as exc:
        creator.last_check_at = datetime.now()
        session.commit()
        return {"added": 0, "updated": 0, "error": str(exc), "rate_limited": True}
    except BilibiliError as exc:
        errors.append(str(exc))

    try:
        videos = fetch_creator_videos(creator.uid)
    except BilibiliRateLimited as exc:
        logger.warning("UP主 %s 视频刷新限流：%s", creator.uid, exc)
        creator.last_check_at = datetime.now()
        session.commit()
        return {"added": 0, "updated": 0, "error": str(exc), "rate_limited": True}
    except BilibiliError as exc:
        logger.warning("UP主 %s 视频刷新失败：%s", creator.uid, exc)
        creator.last_check_at = datetime.now()
        session.commit()
        return {"added": 0, "updated": 0, "error": str(exc), "rate_limited": False}

    existing = {
        item.bvid: item
        for item in session.scalars(select(BilibiliVideo).where(BilibiliVideo.uid == creator.uid)).all()
    }
    new_videos: list[BilibiliVideo] = []
    for item in videos:
        bvid = item.get("bvid")
        if not bvid:
            continue
        video = existing.get(bvid)
        if video is None:
            detail = fetch_video_detail(bvid, item)
            video = BilibiliVideo(
                bvid=bvid,
                uid=creator.uid,
                title=detail["title"],
                description=detail["description"],
                publish_time=detail["publish_time"],
                duration=detail["duration"],
                play=detail["play"],
                like=detail["like"],
                coin=detail["coin"],
                favorite=detail["favorite"],
                comment=detail["comment"],
                share=detail["share"],
                cover=detail["cover"],
            )
            session.add(video)
            new_videos.append(video)
            added += 1
        else:
            for key in ("title", "description", "publish_time", "duration", "play", "like", "coin", "favorite", "comment", "share", "cover"):
                if item.get(key) is not None:
                    setattr(video, key, item[key])
            updated += 1
    creator.last_check_at = datetime.now()
    session.commit()
    _recalculate_popularity(session, creator.uid)
    for video in new_videos:
        try:
            apply_ai_filter(session, video, creator.name)
        except Exception as exc:  # noqa: BLE001 - 单条 AI 失败不影响入库
            logger.warning("视频 AI 筛选异常 %s：%s", video.bvid, exc)
    return {"added": added, "updated": updated, "error": "；".join(errors), "rate_limited": False}


def _refresh_creator_by_id(creator_id: int) -> dict[str, Any]:
    """在线程内使用独立 Session 刷新单个 UP 主。"""
    from services.db import SessionLocal

    with SessionLocal() as session:
        creator = session.get(BilibiliCreator, creator_id)
        if creator is None:
            return {"creator_id": creator_id, "creator": None, "result": {"added": 0, "updated": 0, "error": "UP主不存在", "rate_limited": False}}
        result = refresh_creator(session, creator)
        return {"creator_id": creator_id, "creator": serialize_creator(creator, session), "result": result}


def refresh_creators(session: Session, ids: list[int] | None = None) -> dict[str, Any]:
    """刷新全部或指定 UP 主；全量最多 2 并发，指定 ID 严格串行。"""
    if not _refresh_lock.acquire(blocking=False):
        raise BilibiliError("已有刷新任务正在执行")

    try:
        selected_ids = [int(item) for item in (ids or []) if int(item) > 0]
        if selected_ids:
            selected_ids = list(dict.fromkeys(selected_ids))
            results: list[dict[str, Any]] = []
            for index, creator_id in enumerate(selected_ids):
                if index:
                    time.sleep(1.0)
                results.append(_refresh_creator_by_id(creator_id))
        else:
            creator_ids = list(session.scalars(select(BilibiliCreator.id).where(BilibiliCreator.enabled == True)).all())  # noqa: E712
            with ThreadPoolExecutor(max_workers=2, thread_name_prefix="bili-refresh") as pool:
                results = list(pool.map(_refresh_creator_by_id, creator_ids))

        total_added = sum(int(item.get("result", {}).get("added", 0)) for item in results)
        failed = sum(1 for item in results if item.get("result", {}).get("error"))
        rate_limited = any(bool(item.get("result", {}).get("rate_limited")) for item in results)
        retry_after = 0
        return {
            "results": results,
            "total_added": total_added,
            "failed": failed,
            "rate_limited": rate_limited,
            "retry_after_seconds": retry_after,
        }
    finally:
        _refresh_lock.release()


def refresh_all(session: Session) -> dict[str, Any]:
    """兼容旧入口：刷新全部 UP 主。"""
    return refresh_creators(session)


def list_videos(session: Session, category: str = "", decision: str = "", creator_uid: str = "") -> list[BilibiliVideo]:
    statement = select(BilibiliVideo)
    if category:
        statement = statement.where(BilibiliVideo.category == category)
    if decision:
        statement = statement.where(BilibiliVideo.ai_decision == decision)
    if creator_uid:
        statement = statement.where(BilibiliVideo.uid == creator_uid)
    videos = list(session.scalars(statement).all())
    videos.sort(key=lambda item: (-(item.ai_score if item.ai_score is not None else -1), -(item.publish_time.timestamp() if item.publish_time else 0), -item.id))
    return videos


def get_video(session: Session, video_id: int) -> BilibiliVideo:
    video = session.get(BilibiliVideo, video_id)
    if video is None:
        raise BilibiliError("视频不存在")
    return video


def set_feedback(session: Session, video_id: int, feedback: str) -> BilibiliVideo:
    if feedback not in ("待确认", "值得看", "不值得看"):
        raise BilibiliError("反馈只能是 待确认、值得看 或 不值得看")
    video = get_video(session, video_id)
    video.user_feedback = feedback
    session.commit()
    session.refresh(video)
    return video


def get_stats(session: Session) -> dict[str, int]:
    today = date.today()
    creators = int(session.scalar(select(func.count()).select_from(BilibiliCreator)) or 0)
    today_videos = int(session.scalar(select(func.count()).select_from(BilibiliVideo).where(func.date(BilibiliVideo.created_at) == today.isoformat())) or 0)
    worth = int(session.scalar(select(func.count()).select_from(BilibiliVideo).where(BilibiliVideo.ai_decision == "值得看")) or 0)
    feedback = int(session.scalar(select(func.count()).select_from(BilibiliVideo).where(BilibiliVideo.user_feedback != "待确认")) or 0)
    return {"creators": creators, "today_videos": today_videos, "worth_watching": worth, "feedback_count": feedback}


def _yaml_text(value: Any) -> str:
    return str(value or "").replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").strip()


def _fallback_summary(video: BilibiliVideo) -> dict[str, Any]:
    description = (video.description or "").strip()
    return {"summary": (description[:200] or video.title), "core_points": [description[:500]] if description else [video.title], "knowledge_points": [video.category or "视频内容"]}


def _ai_summary(video: BilibiliVideo, creator_name: str) -> dict[str, Any]:
    prompt = f"""请根据下面的视频标题和简介，生成适合 Obsidian 的通俗笔记结构。
只输出 JSON，不要 Markdown，不要解释。格式：
{{"summary":"一句话概括","core_points":["要点1","要点2"],"knowledge_points":["知识点1","知识点2"]}}
视频标题：{video.title}
UP主：{creator_name}
简介：{video.description[:2000]}
"""
    try:
        raw = ai_service.chat(prompt)
        cleaned = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
        payload = json.loads(cleaned)
        return {
            "summary": str(payload.get("summary") or "").strip()[:300],
            "core_points": [str(item).strip() for item in payload.get("core_points", []) if str(item).strip()][:10],
            "knowledge_points": [str(item).strip() for item in payload.get("knowledge_points", []) if str(item).strip()][:10],
        }
    except (ai_service.AiError, json.JSONDecodeError, TypeError, ValueError) as exc:
        logger.warning("视频笔记 AI 整理失败，使用简介兜底：%s", exc)
        return _fallback_summary(video)


def export_video(session: Session, video_id: int) -> tuple[BilibiliVideo, Path]:
    """生成 Obsidian Markdown 文件并记录导出状态。"""
    video = get_video(session, video_id)
    creator = session.scalar(select(BilibiliCreator).where(BilibiliCreator.uid == video.uid))
    creator_name = creator.name if creator else f"UP主{video.uid}"
    note = _ai_summary(video, creator_name)
    publish = video.publish_time.strftime("%Y-%m-%d") if video.publish_time else date.today().isoformat()
    base_title = sanitize_filename((video.title or "无标题")[:30]) or "无标题"
    safe_up = sanitize_filename(creator_name) or f"UP主{video.uid}"
    file_name = f"{publish}_【{safe_up}】{base_title}.md"
    config.ensure_dirs()
    output_path = config.OBSIDIAN_NOTES_DIR / file_name

    core_points = "\n".join(f"- {item}" for item in note["core_points"]) or "- 暂无"
    knowledge_points = "\n".join(f"- {item}" for item in note["knowledge_points"]) or "- 暂无"
    feedback_text = video.user_feedback if video.user_feedback != "待确认" else "（用户手动写，预留位置）"
    lines = [
        "---",
        f"date: {publish}",
        "source: bilibili",
        f'up: "{_yaml_text(creator_name)}"',
        f"bvid: {video.bvid}",
        f"url: {BILIBILI_VIDEO}/{video.bvid}/",
        f"publish_time: {publish}",
        f"play: {video.play}",
        f"like: {video.like}",
        f"ai_score: {video.ai_score if video.ai_score is not None else ''}",
        f'ai_decision: "{_yaml_text(video.ai_decision)}"',
        f'category: "{_yaml_text(video.category)}"',
        "---",
        "",
        f"# {video.title}",
        "",
        "## 一句话概括",
        note["summary"],
        "",
        "## 核心内容",
        core_points,
        "",
        "## 关键知识点",
        knowledge_points,
        "",
        "## 视频数据",
        f"- 播放量：{video.play}",
        f"- 点赞：{video.like}",
        f"- 评论：{video.comment}",
        f"- 转发：{video.share}",
        f"- 爆款标签：{video.popularity_tag}（{video.popularity_reason}）",
        "",
        "## 我的反馈",
        feedback_text,
        "",
    ]
    output_path.write_text("\n".join(lines), encoding="utf-8")
    video.note_exported = True
    video.exported_path = str(output_path)
    session.commit()
    session.refresh(video)
    return video, output_path


def export_videos(session: Session, video_ids: list[int]) -> dict[str, Any]:
    """批量导出视频，返回成功和失败明细。"""
    ids = list(dict.fromkeys(int(item) for item in video_ids))
    if not ids:
        raise BilibiliError("请选择要导出的视频")
    exported: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for video_id in ids:
        try:
            video, path = export_video(session, video_id)
            exported.append({"id": video.id, "title": video.title, "path": str(path)})
        except BilibiliError as exc:
            failed.append({"id": video_id, "error": str(exc)})
        except OSError as exc:
            logger.exception("视频笔记文件写入失败")
            failed.append({"id": video_id, "error": f"文件写入失败：{exc}"})
    return {"exported": exported, "failed": failed}
