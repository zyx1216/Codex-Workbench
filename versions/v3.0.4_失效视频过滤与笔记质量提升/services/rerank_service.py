# -*- coding: utf-8 -*-
"""Rerank 服务：调用火山方舟 rerank 接口，失败时保持原顺序。"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from services import ai_service

logger = logging.getLogger(__name__)
RERANK_TIMEOUT = 5.0


def _join_url(base_url: str, path: str) -> str:
    """拼接 rerank 地址，兼容以 /api/v3 结尾的 Base URL。"""
    return f"{(base_url or '').rstrip('/')}{path}"


def is_enabled(settings: dict[str, Any] | None = None) -> bool:
    """只有显式开启且配置完整时才调用 rerank。"""
    settings = settings or ai_service.get_config()
    return bool(settings.get("rerank_enabled", False)) and bool(
        str(settings.get("rerank_model", "") or "").strip()
    )


def rerank_documents(
    query: str,
    documents: list[dict[str, Any]],
    top_n: int = 5,
) -> list[dict[str, Any]]:
    """对候选文档重排序；任何异常都返回原顺序的前 top_n 条。"""
    if not documents:
        return []
    top_n = max(1, int(top_n))
    settings = ai_service.get_config()
    if not is_enabled(settings):
        return documents[:top_n]

    base_url = str(settings.get("embedding_base_url", "") or "").strip()
    model = str(settings.get("rerank_model", "") or "").strip()
    api_key = ai_service.get_api_key()
    if not base_url or not model or not api_key:
        return documents[:top_n]

    payload = {
        "model": model,
        "query": query,
        "documents": [str(item.get("text") or "") for item in documents],
        "top_n": top_n,
    }
    try:
        response = httpx.post(
            _join_url(base_url, "/rerank"),
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
            timeout=RERANK_TIMEOUT,
        )
        response.raise_for_status()
        body = response.json()
        results = body.get("results")
        if not isinstance(results, list):
            raise ValueError("响应缺少 results 数组")

        ranked: list[tuple[int, dict[str, Any]]] = []
        seen: set[int] = set()
        for item in results:
            try:
                index = int(item.get("index"))
            except (TypeError, ValueError):
                continue
            if index < 0 or index >= len(documents) or index in seen:
                continue
            score = item.get("relevance_score", item.get("score", 0.0))
            try:
                score = float(score)
            except (TypeError, ValueError):
                score = 0.0
            enriched = dict(documents[index])
            enriched["rerank_score"] = score
            enriched["score"] = score
            ranked.append((index, enriched))
            seen.add(index)
        if not ranked:
            return documents[:top_n]
        ranked.sort(key=lambda pair: (-float(pair[1].get("rerank_score", 0.0)), pair[0]))
        return [item for _, item in ranked[:top_n]]
    except Exception as exc:  # noqa: BLE001 - 重排失败必须降级，不影响 RAG
        logger.warning("Rerank 调用失败，沿用向量顺序：%s", exc)
        return documents[:top_n]