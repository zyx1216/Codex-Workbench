# -*- coding: utf-8 -*-
"""
笔记数据管理。

- 笔记 CRUD、标签和分类聚合、首页统计
- 笔记提交后写入 ChromaDB，并按向量相关度生成 related_ids
- 笔记保存后把质量评估任务放入后台队列，不阻塞接口
"""

from __future__ import annotations

import json
import logging
import queue
import re
import threading
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import object_session

from models.models import Note, PendingItem
from services import link_service, vector_service

logger = logging.getLogger(__name__)

# 中英文逗号都作为标签分隔符
_TAG_SPLIT = re.compile(r"[,，]")

# 向量关联阈值和数量限制
RELATED_SCORE_THRESHOLD = 0.5
RELATED_LIMIT = 5

# 更新会议字段用的哨兵：区分“未传入”和“显式清空”
_UNSET = object()

# 质量评估后台队列：单个线程串行处理，避免并发请求太多
_quality_queue: queue.Queue[int] = queue.Queue()
_quality_pending: set[int] = set()
_quality_lock = threading.Lock()
_quality_worker_started = False

# 当前进程内的评分理由缓存，不写数据库
_quality_reasons: dict[int, str] = {}

# 当前进程内的会议待办缓存：供详情逐条“创建任务”，重启后为空（正文仍保留待办文本）
_meeting_todos: dict[int, list[dict[str, str]]] = {}


class NoteNotFound(Exception):
    """笔记不存在，message 为中文提示。"""


def parse_tags(text: str) -> list[str]:
    """标签字符串切分：去空白、去重保序。"""
    result: list[str] = []
    for part in _TAG_SPLIT.split(text or ""):
        tag = part.strip()
        if tag and tag not in result:
            result.append(tag)
    return result


def tags_to_text(tags: list[str]) -> str:
    """标签列表转逗号分隔字符串，用于编辑表单回填。"""
    return ", ".join(tags or [])

def parse_meeting_time(value: Any) -> datetime | None:
    """解析会议时间（datetime-local/ISO）；空值返回 None。"""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    raw = str(value).strip()
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError("会议时间格式不正确") from exc


def normalize_attendees(value: Any) -> str | None:
    """参会人按逗号切分去空白后再合并；空值返回 None。"""
    if value in (None, ""):
        return None
    names = [part.strip() for part in re.split(r"[,，]", str(value))]
    names = [name for name in names if name]
    return ", ".join(names) if names else None


def _note_tags(note: Note) -> list[str]:
    """读取单条笔记的标签 JSON，损坏时返回空列表。"""
    try:
        tags = json.loads(note.tags) if note.tags else []
    except json.JSONDecodeError:
        return []
    return tags if isinstance(tags, list) else []


def _related_ids(note: Note) -> list[int]:
    """读取关联笔记 ID，JSON 损坏时返回空列表。"""
    try:
        ids = json.loads(note.related_ids or "[]")
    except json.JSONDecodeError:
        return []
    result = []
    for value in ids if isinstance(ids, list) else []:
        try:
            result.append(int(value))
        except (TypeError, ValueError):
            continue
    return result


def clean_tag_list(tags: Any) -> list[str]:
    """标签列表去空白、去重并保持原顺序。"""
    result: list[str] = []
    for tag in tags or []:
        value = str(tag).strip()
        if value and value not in result:
            result.append(value)
    return result


def _related_query(note: Note) -> str:
    """构造关联检索文本：标题、标签和正文前1000字。"""
    tag_text = ", ".join(_note_tags(note))
    short_content = (note.content or "")[:1000]
    return f"标题：{note.title}\n标签：{tag_text}\n正文：{short_content}"


def _refresh_related(note: Note, session: Any, commit: bool = True) -> None:
    """按向量相似度刷新关联笔记。"""
    hits = vector_service.search_notes(_related_query(note), n_results=6)
    related_ids: list[int] = []
    for hit in hits:
        note_id = int(hit["id"])
        if note_id == note.id:
            continue
        if hit["score"] <= RELATED_SCORE_THRESHOLD:
            continue
        if note_id not in related_ids:
            related_ids.append(note_id)
        if len(related_ids) >= RELATED_LIMIT:
            break
    note.related_ids = json.dumps(related_ids, ensure_ascii=False)
    if commit:
        session.commit()


def index_note(note: Note) -> tuple[bool, str]:
    """把已提交笔记写入向量库并刷新关联；失败只返回警告。"""
    warning = ""
    session = object_session(note)
    try:
        vector_service.add_note(
            note.id, note.title, note.content, _note_tags(note)
        )
        _refresh_related(note, session, commit=True)
        indexed = True
    except vector_service.VectorError as exc:
        indexed = False
        warning = str(exc)
        logger.warning("笔记保留成功，但向量索引失败：%s", exc)
    setattr(note, "vector_indexed", indexed)
    setattr(note, "vector_warning", warning)
    return indexed, warning


def create_note(
    session: Any,
    title: str,
    content: str,
    original_url: str | None = None,
    tags: list[str] | None = None,
    source: str = "手动输入",
    category: str = "默认",
    note_type: str = "普通",
    meeting_time: Any = None,
    meeting_attendees: str | None = None,
    meeting_topic: str | None = None,
    commit: bool = True,
) -> Note:
    """新建笔记。commit=False 时由调用方和其他改动一起提交。"""
    note_type = (note_type or "普通").strip()
    if note_type not in ("普通", "会议"):
        raise ValueError("笔记类型只能是普通或会议")
    now = datetime.now()
    note = Note(
        title=(title or "").strip() or "无标题",
        content=content or "",
        original_url=(original_url or "").strip() or None,
        source=source or "手动输入",
        tags=json.dumps(clean_tag_list(tags), ensure_ascii=False),
        related_ids="[]",
        task_ids="[]",
        quality_score=None,
        category=category or "默认",
        note_type=note_type,
        meeting_time=parse_meeting_time(meeting_time),
        meeting_attendees=normalize_attendees(meeting_attendees),
        meeting_topic=str(meeting_topic or "").strip() or None,
        created_at=now,
        updated_at=now,
    )
    session.add(note)
    if not commit:
        return note
    session.commit()
    session.refresh(note)
    index_note(note)
    enqueue_quality_evaluation(note.id)
    return note


def list_notes(
    session: Any,
    keyword: str = "",
    tag: str = "",
    category: str = "",
    note_type: str = "",
    page: int = 1,
    size: int = 20,
) -> tuple[list[Note], int]:
    """查询笔记，返回（当前页笔记列表, 总数）。note_type 空串不过滤。"""
    keyword = (keyword or "").strip().lower()
    tag = (tag or "").strip()
    category = (category or "").strip()
    note_type = (note_type or "").strip()

    all_notes = list(session.scalars(select(Note)).all())

    def matched(note: Note) -> bool:
        if keyword and not (
            keyword in note.title.lower() or keyword in note.content.lower()
        ):
            return False
        if tag and tag not in _note_tags(note):
            return False
        if category and note.category != category:
            return False
        if note_type and note.note_type != note_type:
            return False
        return True

    all_notes = [note for note in all_notes if matched(note)]
    all_notes.sort(key=lambda note: (note.created_at, note.id), reverse=True)

    total = len(all_notes)
    page = max(page, 1)
    size = max(size, 1)
    start = (page - 1) * size
    return all_notes[start:start + size], total


def update_note(
    session: Any,
    note_id: int,
    title: str,
    content: str,
    tags: list[str],
    category: str,
    note_type: Any = _UNSET,
    meeting_time: Any = _UNSET,
    meeting_attendees: Any = _UNSET,
    meeting_topic: Any = _UNSET,
    task_ids: Any = _UNSET,
) -> Note:
    """更新标题、正文、标签、分类、会议字段及关联任务；未传字段保持原值。"""
    note = get_note_by_id(session, note_id)
    note.title = (title or "").strip() or "无标题"
    note.content = content or ""
    note.tags = json.dumps(clean_tag_list(tags), ensure_ascii=False)
    note.category = (category or "").strip() or "默认"
    if note_type is not _UNSET:
        value = str(note_type or "普通").strip()
        if value not in ("普通", "会议"):
            raise ValueError("笔记类型只能是普通或会议")
        note.note_type = value
    if meeting_time is not _UNSET:
        note.meeting_time = parse_meeting_time(meeting_time)
    if meeting_attendees is not _UNSET:
        note.meeting_attendees = normalize_attendees(meeting_attendees)
    if meeting_topic is not _UNSET:
        value = str(meeting_topic or "").strip()
        note.meeting_topic = value or None
    if task_ids is not _UNSET:
        try:
            link_service.set_note_tasks(session, note, task_ids or [])
        except link_service.LinkError as exc:
            raise ValueError(str(exc)) from exc
    note.updated_at = datetime.now()
    session.commit()
    session.refresh(note)

    warning = ""
    try:
        vector_service.update_note(
            note.id, note.title, note.content, clean_tag_list(tags)
        )
        _refresh_related(note, session, commit=True)
        setattr(note, "vector_indexed", True)
    except vector_service.VectorError as exc:
        warning = str(exc)
        setattr(note, "vector_indexed", False)
        logger.warning("笔记更新成功，但向量索引更新失败：%s", exc)
    setattr(note, "vector_warning", warning)
    return note


def all_tags(session: Any) -> list[dict[str, Any]]:
    """聚合全部笔记标签，返回 [{name, count}]。"""
    counter: dict[str, int] = {}
    for note in session.scalars(select(Note)).all():
        for tag in _note_tags(note):
            counter[tag] = counter.get(tag, 0) + 1
    items = [{"name": name, "count": count} for name, count in counter.items()]
    items.sort(key=lambda item: (-item["count"], item["name"]))
    return items


def all_categories(session: Any) -> list[dict[str, Any]]:
    """聚合分类，返回 [{name, count}]。"""
    counter: dict[str, int] = {}
    for note in session.scalars(select(Note)).all():
        counter[note.category] = counter.get(note.category, 0) + 1
    items = [{"name": name, "count": count} for name, count in counter.items()]
    items.sort(key=lambda item: (-item["count"], item["name"]))
    return items


def recent_notes(session: Any, limit: int = 5) -> list[Note]:
    """返回最近 N 条笔记。"""
    notes, _ = list_notes(session, page=1, size=max(limit, 1))
    return notes


def get_stats(session: Any) -> dict[str, int]:
    """首页统计：笔记总数、今日新增、待处理数、去重标签数。"""
    notes = list(session.scalars(select(Note)).all())
    today = datetime.now().date()
    today_notes = sum(
        1 for note in notes
        if note.created_at and note.created_at.date() == today
    )
    pending_count = session.scalar(
        select(func.count()).select_from(PendingItem)
        .where(PendingItem.status == "pending")
    )
    tag_names = {tag for note in notes for tag in _note_tags(note)}
    return {
        "total_notes": len(notes),
        "today_notes": today_notes,
        "pending_count": int(pending_count or 0),
        "tags_count": len(tag_names),
    }


def get_note_by_id(session: Any, note_id: int) -> Note:
    """按主键查笔记，不存在抛 NoteNotFound。"""
    note = session.get(Note, note_id)
    if note is None:
        raise NoteNotFound("笔记不存在或已被删除")
    return note


def get_meeting_notes(session: Any) -> list[Note]:
    """返回全部会议记录：会议时间倒序，无时间按创建时间倒序。"""
    notes = [note for note in session.scalars(select(Note)).all() if note.note_type == "会议"]
    notes.sort(key=lambda note: (note.meeting_time or note.created_at, note.id), reverse=True)
    return notes


def _build_meeting_content(organized: dict[str, Any]) -> str:
    """把整理结果拼成结构化中文正文。"""
    parts: list[str] = []
    discussion = str(organized.get("discussion") or "").strip()
    if discussion:
        parts.append("【议题讨论】\n" + discussion)
    decisions = organized.get("decisions") or []
    if isinstance(decisions, list) and decisions:
        lines = [f"{index}. {str(item).strip()}" for index, item in enumerate(decisions, start=1) if str(item).strip()]
        if lines:
            parts.append("【决议事项】\n" + "\n".join(lines))
    todos = organized.get("todos") or []
    if isinstance(todos, list) and todos:
        lines = []
        for index, todo in enumerate(todos, start=1):
            if not isinstance(todo, dict):
                continue
            content = str(todo.get("content") or "").strip()
            if not content:
                continue
            assignee = str(todo.get("assignee") or "").strip()
            lines.append(f"{index}. {content}" + (f"（负责人：{assignee}）" if assignee else ""))
        if lines:
            parts.append("【后续待办】\n" + "\n".join(lines))
    return "\n\n".join(parts)


def save_organized_meeting(
    session: Any,
    organized: dict[str, Any],
    create_tasks: bool = False,
    selected_todos: set[int] | None = None,
) -> tuple[Note, dict[str, Any]]:
    """保存整理结果为会议笔记；勾选的待办可同时创建工作任务。"""
    from services import task_service

    topic = str(organized.get("topic") or "").strip()
    attendees = str(organized.get("attendees") or "").strip()
    meeting_time = organized.get("meeting_time") or None
    content = _build_meeting_content(organized)
    note = create_note(
        session=session,
        title=topic or "无标题会议纪要",
        content=content,
        tags=["会议纪要"],
        source="会议记录",
        note_type="会议",
        meeting_time=meeting_time,
        meeting_attendees=attendees,
        meeting_topic=topic or None,
    )

    task_count = 0
    warnings: list[str] = []
    todos = organized.get("todos") if isinstance(organized.get("todos"), list) else []
    for index, todo in enumerate(todos):
        if not isinstance(todo, dict):
            continue
        if selected_todos is not None and index not in selected_todos:
            continue
        if selected_todos is None and not create_tasks:
            continue
        todo_content = str(todo.get("content") or "").strip()
        if not todo_content:
            continue
        assignee = str(todo.get("assignee") or "").strip()
        title_text = f"[{assignee}] {todo_content}" if assignee else todo_content
        try:
            task_service.create_task(session, title=title_text, category="工作", note_id=note.id)
            task_count += 1
        except task_service.TaskError as exc:
            warnings.append(str(exc))
            logger.warning("会议待办创建任务失败：%s", exc)

    _meeting_todos[note.id] = [
        {"content": str(todo.get("content") or "").strip(), "assignee": str(todo.get("assignee") or "").strip()}
        for todo in todos if isinstance(todo, dict) and str(todo.get("content") or "").strip()
    ]
    return note, {"task_count": task_count, "warnings": warnings}


def delete_note(session: Any, note_id: int) -> dict[str, str | bool]:
    """直接真删笔记并解除任务关联；向量删除失败不回滚笔记删除。"""
    note = get_note_by_id(session, note_id)
    link_service.set_note_tasks(session, note, [])
    session.delete(note)
    session.commit()

    warning = ""
    indexed = True
    try:
        vector_service.delete_note(note_id)
    except vector_service.VectorError as exc:
        indexed = False
        warning = str(exc)
        logger.warning("笔记已删除，但向量索引删除失败：%s", exc)
    _quality_reasons.pop(note_id, None)
    return {"indexed": indexed, "warning": warning}


def get_related_notes(session: Any, note_id: int, limit: int = 5) -> list[dict[str, Any]]:
    """返回仍存在的关联笔记，失效 ID 自动跳过。"""
    note = get_note_by_id(session, note_id)
    items: list[dict[str, Any]] = []
    for related_id in _related_ids(note)[:max(limit, 1)]:
        related = session.get(Note, related_id)
        if related is not None:
            items.append(serialize_related_note(related))
    return items


def serialize_related_note(note: Note) -> dict[str, Any]:
    """关联笔记卡片数据：标题、标签和正文前200字。"""
    return {
        "id": note.id,
        "title": note.title,
        "tags": _note_tags(note),
        "summary": (note.content or "")[:200],
    }


def set_quality_score(session: Any, note_id: int, score: float, reason: str = "") -> Note:
    """设置笔记质量评分。"""
    note = get_note_by_id(session, note_id)
    score = float(score)
    if score < 1 or score > 5:
        raise ValueError("质量评分必须在1到5之间")
    note.quality_score = round(score, 1)
    session.commit()
    if reason:
        _quality_reasons[note.id] = reason[:200]
    return note


def evaluate_note_quality(session: Any, note_id: int) -> dict[str, Any]:
    """立即执行一次 AI 质量评估并保存分数。"""
    note = get_note_by_id(session, note_id)
    result = evaluate_with_ai(note)
    note.quality_score = result["score"]
    session.commit()
    return result


def evaluate_with_ai(note: Note) -> dict[str, Any]:
    """调用 AI 服务评估单条笔记，并缓存理由。"""
    from services import ai_service

    result = ai_service.evaluate_quality(note.title, note.content)
    _quality_reasons[note.id] = result["reason"]
    return result


def regenerate_note(
    session: Any,
    note_id: int,
    style: str,
) -> tuple[Note, list[str]]:
    """按风格重新生成，并更新标签、关联和质量评分。"""
    from services import ai_service

    note = get_note_by_id(session, note_id)
    rewritten = ai_service.rewrite_with_style(note.title, note.content, style)
    tags = clean_tag_list(ai_service.generate_tags(note.title, rewritten))
    note.content = rewritten
    note.tags = json.dumps(tags, ensure_ascii=False)
    note.related_ids = "[]"
    note.updated_at = datetime.now()
    session.commit()
    session.refresh(note)

    warnings: list[str] = []
    try:
        vector_service.update_note(note.id, note.title, note.content, tags)
        _refresh_related(note, session, commit=True)
    except vector_service.VectorError as exc:
        warnings.append(str(exc))
        logger.warning("重生成后向量更新失败：%s", exc)

    try:
        evaluation = evaluate_with_ai(note)
        note.quality_score = evaluation["score"]
        session.commit()
    except Exception as exc:  # noqa: BLE001 - AI 异常不应抹掉已生成正文
        warnings.append(str(exc))
        logger.warning("重生成后质量评估失败：%s", exc)

    return note, warnings


def serialize_note(note: Note) -> dict[str, Any]:
    """笔记转前端字典。"""
    attendees = []
    if note.meeting_attendees:
        attendees = [name for name in (part.strip() for part in re.split(r"[,，]", note.meeting_attendees)) if name]
    current_session = object_session(note)
    task_ids = link_service.task_ids_for_note(note)

    def load_tasks() -> list[Any]:
        """读取关联任务摘要，失效 ID 自动跳过。"""
        from models.models import Task

        if current_session is not None:
            return [
                task for task_id in task_ids
                if (task := current_session.get(Task, task_id)) is not None
            ]

        from services.db import SessionLocal

        with SessionLocal() as db:
            return [
                task for task_id in task_ids
                if (task := db.get(Task, task_id)) is not None
            ]

    tasks = load_tasks()
    task_summaries = [
        {
            "id": task.id,
            "title": task.title,
            "category": task.category,
            "completed": task.completed,
        }
        for task in tasks
    ]
    return {
        "id": note.id,
        "title": note.title,
        "content": note.content,
        "original_url": note.original_url,
        "source": note.source,
        "tags": _note_tags(note),
        "related_ids": _related_ids(note),
        "task_ids": [task.id for task in tasks],
        "task_summaries": task_summaries,
        "task_count": len(tasks),
        "quality_score": note.quality_score,
        "quality_reason": _quality_reasons.get(
            note.id, "可重新评估获取详细理由"
        ),
        "category": note.category,
        "note_type": note.note_type or "普通",
        "meeting_time": note.meeting_time.isoformat(timespec="minutes") if note.meeting_time else None,
        "meeting_attendees": note.meeting_attendees,
        "attendee_list": attendees,
        "meeting_topic": note.meeting_topic,
        "meeting_todos": _meeting_todos.get(note.id, []),
        "created_at": note.created_at.isoformat() if note.created_at else None,
        "updated_at": note.updated_at.isoformat() if note.updated_at else None,
        "vector_indexed": bool(getattr(note, "vector_indexed", True)),
        "vector_warning": str(getattr(note, "vector_warning", "")),
    }


# ============ 后台质量评估 ============
def enqueue_quality_evaluation(note_id: int) -> None:
    """把笔记 ID 放入质量评估队列；重复 ID 不重复入队。"""
    global _quality_worker_started

    with _quality_lock:
        if note_id in _quality_pending:
            return
        _quality_pending.add(note_id)
    _quality_queue.put(note_id)

    with _quality_lock:
        if not _quality_worker_started:
            worker = threading.Thread(target=_quality_worker, daemon=True)
            worker.start()
            _quality_worker_started = True


def _quality_worker() -> None:
    """串行执行质量评估，失败只记录日志。"""
    while True:
        note_id = _quality_queue.get()
        try:
            from services.db import SessionLocal

            with SessionLocal() as session:
                note = session.get(Note, note_id)
                if note is not None:
                    result = evaluate_with_ai(note)
                    note.quality_score = result["score"]
                    session.commit()
        except Exception as exc:  # noqa: BLE001 - 后台任务不能退出
            logger.warning("笔记 %s 质量评估失败：%s", note_id, exc)
        finally:
            with _quality_lock:
                _quality_pending.discard(note_id)
            _quality_queue.task_done()
