# -*- coding: utf-8 -*-
"""
个人工作台 v2.6 · FastAPI 主入口。

- 启动时初始化 SQLite、RSS 调度器，并在需要时后台同步向量库
- 提供链接处理、文件上传、RSS、笔记管理、语义搜索、RAG 问答和 Agent 自然语言操作接口
- 所有 API 统一返回 {code, message, data}
"""

from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy.orm import Session

import config
from services import (
    ai_service,
    agent_service,
    cache_service,
    crawler_service,
    file_service,
    note_service,
    rag_service,
    rss_service,
    schedule_service,
    scheduler_service,
    task_service,
    async_task_service,
    vector_service,
)
from services.db import get_db, init_db


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """应用生命周期：启动后台服务，关闭时清理调度线程。"""
    init_db()
    scheduler_service.start_scheduler()
    # SQLite 有笔记但向量库为空时后台补齐，不阻塞 FastAPI 启动
    vector_service.start_startup_sync_if_needed()
    async_task_service.start_worker()
    yield
    scheduler_service.stop_scheduler()


app = FastAPI(
    title=config.APP_NAME,
    version=config.APP_VERSION,
    lifespan=lifespan,
)

STATIC_DIR = Path(__file__).resolve().parent / "static"


# ============ 请求体模型 ============
class AIConfig(BaseModel):
    """AI 配置请求体。"""

    base_url: str = ""
    model: str = ""
    api_key: str = ""


class ProcessUrlRequest(BaseModel):
    """链接或手动粘贴正文处理请求体。"""

    mode: str = "url"
    url: str = ""
    title: str = ""
    content: str = ""


class NoteCreateRequest(BaseModel):
    """笔记保存请求体（前端预览确认后提交）。"""

    title: str = ""
    content: str = ""
    original_url: str | None = None
    tags: list[str] = []
    source: str = "手动输入"
    category: str = "默认"
    # v2.7 会议字段
    note_type: str = "普通"
    meeting_time: str | None = None
    meeting_attendees: str | None = None
    meeting_topic: str | None = None
    # AI 整理结果（保存整理纪要时传）和勾选要转任务的待办索引
    organized: dict[str, Any] | None = None
    selected_todos: list[int] = []


class NoteUpdateRequest(BaseModel):
    """笔记更新请求体：标题、正文、标签、分类及会议字段。"""

    title: str = ""
    content: str = ""
    tags: list[str] = []
    category: str = "默认"
    note_type: str | None = None
    meeting_time: str | None = None
    meeting_attendees: str | None = None
    meeting_topic: str | None = None


class RssSourceRequest(BaseModel):
    """RSS 源添加/更新请求体。"""

    name: str = ""
    url: str = ""
    focus_topics: str | None = None
    auto_process: bool = False
    ai_filter_enabled: bool = True


class FilterTestRequest(BaseModel):
    """测试 AI 筛选请求体。"""

    title: str = ""
    content: str = ""


class NoteRegenerateRequest(BaseModel):
    """笔记风格重生成请求体。"""

    style: str = "通俗"


class ProcessedSaveRequest(BaseModel):
    """待处理内容预览保存请求体。"""

    title: str = ""
    content: str = ""
    tags: list[str] = []


class BatchProcessRequest(BaseModel):
    """待处理内容批量处理请求体。"""

    item_ids: list[int] = []


class TaskCreateRequest(BaseModel):
    """日常/工作任务创建请求体。"""

    title: str
    category: str = "日常"
    priority: str = "中"
    due_date: str | None = None
    note_id: int | None = None


class TaskUpdateRequest(BaseModel):
    """日常/工作任务更新请求体；未传字段保持原值。"""

    title: str | None = None
    category: str | None = None
    priority: str | None = None
    due_date: str | None = None
    note_id: int | None = None


class ScheduleCreateRequest(BaseModel):
    """日程创建请求体。"""

    title: str
    schedule_type: str = "日常"
    start_time: str
    end_time: str | None = None
    location: str | None = None
    description: str | None = None
    note_id: int | None = None
    color: str | None = None


class ScheduleUpdateRequest(BaseModel):
    """日程更新请求体；未传字段保持原值。"""

    title: str | None = None
    schedule_type: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    location: str | None = None
    description: str | None = None
    note_id: int | None = None
    color: str | None = None


class SchedulerConfigRequest(BaseModel):
    """定时抓取配置请求体。"""

    enabled: bool = False
    fetch_time: str = "08:00"


class SearchRequest(BaseModel):
    """语义搜索请求体。"""

    query: str


class AskRequest(BaseModel):
    """RAG 问答请求体。"""

    question: str


class AgentChatRequest(BaseModel):
    """Agent 聊天请求体；历史只在本次请求和前端内存中存在。"""

    message: str
    history: list[dict[str, str]] = []


def ok(data: Any = None, message: str = "success") -> dict[str, Any]:
    """统一成功响应信封。"""
    return {"code": 0, "message": message, "data": data}


def fail(message: str, data: Any = None) -> dict[str, Any]:
    """统一业务失败响应信封。"""
    return {"code": 1, "message": message, "data": data}


# ============ 页面入口 ============
@app.get("/")
def index() -> FileResponse:
    """单页应用入口。"""
    return FileResponse(STATIC_DIR / "index.html")


# ============ 首页统计与 AI 配置 ============
@app.get("/api/stats")
def get_stats(db: Session = Depends(get_db)):
    """首页概览：笔记统计和任务统计。"""
    data = note_service.get_stats(db)
    data.update(task_service.get_stats(db))
    data["today_schedules"] = schedule_service.get_today_count(db)
    return ok(data)


@app.get("/api/ai-config")
def get_ai_config():
    """读取非敏感 AI 配置；不返回 Key。"""
    return ok(ai_service.get_config())


@app.post("/api/ai-config")
def post_ai_config(payload: AIConfig):
    """保存 AI 配置：base_url/model 写 json，api_key 非空时写 keyring。"""
    key_saved = ai_service.save_config(
        base_url=payload.base_url,
        model=payload.model,
        api_key=payload.api_key,
    )
    return ok({"saved": True, "key_saved": key_saved}, message="配置已保存")


@app.post("/api/ai-test")
def post_ai_test():
    """测试连接（维持占位）。"""
    return ok(None, message=ai_service.test_connection())


# ============ 链接 / 手动粘贴处理 ============
@app.post("/api/process-url")
def process_url(payload: ProcessUrlRequest):
    """创建链接或手动粘贴改写任务；任务成功后仍只返回预览。"""
    mode = payload.mode if payload.mode in ("url", "text") else "url"
    if mode == "url":
        if not payload.url.strip():
            return fail("请输入网页链接")
        task_type = "rewrite_url"
    else:
        if not payload.content.strip():
            return fail("请粘贴正文内容")
        task_type = "rewrite_text"

    params = {
        "mode": mode,
        "url": payload.url,
        "title": payload.title,
        "content": payload.content,
    }
    try:
        task_id = async_task_service.create_task(task_type, params)
    except async_task_service.AsyncTaskError as exc:
        return fail(str(exc))
    return ok({"task_id": task_id}, message="任务已创建，正在处理")


# ============ 文件上传解析 ============
MAX_UPLOAD_SIZE = 20 * 1024 * 1024


@app.post("/api/upload-file")
async def upload_file(file: UploadFile = File(...)):
    """保存上传文件、解析正文并 AI 改写，返回预览数据（本端点不写库）。"""
    safe_name = Path(file.filename or "").name
    if not safe_name:
        return fail("未获取到文件名")

    suffix = Path(safe_name).suffix.lower()
    if suffix not in file_service.ALLOWED_SUFFIXES:
        return fail("不支持的文件格式，仅支持 PDF、Word（docx）和 txt")

    data = await file.read()
    if len(data) > MAX_UPLOAD_SIZE:
        return fail("文件超过 20MB 大小限制")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    saved_name = f"{timestamp}_{safe_name}"
    saved_path = config.UPLOAD_DIR / saved_name
    saved_path.write_bytes(data)

    try:
        parsed = file_service.parse_file(saved_path, safe_name)
        rewritten = ai_service.rewrite_to_plain(
            parsed["title"], parsed["content"]
        )
        tags = ai_service.generate_tags(parsed["title"], parsed["content"])
    except (file_service.FileParseError, ai_service.AiError) as exc:
        return fail(str(exc))

    return ok({
        "title": parsed["title"],
        "content": rewritten,
        "tags": tags,
        "original_url": saved_name,
    })


# ============ 笔记 CRUD ============
@app.post("/api/notes")
def create_note(payload: NoteCreateRequest, db: Session = Depends(get_db)):
    """保存笔记；传 organized 时按 AI 整理结果存为会议记录并按勾选待办创建任务。"""
    task_info = None
    if payload.organized is not None:
        note, task_info = note_service.save_organized_meeting(
            db, payload.organized, selected_todos=set(payload.selected_todos)
        )
    else:
        note = note_service.create_note(
            session=db,
            title=payload.title,
            content=payload.content,
            original_url=payload.original_url,
            tags=payload.tags,
            source=payload.source,
            category=payload.category,
            note_type=payload.note_type,
            meeting_time=payload.meeting_time,
            meeting_attendees=payload.meeting_attendees,
            meeting_topic=payload.meeting_topic,
        )
    data = note_service.serialize_note(note)
    if task_info is not None:
        data["task_count"] = task_info["task_count"]
        if task_info["warnings"]:
            data["task_warnings"] = task_info["warnings"]
    message = "会议记录已保存" if payload.organized is not None else "笔记已保存"
    if task_info is not None and task_info["task_count"]:
        message += f"，已创建 {task_info['task_count']} 条任务"
    if data.get("vector_warning"):
        message += "，但向量索引失败，可稍后手动同步"
    return ok(data, message=message)


@app.get("/api/notes")
def list_notes(
    keyword: str = Query(default=""),
    tag: str = Query(default=""),
    category: str = Query(default=""),
    note_type: str = Query(default=""),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """笔记列表：支持关键词、标签、分类、类型过滤和分页。"""
    items, total = note_service.list_notes(
        db, keyword=keyword, tag=tag, category=category, note_type=note_type, page=page, size=size
    )
    return ok({
        "items": [note_service.serialize_note(note) for note in items],
        "total": total,
        "page": page,
        "size": size,
    })


# ============ 会议记录 ============
class MeetingCreateRequest(BaseModel):
    """手动创建会议记录请求体。"""

    title: str = ""
    content: str = ""
    meeting_time: str | None = None
    attendees: str | None = None
    topic: str | None = None
    tags: list[str] = []


class MeetingOrganizeRequest(BaseModel):
    """AI 整理会议纪要请求体。"""

    raw_text: str


@app.get("/api/notes/meetings")
def get_meetings(db: Session = Depends(get_db)):
    """返回全部会议记录。"""
    items = note_service.get_meeting_notes(db)
    return ok([note_service.serialize_note(item) for item in items])


@app.post("/api/notes/meeting/organize")
def organize_meeting_route(payload: MeetingOrganizeRequest):
    """创建 AI 整理会议纪要后台任务，立即返回任务 ID。"""
    if not str(payload.raw_text or "").strip():
        return fail("请粘贴会议记录内容")
    task_id = async_task_service.create_task(
        "organize_meeting", {"raw_text": payload.raw_text}
    )
    return ok({"task_id": task_id}, message="会议整理任务已创建")


@app.post("/api/notes/meeting")
def create_meeting_route(payload: MeetingCreateRequest, db: Session = Depends(get_db)):
    """手动创建会议记录。"""
    content = str(payload.content or "").strip()
    if not content:
        return fail("请填写会议内容")
    topic = str(payload.topic or "").strip()
    note = note_service.create_note(
        session=db,
        title=payload.title or topic or "会议记录",
        content=content,
        tags=payload.tags,
        source="会议记录",
        note_type="会议",
        meeting_time=payload.meeting_time,
        meeting_attendees=payload.attendees,
        meeting_topic=topic or None,
    )
    return ok(note_service.serialize_note(note), message="会议记录已创建")

@app.get("/api/notes/{note_id}")
def get_note(note_id: int, db: Session = Depends(get_db)):
    """笔记详情。"""
    try:
        note = note_service.get_note_by_id(db, note_id)
    except note_service.NoteNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ok(note_service.serialize_note(note))


@app.delete("/api/notes/{note_id}")
def delete_note(note_id: int, db: Session = Depends(get_db)):
    """直接真删笔记；向量删除失败不回滚笔记删除。"""
    try:
        result = note_service.delete_note(db, note_id)
    except note_service.NoteNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    message = "笔记已删除"
    if result.get("warning"):
        message = "笔记已删除，但向量索引删除失败，可稍后手动同步"
    return ok(result, message=message)


# ============ 标签、分类、笔记更新 ============
@app.get("/api/tags")
def get_tags(db: Session = Depends(get_db)):
    """全部标签及数量。"""
    return ok(note_service.all_tags(db))


@app.get("/api/categories")
def get_categories(db: Session = Depends(get_db)):
    """全部分类及数量。"""
    return ok(note_service.all_categories(db))


@app.get("/api/notes/{note_id}/related")
def get_related_notes(note_id: int, db: Session = Depends(get_db)):
    """返回笔记的向量相似关联笔记。"""
    try:
        items = note_service.get_related_notes(db, note_id)
    except note_service.NoteNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ok(items)


@app.post("/api/notes/{note_id}/evaluate")
def evaluate_note(note_id: int, db: Session = Depends(get_db)):
    """创建 AI 质量评估任务。"""
    try:
        note_service.get_note_by_id(db, note_id)
    except note_service.NoteNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    task_id = async_task_service.create_task("evaluate", {"note_id": note_id})
    return ok({"task_id": task_id}, message="质量评估任务已创建")


@app.post("/api/notes/{note_id}/regenerate")
def regenerate_note(
    note_id: int,
    payload: NoteRegenerateRequest,
    db: Session = Depends(get_db),
):
    """创建按风格重新生成任务。"""
    try:
        note_service.get_note_by_id(db, note_id)
    except note_service.NoteNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if payload.style not in ("通俗", "精简", "详细"):
        return fail("不支持的改写风格，只能选择通俗、精简或详细")
    task_id = async_task_service.create_task(
        "regenerate", {"note_id": note_id, "style": payload.style}
    )
    return ok({"task_id": task_id}, message="重新生成任务已创建")


@app.put("/api/notes/{note_id}")
def update_note(note_id: int, payload: NoteUpdateRequest, db: Session = Depends(get_db)):
    """更新笔记标题、正文、标签、分类。"""
    try:
        note = note_service.update_note(
            session=db,
            note_id=note_id,
            title=payload.title,
            content=payload.content,
            tags=payload.tags,
            category=payload.category,
            note_type=payload.note_type if payload.note_type is not None else note_service._UNSET,
            meeting_time=payload.meeting_time if payload.meeting_time is not None else note_service._UNSET,
            meeting_attendees=payload.meeting_attendees if payload.meeting_attendees is not None else note_service._UNSET,
            meeting_topic=payload.meeting_topic if payload.meeting_topic is not None else note_service._UNSET,
        )
    except note_service.NoteNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    data = note_service.serialize_note(note)
    message = "笔记已更新"
    if data.get("vector_warning"):
        message = "笔记已更新，但向量索引更新失败，可稍后手动同步"
    return ok(data, message=message)


# ============ RSS 订阅与待处理队列 ============
@app.get("/api/rss-sources")
def get_rss_sources(db: Session = Depends(get_db)):
    """返回全部 RSS 源。"""
    sources = rss_service.list_sources(db)
    return ok([rss_service.serialize_source(source) for source in sources])


@app.post("/api/rss-sources")
def post_rss_source(payload: RssSourceRequest, db: Session = Depends(get_db)):
    """验证并添加 RSS 源。"""
    try:
        source = rss_service.add_source(
            db,
            payload.name,
            payload.url,
            focus_topics=payload.focus_topics,
            auto_process=payload.auto_process,
            ai_filter_enabled=payload.ai_filter_enabled,
        )
    except rss_service.RssError as exc:
        return fail(str(exc))
    return ok(rss_service.serialize_source(source), message="RSS 源已添加")


@app.put("/api/rss-sources/{source_id}")
def put_rss_source(source_id: int, payload: RssSourceRequest, db: Session = Depends(get_db)):
    """更新 RSS 源名称和三个筛选配置。"""
    try:
        source = rss_service.update_source(
            db,
            source_id,
            name=payload.name,
            focus_topics=payload.focus_topics,
            auto_process=payload.auto_process,
            ai_filter_enabled=payload.ai_filter_enabled,
        )
    except rss_service.RssSourceNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except rss_service.RssError as exc:
        return fail(str(exc))
    return ok(rss_service.serialize_source(source), message="RSS 源已更新")


@app.post("/api/rss-sources/{source_id}/test-filter")
def test_rss_filter(source_id: int, payload: FilterTestRequest, db: Session = Depends(get_db)):
    """用该源自身的关注主题测试一条内容的 AI 判定结果。"""
    try:
        source = rss_service.get_source_by_id(db, source_id)
    except rss_service.RssSourceNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    topics = (source.focus_topics or "").strip()
    if not topics:
        return fail("这个 RSS 源还没有填写关注主题")
    result = ai_service.is_relevant(payload.title, payload.content, topics)
    return ok(result)


@app.delete("/api/rss-sources/{source_id}")
def delete_rss_source(source_id: int, db: Session = Depends(get_db)):
    """删除 RSS 源。"""
    try:
        rss_service.delete_source(db, source_id)
    except rss_service.RssSourceNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ok(None, message="RSS 源已删除")


@app.post("/api/rss-sources/{source_id}/fetch")
def fetch_rss_source(source_id: int, db: Session = Depends(get_db)):
    """手动抓取单个 RSS 源。"""
    try:
        stats = rss_service.fetch_source(db, source_id)
    except rss_service.RssSourceNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except rss_service.RssError as exc:
        return fail(str(exc))
    message = (
        f"本次发现 {stats['added']} 条，"
        f"相关进入待处理 {stats['pending_count']} 条，"
        f"自动保存 {stats['auto_saved_count']} 条，"
        f"筛选掉 {stats['filtered_count']} 条"
    )
    return ok(stats, message=message)


@app.post("/api/rss/fetch-all")
def fetch_all_rss(db: Session = Depends(get_db)):
    """抓取全部 RSS 源。"""
    return ok(rss_service.fetch_all_sources(db), message="全部源抓取完成")


@app.get("/api/pending")
def get_pending(db: Session = Depends(get_db)):
    """返回待处理和已跳过队列。"""
    items = rss_service.list_pending(db)
    return ok([rss_service.serialize_pending(item) for item in items])


@app.post("/api/pending/{item_id}/process")
def process_pending(item_id: int, db: Session = Depends(get_db)):
    """抓取正文并 AI 改写，返回预览但不保存。"""
    try:
        preview = rss_service.process_pending_item(db, item_id)
    except rss_service.PendingNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (
        rss_service.RssError,
        crawler_service.CrawlError,
        ai_service.AiError,
    ) as exc:
        return fail(str(exc))
    return ok(preview, message="改写完成，请确认后保存")


@app.post("/api/pending/{item_id}/save")
def save_pending(
    item_id: int,
    payload: ProcessedSaveRequest,
    db: Session = Depends(get_db),
):
    """保存预览笔记并标记队列项完成。"""
    try:
        note = rss_service.save_processed_item(
            db, item_id, payload.title, payload.content, payload.tags
        )
    except rss_service.PendingNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except rss_service.RssError as exc:
        return fail(str(exc))

    data = note_service.serialize_note(note)
    message = "笔记已保存"
    if data.get("vector_warning"):
        message = "笔记已保存，但向量索引失败，可稍后手动同步"
    return ok(data, message=message)


@app.post("/api/pending/{item_id}/skip")
def skip_pending(item_id: int, db: Session = Depends(get_db)):
    """跳过待处理项。"""
    try:
        rss_service.skip_pending_item(db, item_id)
    except rss_service.PendingNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except rss_service.RssError as exc:
        return fail(str(exc))
    return ok(None, message="已跳过")


@app.delete("/api/pending/{item_id}")
def delete_pending(item_id: int, db: Session = Depends(get_db)):
    """删除待处理项。"""
    try:
        rss_service.delete_pending_item(db, item_id)
    except rss_service.PendingNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ok(None, message="待处理项已删除")


# ============ 后台任务、缓存和批量处理 ============
@app.get("/api/async-tasks/{task_id}")
def get_async_task(task_id: int):
    """查询单个后台异步任务。"""
    try:
        task = async_task_service.get_task(task_id)
    except async_task_service.AsyncTaskNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ok(task)


@app.get("/api/async-tasks")
def list_async_tasks(limit: int = Query(default=20, ge=1, le=100)):
    """查询最近后台异步任务。"""
    return ok(async_task_service.list_tasks(limit))


@app.post("/api/cache/clear")
def clear_cache():
    """清空全部本地缓存。"""
    count = cache_service.clear_all()
    return ok({"removed": count}, message=f"已清空 {count} 个缓存文件")


@app.post("/api/pending/batch-process")
def batch_process_pending(payload: BatchProcessRequest):
    """创建批量处理任务；任务按顺序执行并直接保存。"""
    item_ids = list(dict.fromkeys(payload.item_ids))
    if not item_ids:
        return fail("请先选择待处理内容")
    if len(item_ids) > 50:
        return fail("单次最多批量处理 50 条")
    task_id = async_task_service.create_task(
        "batch_process", {"item_ids": item_ids}
    )
    return ok({"task_id": task_id}, message="批量处理任务已创建")


# ============ 日常/工作任务管理 ============
@app.post("/api/tasks")
def create_user_task(payload: TaskCreateRequest, db: Session = Depends(get_db)):
    """创建日常或工作任务。"""
    try:
        task = task_service.create_task(
            db,
            payload.title,
            payload.category,
            payload.priority,
            payload.due_date,
            payload.note_id,
        )
    except task_service.TaskError as exc:
        return fail(str(exc))
    return ok(task_service.serialize_task(task), message="任务已创建")


@app.get("/api/tasks/today")
def get_today_user_tasks(db: Session = Depends(get_db)):
    """获取今日待办。"""
    items = task_service.get_today_tasks(db)
    return ok([task_service.serialize_task(task) for task in items])


@app.get("/api/tasks/stats")
def get_user_task_stats(db: Session = Depends(get_db)):
    """获取任务统计。"""
    return ok(task_service.get_stats(db))


@app.get("/api/tasks")
def list_user_tasks(
    category: str | None = None,
    completed: bool | None = None,
    db: Session = Depends(get_db),
):
    """查询任务列表，可按分类和完成状态筛选。"""
    try:
        items = task_service.list_tasks(db, category=category, completed=completed)
    except task_service.TaskError as exc:
        return fail(str(exc))
    return ok([task_service.serialize_task(task) for task in items])


@app.get("/api/tasks/{task_id}")
def get_user_task(task_id: int, db: Session = Depends(get_db)):
    """获取单个任务。"""
    try:
        task = task_service.get_task(db, task_id)
    except task_service.TaskNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ok(task_service.serialize_task(task))


@app.put("/api/tasks/{task_id}")
def update_user_task(
    task_id: int,
    payload: TaskUpdateRequest,
    db: Session = Depends(get_db),
):
    """更新任务基础信息。"""
    try:
        task = task_service.update_task(
            db, task_id, **payload.model_dump(exclude_unset=True)
        )
    except task_service.TaskNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except task_service.TaskError as exc:
        return fail(str(exc))
    return ok(task_service.serialize_task(task), message="任务已更新")


@app.delete("/api/tasks/{task_id}")
def delete_user_task(task_id: int, db: Session = Depends(get_db)):
    """删除任务。"""
    try:
        task_service.delete_task(db, task_id)
    except task_service.TaskNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ok(None, message="任务已删除")


@app.post("/api/tasks/{task_id}/complete")
def complete_user_task(task_id: int, db: Session = Depends(get_db)):
    """标记任务完成。"""
    try:
        task = task_service.complete_task(db, task_id)
    except task_service.TaskNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ok(task_service.serialize_task(task), message="任务已完成")


@app.post("/api/tasks/{task_id}/uncomplete")
def uncomplete_user_task(task_id: int, db: Session = Depends(get_db)):
    """取消任务完成状态。"""
    try:
        task = task_service.uncomplete_task(db, task_id)
    except task_service.TaskNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ok(task_service.serialize_task(task), message="已取消完成")


# ============ 日程计划与日历 ============
@app.get("/api/schedules/today")
def get_today_schedules(db: Session = Depends(get_db)):
    """获取今日日程。"""
    items = schedule_service.get_today_schedules(db)
    return ok([schedule_service.serialize_schedule(item) for item in items])


@app.get("/api/schedules/month")
def get_month_schedules(year: int, month: int, db: Session = Depends(get_db)):
    """获取某月日程。"""
    try:
        items = schedule_service.get_month_schedules(db, year, month)
    except schedule_service.ScheduleError as exc:
        return fail(str(exc))
    return ok([schedule_service.serialize_schedule(item) for item in items])


@app.get("/api/schedules")
def list_schedules(
    start_date: str,
    end_date: str,
    db: Session = Depends(get_db),
):
    """查询日期范围内的日程。"""
    try:
        items = schedule_service.list_schedules(db, start_date, end_date)
    except schedule_service.ScheduleError as exc:
        return fail(str(exc))
    return ok([schedule_service.serialize_schedule(item) for item in items])


@app.post("/api/schedules")
def create_schedule(payload: ScheduleCreateRequest, db: Session = Depends(get_db)):
    """创建日程。"""
    try:
        schedule = schedule_service.create_schedule(
            db,
            payload.title,
            payload.schedule_type,
            payload.start_time,
            payload.end_time,
            payload.location,
            payload.description,
            payload.note_id,
            payload.color,
        )
    except schedule_service.ScheduleError as exc:
        return fail(str(exc))
    return ok(schedule_service.serialize_schedule(schedule), message="日程已创建")


@app.get("/api/schedules/{schedule_id}")
def get_schedule(schedule_id: int, db: Session = Depends(get_db)):
    """获取单个日程。"""
    try:
        schedule = schedule_service.get_schedule(db, schedule_id)
    except schedule_service.ScheduleNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ok(schedule_service.serialize_schedule(schedule))


@app.put("/api/schedules/{schedule_id}")
def update_schedule(
    schedule_id: int,
    payload: ScheduleUpdateRequest,
    db: Session = Depends(get_db),
):
    """更新日程。"""
    try:
        schedule = schedule_service.update_schedule(
            db, schedule_id, **payload.model_dump(exclude_unset=True)
        )
    except schedule_service.ScheduleNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except schedule_service.ScheduleError as exc:
        return fail(str(exc))
    return ok(schedule_service.serialize_schedule(schedule), message="日程已更新")


@app.delete("/api/schedules/{schedule_id}")
def delete_schedule(schedule_id: int, db: Session = Depends(get_db)):
    """删除日程。"""
    try:
        schedule_service.delete_schedule(db, schedule_id)
    except schedule_service.ScheduleNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ok(None, message="日程已删除")


# ============ 定时自动抓取 ============
@app.get("/api/scheduler/config")
def get_scheduler_config():
    """读取定时抓取配置。"""
    return ok(scheduler_service.load_config())


@app.put("/api/scheduler/config")
def put_scheduler_config(payload: SchedulerConfigRequest):
    """更新定时抓取开关和每日执行时间。"""
    try:
        cfg = scheduler_service.update_schedule(
            payload.enabled, payload.fetch_time
        )
    except scheduler_service.SchedulerError as exc:
        return fail(str(exc))
    return ok(cfg, message="定时抓取设置已保存")


@app.get("/api/scheduler/logs")
def get_scheduler_logs(limit: int = Query(default=50, ge=1, le=200)):
    """读取最近抓取日志。"""
    return ok(scheduler_service.get_logs(limit))


@app.post("/api/scheduler/run-now")
def scheduler_run_now():
    """立即执行一次全部 RSS 抓取。"""
    try:
        result = scheduler_service.run_now()
    except scheduler_service.SchedulerBusy as exc:
        return fail(str(exc))
    return ok(result, message=f"抓取完成，新增 {result['total_added']} 条")


# ============ v1.6：语义搜索、RAG 问答、向量同步 ============
@app.post("/api/search")
def semantic_search(payload: SearchRequest, db: Session = Depends(get_db)):
    """语义搜索笔记，返回完整笔记和相关度分数。"""
    try:
        search_results = vector_service.search_notes(payload.query, n_results=10)
    except vector_service.VectorError as exc:
        return fail(str(exc))

    items: list[dict[str, Any]] = []
    for result in search_results:
        try:
            note = note_service.get_note_by_id(db, result["id"])
        except note_service.NoteNotFound:
            continue
        item = note_service.serialize_note(note)
        item["score"] = result["score"]
        items.append(item)
    return ok({"items": items, "total": len(items)})


@app.post("/api/ask")
def ask(payload: AskRequest, db: Session = Depends(get_db)):
    """基于笔记库进行单轮 RAG 问答。"""
    try:
        result = rag_service.answer_question(payload.question, db)
    except (vector_service.VectorError, ai_service.AiError) as exc:
        return fail(str(exc))
    return ok(result)


@app.get("/api/vector/stats")
def vector_stats():
    """读取向量库数量和同步状态。"""
    try:
        stats = vector_service.get_stats()
    except vector_service.VectorError as exc:
        return fail(str(exc))
    return ok(stats)


@app.post("/api/vector/sync")
def vector_sync():
    """后台启动全量向量同步。"""
    try:
        vector_service.start_background_sync()
    except vector_service.VectorError as exc:
        return fail(str(exc))
    return ok({"status": "running"}, message="向量同步已开始，首次下载模型可能较慢")


# ============ v2.0：Agent Function Calling ============
@app.get("/api/agent/tools")
def get_agent_tools():
    """返回 Agent 可用工具定义。"""
    return ok(agent_service.list_tools())


@app.post("/api/agent/chat")
def agent_chat(payload: AgentChatRequest, db: Session = Depends(get_db)):
    """自然语言操作入口：Function Calling、工具执行和自然语言总结。"""
    try:
        result = agent_service.chat(
            payload.message, payload.history, db
        )
    except (agent_service.AgentError, ai_service.AiError) as exc:
        return fail(str(exc))
    return ok(result)


# 静态资源放在 /static 下，需在所有路由之后挂载
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
