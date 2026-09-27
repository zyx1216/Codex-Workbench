# -*- coding: utf-8 -*-
"""
向量库服务：使用 ChromaDB 持久化保存笔记向量。

- 默认本地 embedding：all-MiniLM-L6-v2
- 向量文件：data/vectordb/
- 向量库故障只抛出中文 VectorError，由调用方决定是否降级
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Any

import config
from sqlalchemy import select

logger = logging.getLogger(__name__)

COLLECTION_NAME = "notes"
CONTENT_LIMIT = 2000
BATCH_SIZE = 50

# 同步状态和锁：状态锁保护内存字段，同步锁防止两批任务同时写 Chroma
_state_lock = threading.Lock()
_sync_lock = threading.Lock()
_sync_state: dict[str, Any] = {
    "status": "idle",  # idle/running/done/failed
    "progress": 0,
    "total": 0,
    "success": 0,
    "failed": 0,
    "last_sync": None,
    "error": "",
}


class VectorError(Exception):
    """向量库操作失败，message 为可直接展示的中文信息。"""


def _set_state(**values: Any) -> None:
    """更新内存同步状态。"""
    with _state_lock:
        _sync_state.update(values)


def _get_collection() -> Any:
    """创建或获取 Chroma collection；延迟初始化，避免导入模块就下载模型。"""
    try:
        import chromadb
        from chromadb.utils.embedding_functions import DefaultEmbeddingFunction
    except ImportError as exc:
        raise VectorError("当前环境缺少 ChromaDB，请先安装 chromadb") from exc

    try:
        config.ensure_dirs()
        client = chromadb.PersistentClient(path=str(config.VECTOR_DB_DIR))
        return client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
            embedding_function=DefaultEmbeddingFunction(),
        )
    except Exception as exc:  # noqa: BLE001 - Chroma 异常类型随版本变化
        logger.warning("向量库初始化失败：%s", exc)
        raise VectorError(f"向量库初始化失败：{exc}") from exc


def _document_text(title: str, content: str, tags: list[str]) -> str:
    """拼接入库文本：标题、标签、正文前 2000 字。"""
    tag_text = ", ".join(tags or [])
    short_content = (content or "")[:CONTENT_LIMIT]
    return f"标题：{title or '无标题'}\n标签：{tag_text}\n正文：\n{short_content}"


def add_note(note_id: int, title: str, content: str, tags: list[str]) -> None:
    """新增单条笔记向量。"""
    collection = _get_collection()
    try:
        collection.add(
            ids=[str(note_id)],
            documents=[_document_text(title, content, tags)],
            metadatas=[{
                "title": title or "无标题",
                "tags": ", ".join(tags or []),
            }],
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("笔记向量写入失败：%s", exc)
        raise VectorError(f"笔记向量写入失败：{exc}") from exc


def delete_note(note_id: int) -> None:
    """按笔记 ID 删除向量；向量库异常由调用方降级。"""
    collection = _get_collection()
    try:
        collection.delete(ids=[str(note_id)])
    except Exception as exc:  # noqa: BLE001
        logger.warning("笔记向量删除失败：%s", exc)
        raise VectorError(f"笔记向量删除失败：{exc}") from exc


def update_note(note_id: int, title: str, content: str, tags: list[str]) -> None:
    """更新向量：先删后加。"""
    # 旧向量不存在时删除通常不报错；真报错仍交给上层降级
    delete_note(note_id)
    add_note(note_id, title, content, tags)


def _score_from_distance(distance: Any) -> float:
    """Chroma cosine distance 转相关度，并裁剪到 0 到 1。"""
    try:
        score = 1 - float(distance)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, score))


def search_notes(query: str, n_results: int = 10) -> list[dict[str, Any]]:
    """语义搜索，返回笔记 ID 和相关度。"""
    query = (query or "").strip()
    if not query:
        raise VectorError("搜索内容不能为空")

    collection = _get_collection()
    try:
        result = collection.query(
            query_texts=[query],
            n_results=max(1, min(n_results, 50)),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("语义搜索失败：%s", exc)
        raise VectorError(f"语义搜索失败：{exc}") from exc

    ids = result.get("ids", [[]])[0]
    distances = result.get("distances", [[]])[0]
    items: list[dict[str, Any]] = []
    for index, raw_id in enumerate(ids):
        try:
            note_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        distance = distances[index] if index < len(distances) else 1
        items.append({"id": note_id, "score": _score_from_distance(distance)})
    return items


def _sync_all_notes() -> dict[str, int]:
    """立即全量同步，可重复执行；返回总数、成功数、失败数。"""
    if not _sync_lock.acquire(blocking=False):
        raise VectorError("已有同步任务正在执行")

    # 延迟导入，避免模块加载阶段循环依赖
    from services.db import SessionLocal
    from models.models import Note

    _set_state(
        status="running", progress=0, total=0, success=0,
        failed=0, error="",
    )
    total = success = failed = 0
    try:
        collection = _get_collection()
        with SessionLocal() as session:
            notes = list(session.scalars(select(Note).order_by(Note.id)).all())
        total = len(notes)
        _set_state(total=total)

        for start in range(0, total, BATCH_SIZE):
            batch = notes[start:start + BATCH_SIZE]
            ids: list[str] = []
            documents: list[str] = []
            metadatas: list[dict[str, str]] = []
            for note in batch:
                tags = _safe_tags(note.tags)
                ids.append(str(note.id))
                documents.append(_document_text(note.title, note.content, tags))
                metadatas.append({
                    "title": note.title or "无标题",
                    "tags": ", ".join(tags),
                })
            try:
                collection.upsert(ids=ids, documents=documents, metadatas=metadatas)
                success += len(batch)
            except Exception as exc:  # noqa: BLE001
                failed += len(batch)
                logger.warning("批量同步向量失败：%s", exc)
                _set_state(error=f"部分笔记同步失败：{exc}")
            _set_state(progress=success + failed, success=success, failed=failed)

        _set_state(
            status="failed" if failed else "done",
            last_sync=datetime.now().isoformat(timespec="seconds"),
        )
        return {"total": total, "success": success, "failed": failed}
    except Exception as exc:
        message = str(exc) if isinstance(exc, VectorError) else f"全量同步失败：{exc}"
        _set_state(status="failed", error=message, last_sync=datetime.now().isoformat(timespec="seconds"))
        raise VectorError(message) from exc
    finally:
        _sync_lock.release()


def start_background_sync() -> dict[str, str]:
    """后台启动全量同步；已有任务时抛 VectorError。"""
    with _state_lock:
        if _sync_state["status"] == "running":
            raise VectorError("已有同步任务正在执行")

    worker = threading.Thread(target=_sync_all_notes, daemon=True)
    worker.start()
    return {"status": "running"}


def sync_all_notes() -> dict[str, int]:
    """对外暴露立即同步入口。"""
    return _sync_all_notes()


def _safe_tags(raw_tags: str) -> list[str]:
    """读取标签 JSON；损坏时不阻断同步。"""
    import json

    try:
        tags = json.loads(raw_tags or "[]")
    except json.JSONDecodeError:
        return []
    return tags if isinstance(tags, list) else []


def start_startup_sync_if_needed() -> None:
    """启动时后台检查：SQLite 有笔记且向量库为空时自动同步。"""

    def worker() -> None:
        try:
            from services.db import SessionLocal
            from models.models import Note
            from sqlalchemy import func

            with SessionLocal() as session:
                note_count = session.scalar(select(func.count()).select_from(Note)) or 0
            if note_count == 0:
                return

            collection = _get_collection()
            if collection.count() == 0:
                _sync_all_notes()
        except Exception as exc:  # 启动同步不能影响主服务
            logger.warning("启动后台向量同步未执行：%s", exc)
            _set_state(
                status="failed",
                error=f"启动同步失败：{exc}",
                last_sync=datetime.now().isoformat(timespec="seconds"),
            )

    threading.Thread(target=worker, daemon=True).start()


def get_stats() -> dict[str, Any]:
    """返回向量库数量和同步状态。"""
    collection = _get_collection()
    try:
        count = collection.count()
    except Exception as exc:  # noqa: BLE001
        raise VectorError(f"读取向量库统计失败：{exc}") from exc

    with _state_lock:
        state = _sync_state.copy()
    return {
        "collection": COLLECTION_NAME,
        "note_count": count,
        **state,
    }
