# -*- coding: utf-8 -*-
"""向量库服务：远端/本地双 collection、笔记分块和混合检索基础。"""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Any

import config
from sqlalchemy import func, select

from services import embedding_service

logger = logging.getLogger(__name__)

REMOTE_COLLECTION = "notes_remote"
LOCAL_COLLECTION = "notes_local"
COLLECTION_NAMES = {"remote": REMOTE_COLLECTION, "local": LOCAL_COLLECTION}
LEGACY_COLLECTION = "notes"
CHUNK_SIZE = 500
CHUNK_OVERLAP = 50
BATCH_SIZE = embedding_service.EMBEDDING_BATCH_SIZE

_state_lock = threading.Lock()
_sync_lock = threading.Lock()
_sync_state: dict[str, Any] = {
    "status": "idle",
    "progress": 0,
    "total": 0,
    "success": 0,
    "failed": 0,
    "last_sync": None,
    "error": "",
    "remote_error": "",
    "remote_available": False,
    "fallback_active": False,
}


class VectorError(Exception):
    """向量库操作失败，message 可直接展示给用户。"""


def _set_state(**values: Any) -> None:
    with _state_lock:
        _sync_state.update(values)


def _get_state() -> dict[str, Any]:
    with _state_lock:
        return _sync_state.copy()


def _get_client() -> Any:
    """创建或复用 Chroma 持久化客户端。"""
    try:
        import chromadb
    except ImportError as exc:
        raise VectorError("当前环境缺少 ChromaDB，请先安装 chromadb") from exc
    try:
        config.ensure_dirs()
        return chromadb.PersistentClient(path=str(config.VECTOR_DB_DIR))
    except Exception as exc:  # noqa: BLE001
        raise VectorError(f"向量库初始化失败：{exc}") from exc


def _collection_metadata(mode: str) -> dict[str, Any]:
    dimension = embedding_service.LOCAL_DIM if mode == "local" else embedding_service.REMOTE_DIM
    return {
        "hnsw:space": "cosine",
        "embedding_key": embedding_service.get_signature(mode),
        "dimension": dimension,
    }


def _get_collection(mode: str) -> Any:
    mode = "local" if mode == "local" else "remote"
    name = COLLECTION_NAMES[mode]
    try:
        client = _get_client()
        try:
            return client.get_collection(name=name)
        except Exception:
            return client.create_collection(name=name, metadata=_collection_metadata(mode))
    except VectorError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise VectorError(f"向量库初始化失败：{exc}") from exc


def _reset_collection(mode: str) -> Any:
    name = COLLECTION_NAMES[mode]
    try:
        client = _get_client()
        try:
            client.delete_collection(name=name)
        except Exception:
            pass
        return client.create_collection(name=name, metadata=_collection_metadata(mode))
    except Exception as exc:  # noqa: BLE001
        raise VectorError(f"向量库重建失败：{exc}") from exc


def _safe_collection(mode: str) -> Any | None:
    try:
        return _get_collection(mode)
    except VectorError:
        return None


def _collection_count(mode: str) -> int:
    collection = _safe_collection(mode)
    if collection is None:
        return 0
    try:
        return int(collection.count())
    except Exception:
        return 0


def _collection_is_current(mode: str) -> bool:
    collection = _safe_collection(mode)
    if collection is None:
        return False
    try:
        if int(collection.count()) <= 0:
            return False
        metadata = collection.metadata or {}
        dimension = embedding_service.LOCAL_DIM if mode == "local" else embedding_service.REMOTE_DIM
        return metadata.get("embedding_key") == embedding_service.get_signature(mode) and int(metadata.get("dimension", 0)) == dimension
    except Exception:
        return False


def _split_body(text: str, limit: int, overlap: int) -> list[str]:
    """按段落和标点尽可能切分正文，块之间保留重叠文本。"""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text or limit <= 0:
        return []
    overlap = max(0, min(overlap, limit - 1))
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + limit, len(text))
        if end < len(text):
            window_start = max(start, end - min(limit - 1, 120))
            window = text[window_start:end]
            break_at = -1
            for separator in ("\n\n", "\n", "。", "；", "，", " "):
                position = window.rfind(separator)
                if position >= 0:
                    break_at = window_start + position + len(separator)
                    break
            if break_at > start:
                end = break_at
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= len(text):
            break
        next_start = max(start + 1, end - overlap)
        while next_start < end and text[next_start].isspace():
            next_start += 1
        start = next_start
    return chunks


def build_note_chunks(note_id: int, title: str, category: str, content: str, tags: list[str] | None = None) -> list[dict[str, Any]]:
    """把一篇笔记切成标题块和正文块，单块总长度不超过 500。"""
    safe_title = (title or "无标题").strip() or "无标题"
    safe_category = (category or "默认").strip() or "默认"
    prefix = f"标题：{safe_title}\n分类：{safe_category}\n正文：\n"
    if len(prefix) >= CHUNK_SIZE:
        prefix = prefix[:CHUNK_SIZE]
    chunks: list[dict[str, Any]] = [{
        "id": f"{note_id}_title",
        "text": f"标题：{safe_title}\n分类：{safe_category}",
        "metadata": {"note_id": int(note_id), "title": safe_title, "category": safe_category, "tags": ", ".join(tags or []), "kind": "title", "chunk_index": 0},
    }]
    body_limit = max(1, CHUNK_SIZE - len(prefix))
    body_chunks = _split_body(content, body_limit, CHUNK_OVERLAP) or [""]
    for index, body in enumerate(body_chunks):
        chunks.append({
            "id": f"{note_id}_body_{index}",
            "text": (prefix + body)[:CHUNK_SIZE],
            "metadata": {"note_id": int(note_id), "title": safe_title, "category": safe_category, "tags": ", ".join(tags or []), "kind": "body", "chunk_index": index},
        })
    return chunks


def _note_values(note_id: int, title: str, content: str, tags: list[str] | None, category: str = "默认") -> dict[str, Any]:
    return {"id": int(note_id), "title": title or "无标题", "content": content or "", "tags": list(tags or []), "category": category or "默认"}


def _upsert_note_in_collection(mode: str, note: dict[str, Any]) -> None:
    collection = _get_collection(mode)
    chunks = build_note_chunks(note["id"], note["title"], note["category"], note["content"], note.get("tags"))
    for start in range(0, len(chunks), BATCH_SIZE):
        batch = chunks[start:start + BATCH_SIZE]
        texts = [item["text"] for item in batch]
        vectors = embedding_service.embed_texts(texts, mode=mode)
        collection.upsert(
            ids=[item["id"] for item in batch],
            embeddings=vectors,
            documents=texts,
            metadatas=[item["metadata"] for item in batch],
        )

def _delete_note_in_collection(mode: str, note_id: int) -> None:
    collection = _get_collection(mode)
    collection.delete(where={"note_id": int(note_id)})


def add_note(note_id: int, title: str, content: str, tags: list[str] | None = None, category: str = "默认") -> None:
    """新增或更新单条笔记向量；本地成功即视为可用。"""
    note = _note_values(note_id, title, content, tags, category)
    local_error: Exception | None = None
    remote_error: Exception | None = None
    try:
        _upsert_note_in_collection("local", note)
    except Exception as exc:  # noqa: BLE001
        local_error = exc
    try:
        _upsert_note_in_collection("remote", note)
        _set_state(remote_available=True, remote_error="", fallback_active=False)
    except Exception as exc:  # noqa: BLE001
        remote_error = exc
        _set_state(remote_available=False, remote_error=str(exc), fallback_active=True)
        logger.warning("远端笔记向量写入失败，保留本地索引：%s", exc)
    if local_error is not None:
        message = str(local_error) if isinstance(local_error, VectorError) else f"笔记向量写入失败：{local_error}"
        raise VectorError(message) from local_error
    if remote_error is not None:
        logger.info("笔记已写入本地向量库，远端索引暂不可用：%s", remote_error)


def delete_note(note_id: int) -> None:
    """按笔记 ID 删除两套索引；本地失败才向上报告。"""
    local_error: Exception | None = None
    try:
        _delete_note_in_collection("local", note_id)
    except Exception as exc:  # noqa: BLE001
        local_error = exc
    try:
        _delete_note_in_collection("remote", note_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("远端笔记向量删除失败：%s", exc)
    if local_error is not None:
        message = str(local_error) if isinstance(local_error, VectorError) else f"笔记向量删除失败：{local_error}"
        raise VectorError(message) from local_error


def update_note(note_id: int, title: str, content: str, tags: list[str] | None = None, category: str = "默认") -> None:
    """更新向量：先删旧块，再写新块。"""
    delete_note(note_id)
    add_note(note_id, title, content, tags, category)


def _score_from_distance(distance: Any) -> float:
    """Chroma cosine distance 转相关度，并裁剪到 0 到 1。"""
    try:
        score = 1 - float(distance)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, score))


def _search_collection(mode: str, query: str, n_results: int) -> list[dict[str, Any]]:
    """在单个 collection 中检索并按笔记聚合分块结果。"""
    if not _collection_is_current(mode):
        raise VectorError("向量库需要重建")
    collection = _get_collection(mode)
    query_vector = embedding_service.embed_texts([query], mode=mode)[0]
    result = collection.query(
        query_embeddings=[query_vector],
        n_results=max(1, min(int(n_results) * 3, 200)),
        include=["metadatas", "distances"],
    )
    ids = result.get("ids", [[]])[0]
    distances = result.get("distances", [[]])[0]
    metadatas = result.get("metadatas", [[]])[0]
    grouped: dict[int, dict[str, Any]] = {}
    for index, raw_id in enumerate(ids):
        metadata = metadatas[index] if index < len(metadatas) else {}
        try:
            note_id = int(metadata.get("note_id", str(raw_id).split("_", 1)[0]))
        except (TypeError, ValueError):
            continue
        distance = distances[index] if index < len(distances) else 1
        score = _score_from_distance(distance)
        current = grouped.get(note_id)
        if current is None or score > current["score"]:
            grouped[note_id] = {"id": note_id, "score": score}
    return sorted(grouped.values(), key=lambda item: (-item["score"], item["id"]))[:max(1, int(n_results))]


def search_notes(query: str, n_results: int = 10) -> list[dict[str, Any]]:
    """语义搜索；远端不可用时自动查询本地 collection。"""
    query = (query or "").strip()
    if not query:
        raise VectorError("搜索内容不能为空")
    mode = embedding_service.get_mode()
    if mode == "remote":
        if _collection_is_current("remote"):
            try:
                items = _search_collection("remote", query, n_results)
                _set_state(remote_available=True, fallback_active=False, remote_error="")
                return items
            except Exception as exc:  # noqa: BLE001
                logger.warning("远端向量检索失败，降级本地：%s", exc)
                _set_state(remote_available=False, remote_error=str(exc), fallback_active=True)
        try:
            return _search_collection("local", query, n_results)
        except Exception as exc:  # noqa: BLE001
            message = str(exc) if isinstance(exc, VectorError) else f"语义搜索失败：{exc}"
            raise VectorError(message) from exc
    try:
        return _search_collection("local", query, n_results)
    except Exception as exc:  # noqa: BLE001
        message = str(exc) if isinstance(exc, VectorError) else f"语义搜索失败：{exc}"
        raise VectorError(message) from exc


def _safe_tags(raw_tags: str) -> list[str]:
    import json

    try:
        tags = json.loads(raw_tags or "[]")
    except json.JSONDecodeError:
        return []
    return tags if isinstance(tags, list) else []


def _note_from_model(note: Any) -> dict[str, Any]:
    return _note_values(note.id, note.title, note.content, _safe_tags(note.tags), note.category)


def _sync_all_notes() -> dict[str, int]:
    """重建本地和远端两套向量库；以本地完成作为主成功标准。"""
    if not _sync_lock.acquire(blocking=False):
        raise VectorError("已有同步任务正在执行")
    from services.db import SessionLocal
    from models.models import Note

    _set_state(status="rebuilding", progress=0, total=0, success=0, failed=0, error="", remote_error="", remote_available=False, fallback_active=False)
    total = success = failed = 0
    try:
        with SessionLocal() as session:
            notes = list(session.scalars(select(Note).order_by(Note.id)).all())
        total = len(notes)
        _set_state(total=total)
        _reset_collection("local")
        for note in notes:
            try:
                _upsert_note_in_collection("local", _note_from_model(note))
                success += 1
            except Exception as exc:  # noqa: BLE001
                failed += 1
                logger.warning("本地向量重建失败（笔记 %s）：%s", note.id, exc)
            _set_state(progress=success + failed, success=success, failed=failed)

        remote_error = ""
        remote_ok = False
        if notes:
            try:
                _reset_collection("remote")
                remote_ok = True
                for note in notes:
                    _upsert_note_in_collection("remote", _note_from_model(note))
            except Exception as exc:  # noqa: BLE001
                remote_ok = False
                remote_error = str(exc)
                logger.warning("远端向量重建失败，已保留本地索引：%s", exc)
        _set_state(status="done" if not failed else "failed", last_sync=datetime.now().isoformat(timespec="seconds"), remote_available=remote_ok, remote_error=remote_error, fallback_active=bool(remote_error))
        return {"total": total, "success": success, "failed": failed}
    except Exception as exc:
        message = str(exc) if isinstance(exc, VectorError) else f"全量同步失败：{exc}"
        _set_state(status="failed", error=message, last_sync=datetime.now().isoformat(timespec="seconds"))
        raise VectorError(message) from exc
    finally:
        _sync_lock.release()


def start_background_sync() -> dict[str, str]:
    """后台启动双集合重建；已有任务时抛 VectorError。"""
    with _state_lock:
        if _sync_state["status"] in ("running", "rebuilding"):
            raise VectorError("已有同步任务正在执行")
    worker = threading.Thread(target=_sync_all_notes, daemon=True)
    worker.start()
    return {"status": "rebuilding"}


def sync_all_notes() -> dict[str, int]:
    return _sync_all_notes()


def start_startup_sync_if_needed() -> None:
    def worker() -> None:
        try:
            from services.db import SessionLocal
            from models.models import Note
            with SessionLocal() as session:
                note_count = session.scalar(select(func.count()).select_from(Note)) or 0
            if note_count == 0:
                return
            mode = embedding_service.get_mode()
            if _collection_count(mode) == 0 or not _collection_is_current(mode):
                _sync_all_notes()
        except Exception as exc:  # noqa: BLE001
            logger.warning("启动后台向量同步未执行：%s", exc)
            _set_state(status="failed", error=f"启动同步失败：{exc}", last_sync=datetime.now().isoformat(timespec="seconds"))
    threading.Thread(target=worker, daemon=True).start()


def is_rebuilding() -> bool:
    return _get_state().get("status") in ("running", "rebuilding")


def get_stats() -> dict[str, Any]:
    """返回向量库数量、模式、签名和同步状态。"""
    mode = embedding_service.get_mode()
    local_count = _collection_count("local")
    remote_count = _collection_count("remote")
    state = _get_state()
    try:
        from services.db import SessionLocal
        from models.models import Note
        with SessionLocal() as session:
            note_count = int(session.scalar(select(func.count()).select_from(Note)) or 0)
    except Exception:
        note_count = 0
    active_current = _collection_is_current(mode)
    active_count = remote_count if mode == "remote" else local_count
    return {
        "mode": mode,
        "collection": COLLECTION_NAMES[mode],
        "note_count": active_count,
        "remote_count": remote_count,
        "local_count": local_count,
        "rebuild_required": bool(note_count and not active_current),
        **state,
    }
