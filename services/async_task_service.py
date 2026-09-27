# -*- coding: utf-8 -*-
"""后台异步任务执行服务：用本地线程串行处理耗时任务。"""

from __future__ import annotations

import json
import logging
import queue
import threading
from datetime import datetime
from typing import Any

from sqlalchemy import select

from models.models import Note, PendingItem, AsyncTask
from services import cache_service

logger = logging.getLogger(__name__)

VALID_TASK_TYPES = {
    "rewrite_url",
    "rewrite_text",
    "batch_process",
    "evaluate",
    "regenerate",
}


class AsyncTaskError(Exception):
    """任务参数或执行结果不符合要求。"""


class AsyncTaskNotFound(Exception):
    """任务不存在。"""


# 单队列、单工作线程，保证任务顺序执行且不并发改数据库
_task_queue: queue.Queue[int] = queue.Queue()
_worker_started = False
_worker_lock = threading.Lock()
_wake_event = threading.Event()


def _json_dumps(value: Any) -> str:
    """统一中文 JSON 序列化。"""
    return json.dumps(value, ensure_ascii=False)


def _json_loads(raw: str | None, default: Any) -> Any:
    """读取 JSON；损坏时返回默认值。"""
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default


def create_task(task_type: str, params: dict[str, Any]) -> int:
    """创建待执行任务并唤醒工作线程。"""
    if task_type not in VALID_TASK_TYPES:
        raise AsyncTaskError("不支持的任务类型")
    if not isinstance(params, dict):
        raise AsyncTaskError("任务参数必须是对象")

    from services.db import SessionLocal

    now = datetime.now()
    with SessionLocal() as session:
        task = AsyncTask(
            task_type=task_type,
            status="pending",
            progress=0,
            progress_message="等待执行…",
            params=_json_dumps(params),
            result=None,
            error=None,
            created_at=now,
            updated_at=now,
        )
        session.add(task)
        session.commit()
        session.refresh(task)
        task_id = task.id

    _wake_event.set()
    return task_id


def get_task(task_id: int) -> dict[str, Any]:
    """查询单个任务。"""
    from services.db import SessionLocal

    with SessionLocal() as session:
        task = session.get(AsyncTask, task_id)
        if task is None:
            raise AsyncTaskNotFound("任务不存在或已被删除")
        return serialize_task(task)


def list_tasks(limit: int = 20) -> list[dict[str, Any]]:
    """返回最近任务。"""
    from services.db import SessionLocal

    limit = max(1, min(limit, 100))
    with SessionLocal() as session:
        tasks = session.scalars(
            select(AsyncTask).order_by(AsyncTask.created_at.desc(), AsyncTask.id.desc()).limit(limit)
        ).all()
        return [serialize_task(task) for task in tasks]


def serialize_task(task: AsyncTask) -> dict[str, Any]:
    """任务转前端字典。"""
    return {
        "id": task.id,
        "task_type": task.task_type,
        "status": task.status,
        "progress": task.progress,
        "progress_message": task.progress_message,
        "params": _json_loads(task.params, {}),
        "result": _json_loads(task.result, None),
        "error": task.error,
        "created_at": task.created_at.isoformat(timespec="seconds") if task.created_at else None,
        "updated_at": task.updated_at.isoformat(timespec="seconds") if task.updated_at else None,
    }


def start_worker() -> None:
    """幂等启动任务工作线程，并恢复服务重启前的任务状态。"""
    global _worker_started

    _recover_interrupted_tasks()
    with _worker_lock:
        if _worker_started:
            _wake_event.set()
            return
        thread = threading.Thread(target=_worker_loop, name="任务工作线程", daemon=True)
        thread.start()
        _worker_started = True
    _wake_event.set()


def _recover_interrupted_tasks() -> None:
    """服务重启时，运行中任务标记失败；等待中任务保留。"""
    from services.db import SessionLocal

    now = datetime.now()
    with SessionLocal() as session:
        running_tasks = session.scalars(select(AsyncTask).where(AsyncTask.status == "running")).all()
        changed = False
        for task in running_tasks:
            task.status = "failed"
            task.error = "服务重启，任务中断"
            task.progress_message = "任务中断"
            task.updated_at = now
            changed = True
        if changed:
            session.commit()

        pending = session.scalar(select(AsyncTask.id).where(AsyncTask.status == "pending").order_by(AsyncTask.id))
        if pending is not None:
            _wake_event.set()


def _worker_loop() -> None:
    """按任务 ID 顺序执行。"""
    from services.db import SessionLocal

    while True:
        _wake_event.wait()
        task_id: int | None = None
        # 加锁完成“查无任务 → 清除唤醒信号”，避免新建任务的唤醒信号被误清
        with _worker_lock:
            with SessionLocal() as session:
                task_id = session.scalar(
                    select(AsyncTask.id).where(AsyncTask.status == "pending").order_by(AsyncTask.id)
                )
                if task_id is None:
                    _wake_event.clear()
                    # 清除后再查一次，挡住刚好在清除前提交的任务
                    task_id = session.scalar(
                        select(AsyncTask.id).where(AsyncTask.status == "pending").order_by(AsyncTask.id)
                    )
        if task_id is None:
            continue

        _execute_task(task_id)


def _update_running(
    task_id: int,
    progress: int,
    message: str,
) -> None:
    """更新任务进度。"""
    from services.db import SessionLocal

    with SessionLocal() as session:
        task = session.get(AsyncTask, task_id)
        if task is None:
            return
        task.status = "running"
        task.progress = max(0, min(100, int(progress)))
        task.progress_message = message
        task.updated_at = datetime.now()
        session.commit()


def _finish_success(task_id: int, result: Any, message: str = "处理完成") -> None:
    """写入成功结果。"""
    from services.db import SessionLocal

    with SessionLocal() as session:
        task = session.get(AsyncTask, task_id)
        if task is None:
            return
        task.status = "success"
        task.progress = 100
        task.progress_message = message
        task.result = _json_dumps(result)
        task.error = None
        task.updated_at = datetime.now()
        session.commit()
    _wake_event.set()


def _finish_failed(task_id: int, error: str, stage_result: dict[str, str] | None = None) -> None:
    """写入失败结果。"""
    from services.db import SessionLocal

    with SessionLocal() as session:
        task = session.get(AsyncTask, task_id)
        if task is None:
            return
        task.status = "failed"
        task.progress_message = "处理失败"
        task.result = _json_dumps(stage_result) if stage_result else None
        task.error = error
        task.updated_at = datetime.now()
        session.commit()
    _wake_event.set()


def _execute_task(task_id: int) -> None:
    """执行单个任务，捕获业务异常并写入任务状态。"""
    try:
        if task_id not in _task_queue.queue:  # 仅用于提示，不改变队列语义
            pass
        with _get_task_for_update(task_id) as task:
            task_type = task.task_type
            params = _json_loads(task.params, {})

        if task_type == "rewrite_url":
            result = run_rewrite_url(task_id, params)
        elif task_type == "rewrite_text":
            result = run_rewrite_text(task_id, params)
        elif task_type == "batch_process":
            result = run_batch_process(task_id, params)
        elif task_type == "evaluate":
            result = run_evaluate(task_id, params)
        else:
            result = run_regenerate(task_id, params)
        _finish_success(task_id, result)
    except Exception as exc:  # noqa: BLE001 - 后台任务必须把错误写回任务
        stage = getattr(exc, "stage_result", None)
        logger.warning("任务 %s 执行失败：%s", task_id, exc)
        _finish_failed(task_id, str(exc) or "任务执行失败", stage)


class _TaskContext:
    """简单上下文，避免额外抽象。"""

    def __init__(self, task_id: int):
        self.task_id = task_id
        self.session = None
        self.task = None

    def __enter__(self):
        from services.db import SessionLocal

        self.session = SessionLocal()
        self.task = self.session.get(AsyncTask, self.task_id)
        if self.task is None:
            raise AsyncTaskNotFound("任务不存在或已被删除")
        return self.task

    def __exit__(self, exc_type, exc, tb):
        self.session.close()


def _get_task_for_update(task_id: int) -> _TaskContext:
    return _TaskContext(task_id)


def run_rewrite_url(task_id: int, params: dict[str, Any]) -> dict[str, Any]:
    """URL 改写任务；命中缓存时不调用 AI。"""
    from services import ai_service, crawler_service

    url = str(params.get("url") or "").strip()
    cache_key = cache_service.make_rewrite_url_key(url)
    cached = cache_service.get_cache(cache_key)
    if cached is not None:
        _update_running(task_id, 100, "已命中缓存，正在返回…")
        return cached

    _update_running(task_id, 20, "正在抓取网页…")
    try:
        html_bytes, final_url = crawler_service.fetch_url(url)
        extracted = crawler_service.extract_content(html_bytes, final_url)
    except crawler_service.CrawlError as exc:
        exc.stage_result = {"stage": "crawl"}
        raise

    _update_running(task_id, 65, "正在 AI 改写…")
    try:
        result = {
            "title": extracted["title"],
            "content": ai_service.rewrite_to_plain(extracted["title"], extracted["content"]),
            "tags": ai_service.generate_tags(extracted["title"], extracted["content"]),
            "original_url": final_url,
            "source": "手动输入",
        }
    except ai_service.AiError as exc:
        exc.stage_result = {"stage": "ai"}
        raise

    cache_service.set_cache(cache_key, result)
    return result


def run_rewrite_text(task_id: int, params: dict[str, Any]) -> dict[str, Any]:
    """手动粘贴改写任务；命中缓存时不调用 AI。"""
    from services import ai_service, crawler_service

    cache_key = cache_service.make_rewrite_text_key(str(params.get("content") or ""))
    cached = cache_service.get_cache(cache_key)
    if cached is not None:
        _update_running(task_id, 100, "已命中缓存，正在返回…")
        return cached

    _update_running(task_id, 20, "正在整理正文…")
    try:
        extracted = crawler_service.extract_from_text(
            str(params.get("title") or ""),
            str(params.get("content") or ""),
            str(params.get("url") or ""),
        )
    except crawler_service.CrawlError as exc:
        exc.stage_result = {"stage": "text"}
        raise

    title = extracted["title"]
    if not title:
        _update_running(task_id, 45, "正在生成标题…")
        try:
            title = ai_service.generate_title(extracted["content"]) or "无标题"
        except ai_service.AiError:
            title = "无标题"

    _update_running(task_id, 70, "正在 AI 改写…")
    try:
        result = {
            "title": title,
            "content": ai_service.rewrite_to_plain(title, extracted["content"]),
            "tags": ai_service.generate_tags(title, extracted["content"]),
            "original_url": extracted["url"] or None,
            "source": "手动粘贴",
        }
    except ai_service.AiError as exc:
        exc.stage_result = {"stage": "ai"}
        raise

    cache_service.set_cache(cache_key, result)
    return result


def run_batch_process(task_id: int, params: dict[str, Any]) -> dict[str, Any]:
    """批量处理任务；单条失败不影响后续。"""
    from services import ai_service, rss_service
    from services.db import SessionLocal

    raw_ids = params.get("item_ids") or []
    item_ids = list(dict.fromkeys(int(item_id) for item_id in raw_ids))
    total = len(item_ids)
    results: list[dict[str, Any]] = []
    success_count = failed_count = 0

    for index, item_id in enumerate(item_ids, start=1):
        _update_running(
            task_id,
            round((index - 1) / total * 100),
            f"正在处理 {index}/{total}…",
        )
        try:
            with SessionLocal() as session:
                item = rss_service.get_pending_by_id(session, item_id)
                cache_key = cache_service.make_rewrite_url_key(item.url)
                preview = cache_service.get_cache(cache_key)
                if preview is None:
                    preview = rss_service.process_pending_item(session, item_id)
                    cache_service.set_cache(cache_key, preview)
                # 缓存来自其他链接时只复用改写内容，来源仍以当前队列项为准
                preview = {
                    **preview,
                    "original_url": item.url,
                    "source": item.source or preview.get("source") or "RSS",
                }
                note = rss_service.save_processed_item(
                    session,
                    item_id,
                    preview["title"],
                    preview["content"],
                    preview.get("tags", []),
                )
                note_id = note.id
            results.append({
                "item_id": item_id,
                "status": "success",
                "note_id": note_id,
            })
            success_count += 1
        except Exception as exc:  # noqa: BLE001 - 批量任务必须继续
            results.append({
                "item_id": item_id,
                "status": "failed",
                "error": str(exc) or "处理失败",
            })
            failed_count += 1

    _update_running(task_id, 100, f"批量处理完成：成功 {success_count} 条，失败 {failed_count} 条")
    return {
        "success": success_count,
        "failed": failed_count,
        "results": results,
    }


def run_evaluate(task_id: int, params: dict[str, Any]) -> dict[str, Any]:
    """立即质量评估任务；命中缓存时不调用 AI。"""
    from services import note_service
    from services.db import SessionLocal

    note_id = int(params.get("note_id"))
    _update_running(task_id, 25, "正在读取笔记…")
    with SessionLocal() as session:
        note = note_service.get_note_by_id(session, note_id)
        content = note.content
        cache_key = cache_service.make_evaluate_key(note_id, content)
        cached = cache_service.get_cache(cache_key)
        if cached is not None:
            note_service.set_quality_score(
                session,
                note_id,
                float(cached["score"]),
                str(cached.get("reason") or ""),
            )
            result = cached
        else:
            result = note_service.evaluate_note_quality(session, note_id)
            cache_service.set_cache(cache_key, result)
    _update_running(task_id, 100, "质量评估完成")
    return result


def run_regenerate(task_id: int, params: dict[str, Any]) -> dict[str, Any]:
    """风格重生成任务；命中缓存时不调用 AI。"""
    from services import note_service, vector_service
    from services.db import SessionLocal

    note_id = int(params.get("note_id"))
    style = str(params.get("style") or "通俗")
    _update_running(task_id, 20, "正在读取笔记…")

    with SessionLocal() as session:
        note = note_service.get_note_by_id(session, note_id)
        cache_key = cache_service.make_regenerate_key(note_id, style, note.content)
        cached = cache_service.get_cache(cache_key)

        if cached is None:
            note, warnings = note_service.regenerate_note(session, note_id, style)
            note_data = note_service.serialize_note(note)
            cache_value = {
                "note": note_data,
                "warnings": warnings,
            }
            cache_service.set_cache(cache_key, cache_value)
            result = cache_value
        else:
            note_data = cached["note"]
            tags = note_data.get("tags") or []
            note.title = note_data["title"]
            note.content = note_data["content"]
            note.tags = _json_dumps(tags)
            note.related_ids = _json_dumps(note_data.get("related_ids") or [])
            note.quality_score = note_data.get("quality_score")
            note.updated_at = datetime.now()
            session.commit()
            try:
                vector_service.update_note(note.id, note.title, note.content, tags)
            except vector_service.VectorError as exc:
                logger.warning("缓存命中后向量更新失败：%s", exc)
            note_service._quality_reasons[note.id] = note_data.get("quality_reason") or ""
            result = cached

    _update_running(task_id, 100, "重新生成完成")
    return result
