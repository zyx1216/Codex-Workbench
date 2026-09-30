# -*- coding: utf-8 -*-
"""Embedding 服务：火山方舟远端与本地 Chroma 模型统一入口。"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

import httpx

from services import ai_service

logger = logging.getLogger(__name__)

REMOTE_DIM = 2048
LOCAL_DIM = 384
EMBEDDING_BATCH_SIZE = 16
REMOTE_TIMEOUT = 30.0
LOCAL_MODEL_NAME = "all-MiniLM-L6-v2"

_local_embedder: Any = None


class EmbeddingError(Exception):
    """Embedding 调用失败，message 可直接展示给用户。"""


def _join_url(base_url: str, path: str) -> str:
    """拼接 OpenAI 兼容接口地址，避免出现重复斜杠。"""
    return f"{(base_url or '').rstrip('/')}{path}"


def get_mode(settings: dict[str, Any] | None = None) -> str:
    """返回当前向量模式：remote 或 local。"""
    settings = settings or ai_service.get_config()
    return "local" if bool(settings.get("use_local_embedding", False)) else "remote"


def get_signature(mode: str | None = None, settings: dict[str, Any] | None = None) -> str:
    """生成集合签名，用于阻止不同模型或维度混用。"""
    settings = settings or ai_service.get_config()
    mode = mode or get_mode(settings)
    if mode == "local":
        return f"local:{LOCAL_MODEL_NAME}:{LOCAL_DIM}"
    base_url = str(settings.get("embedding_base_url", "") or "").strip()
    model = str(settings.get("embedding_model", "") or "").strip()
    digest = hashlib.sha256(f"{base_url}|{model}".encode("utf-8")).hexdigest()[:16]
    return f"remote:{digest}:{REMOTE_DIM}"


def _validate_vectors(vectors: list[list[float]], dimension: int) -> list[list[float]]:
    """校验向量数量和维度，防止错误数据进入 Chroma。"""
    result: list[list[float]] = []
    for vector in vectors:
        if not isinstance(vector, list) or len(vector) != dimension:
            raise EmbeddingError(f"Embedding 维度不正确，预期 {dimension} 维")
        result.append([float(value) for value in vector])
    return result


def _embed_remote_batch(texts: list[str]) -> list[list[float]]:
    """调用一次远端 embedding 请求，单次不超过 16 条。"""
    settings = ai_service.get_config()
    base_url = str(settings.get("embedding_base_url", "") or "").strip()
    model = str(settings.get("embedding_model", "") or "").strip()
    api_key = ai_service.get_api_key()
    if not base_url:
        raise EmbeddingError("请先在设置页填写 Embedding Base URL")
    if not model:
        raise EmbeddingError("请先在设置页填写 Embedding 模型名称")
    if not api_key:
        raise EmbeddingError("请先在设置页填写 API Key")

    try:
        response = httpx.post(
            _join_url(base_url, "/embeddings"),
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": model, "input": texts},
            timeout=REMOTE_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
        items = payload.get("data")
        if not isinstance(items, list):
            raise ValueError("响应缺少 data 数组")
        items.sort(key=lambda item: item.get("index", 0))
        vectors = [item.get("embedding") for item in items]
        if len(vectors) != len(texts):
            raise ValueError("返回向量数量与输入不一致")
        return _validate_vectors(vectors, REMOTE_DIM)
    except EmbeddingError:
        raise
    except Exception as exc:  # noqa: BLE001 - httpx 和 JSON 异常类型不统一
        logger.warning("火山方舟 Embedding 调用失败：%s", exc)
        raise EmbeddingError(f"Embedding 调用失败：{exc}") from exc


def _get_local_embedder() -> Any:
    """延迟创建本地模型，首次调用时才加载依赖和模型。"""
    global _local_embedder
    if _local_embedder is not None:
        return _local_embedder
    try:
        from chromadb.utils.embedding_functions import DefaultEmbeddingFunction
    except ImportError as exc:
        raise EmbeddingError("当前环境缺少 ChromaDB，无法使用本地 Embedding") from exc
    _local_embedder = DefaultEmbeddingFunction()
    return _local_embedder


def _embed_local_batch(texts: list[str]) -> list[list[float]]:
    """调用本地 all-MiniLM-L6-v2，单次不超过 16 条。"""
    try:
        vectors = _get_local_embedder()(texts)
        return _validate_vectors([list(vector) for vector in vectors], LOCAL_DIM)
    except EmbeddingError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("本地 Embedding 调用失败：%s", exc)
        raise EmbeddingError(f"本地 Embedding 调用失败：{exc}") from exc


def embed_texts(texts: list[str], mode: str | None = None) -> list[list[float]]:
    """按当前模式批量生成向量，每批最多 16 条并保持输入顺序。"""
    if not texts:
        return []
    mode = mode or get_mode()
    batch_func = _embed_local_batch if mode == "local" else _embed_remote_batch
    vectors: list[list[float]] = []
    for start in range(0, len(texts), EMBEDDING_BATCH_SIZE):
        vectors.extend(batch_func(texts[start:start + EMBEDDING_BATCH_SIZE]))
    return vectors