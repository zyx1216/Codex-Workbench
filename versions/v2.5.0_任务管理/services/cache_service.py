# -*- coding: utf-8 -*-
"""本地 JSON 缓存服务，用于缓存 AI 改写和评估结果。"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import config

logger = logging.getLogger(__name__)
DEFAULT_EXPIRE_HOURS = 24 * 7


def _cache_file(key: str) -> Path:
    """按稳定 SHA256 生成缓存文件路径。"""
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return config.CACHE_DIR / f"{digest}.json"


def make_rewrite_url_key(url: str) -> str:
    """URL 改写缓存键。"""
    return f"rewrite_url|{(url or '').strip()}"


def make_rewrite_text_key(content: str) -> str:
    """文本改写缓存键，只取正文前 500 字。"""
    return f"rewrite_text|{(content or '')[:500]}"


def make_evaluate_key(note_id: int, content: str) -> str:
    """质量评估缓存键。"""
    return f"evaluate|{note_id}|{content or ''}"


def make_regenerate_key(note_id: int, style: str, content: str) -> str:
    """风格重生成缓存键。"""
    return f"regenerate|{note_id}|{style}|{content or ''}"


def get_cache(key: str) -> Any:
    """读取缓存；过期、损坏或不存在时返回 None。"""
    path = _cache_file(key)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        expire_at = datetime.fromisoformat(payload["expire_at"])
    except (OSError, json.JSONDecodeError, TypeError, KeyError, ValueError) as exc:
        logger.warning("缓存文件损坏，按未命中处理：%s", exc)
        return None
    if datetime.now() >= expire_at:
        try:
            path.unlink()
        except OSError as exc:
            logger.warning("过期缓存删除失败：%s", exc)
        return None
    return payload.get("value")


def set_cache(key: str, value: Any, expire_hours: int = DEFAULT_EXPIRE_HOURS) -> None:
    """写入缓存；失败只记录日志，不阻断业务。"""
    now = datetime.now()
    payload = {
        "key": key,
        "created_at": now.isoformat(timespec="seconds"),
        "expire_at": (now + timedelta(hours=max(1, expire_hours))).isoformat(timespec="seconds"),
        "value": value,
    }
    try:
        config.ensure_dirs()
        _cache_file(key).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as exc:
        logger.warning("缓存写入失败：%s", exc)


def delete_cache(key: str) -> None:
    """删除单个缓存。"""
    try:
        _cache_file(key).unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("缓存删除失败：%s", exc)


def _iter_cache_files() -> list[Path]:
    """列出全部缓存文件。"""
    if not config.CACHE_DIR.exists():
        return []
    return [path for path in config.CACHE_DIR.glob("*.json") if path.is_file()]


def clear_expired() -> int:
    """清理过期缓存，返回删除数量。"""
    removed = 0
    now = datetime.now()
    for path in _iter_cache_files():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if now >= datetime.fromisoformat(payload["expire_at"]):
                path.unlink()
                removed += 1
        except (OSError, json.JSONDecodeError, TypeError, KeyError, ValueError):
            # 损坏文件无法判断有效期，不主动删除，避免误删可修复内容
            continue
    return removed


def clear_all() -> int:
    """清空全部缓存，返回删除数量。"""
    files = _iter_cache_files()
    removed = 0
    for path in files:
        try:
            path.unlink()
            removed += 1
        except OSError as exc:
            logger.warning("缓存清空失败：%s", exc)
    return removed
