# -*- coding: utf-8 -*-
"""Jina Reader 原文抓取服务。"""

from __future__ import annotations

import logging
import time

import httpx

logger = logging.getLogger(__name__)
JINA_PREFIX = "https://r.jina.ai/"
JINA_TIMEOUT = 15.0
MAX_TEXT_LENGTH = 8000


def fetch_article_text(url: str) -> str:
    """通过 Jina Reader 获取 Markdown 正文；失败或为空时返回空字符串。"""
    url = str(url or "").strip()
    if not url:
        return ""
    started = time.monotonic()
    target = f"{JINA_PREFIX}{url}"
    try:
        response = httpx.get(
            target,
            headers={"Accept": "text/plain"},
            timeout=JINA_TIMEOUT,
            follow_redirects=True,
        )
        response.raise_for_status()
        text = response.text.strip()
        if len(text) > MAX_TEXT_LENGTH:
            text = text[:MAX_TEXT_LENGTH].rstrip()
            text += "\n\n内容过长已截断"
        elapsed = time.monotonic() - started
        logger.info("Jina Reader 抓取成功 url=%s length=%s elapsed=%.2fs", url, len(text), elapsed)
        return text
    except Exception as exc:  # noqa: BLE001 - Jina 失败必须静默降级
        logger.warning("Jina Reader 抓取失败 url=%s elapsed=%.2fs error=%s", url, time.monotonic() - started, exc)
        return ""
