# -*- coding: utf-8 -*-
"""
知识消化平台 v1.5 · FastAPI 主入口。

- 启动时初始化数据目录和 SQLite 表
- 挂载 /static 提供前端资源
- 保留既有功能；新增定时自动抓取和抓取日志
- 根路径返回单页应用入口 index.html
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
    ai_service, crawler_service, file_service,
    note_service, rss_service, scheduler_service,
)
from services.db import get_db, init_db

# 启动时确保表就绪（幂等）
init_db()

@asynccontextmanager
async def lifespan(_app: FastAPI):
    """应用启动时启动调度器，关闭时停止后台线程。"""
    init_db()
    scheduler_service.start_scheduler()
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
    """链接处理请求体。"""

    url: str


class NoteCreateRequest(BaseModel):
    """笔记保存请求体（前端预览确认后提交）。"""

    title: str = ""
    content: str = ""
    original_url: str | None = None
    tags: list[str] = []
    source: str = "手动输入"
    category: str = "默认"


class NoteUpdateRequest(BaseModel):
    """笔记更新请求体：标题、正文、标签、分类。"""

    title: str = ""
    content: str = ""
    tags: list[str] = []
    category: str = "默认"


class RssSourceRequest(BaseModel):
    """RSS 源添加请求体。"""

    name: str = ""
    url: str = ""


class ProcessedSaveRequest(BaseModel):
    """待处理内容预览保存请求体。"""

    title: str = ""
    content: str = ""
    tags: list[str] = []


class SchedulerConfigRequest(BaseModel):
    """定时抓取配置请求体。"""

    enabled: bool = False
    fetch_time: str = "08:00"


def ok(data: Any = None, message: str = "success") -> dict[str, Any]:
    """统一成功响应信封。"""
    return {"code": 0, "message": message, "data": data}


# ============ 页面入口与 v1.0 端点 ============
@app.get("/")
def index() -> FileResponse:
    """单页应用入口。"""
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/stats")
def get_stats(db: Session = Depends(get_db)):
    """首页概览：真实统计数据。"""
    return ok(note_service.get_stats(db))


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
    """测试连接（本期维持 v1.0 占位）。"""
    return ok(None, message=ai_service.test_connection())


# ============ v1.1：链接处理 ============
@app.post("/api/process-url")
def process_url(payload: ProcessUrlRequest):
    """抓取网页正文并 AI 改写，返回预览数据（本端点不写库）。"""
    try:
        # 第一步：抓取网页
        html_bytes, final_url = crawler_service.fetch_url(payload.url)
        # 第二步：提取标题和正文
        extracted = crawler_service.extract_content(html_bytes, final_url)
        # 第三步：AI 改写成通俗笔记
        rewritten = ai_service.rewrite_to_plain(
            extracted["title"], extracted["content"]
        )
        # 第四步：生成标签，失败降级空列表
        tags = ai_service.generate_tags(extracted["title"], extracted["content"])
    except (crawler_service.CrawlError, ai_service.AiError) as exc:
        # 抓取或改写失败：统一返回 code=1，不抛 500
        return {"code": 1, "message": str(exc), "data": None}

    return ok({
        "title": extracted["title"],
        "content": rewritten,
        "tags": tags,
        "original_url": final_url,
    })


# ============ v1.3：文件上传解析 ============
# 文件大小上限 20MB
MAX_UPLOAD_SIZE = 20 * 1024 * 1024


@app.post("/api/upload-file")
async def upload_file(file: UploadFile = File(...)):
    """保存上传文件、解析正文并 AI 改写，返回预览数据（本端点不写库）。"""
    # 只取文件名，防止客户端传入带路径的文件名造成路径穿越
    safe_name = Path(file.filename or "").name
    if not safe_name:
        return {"code": 1, "message": "未获取到文件名", "data": None}

    suffix = Path(safe_name).suffix.lower()
    if suffix not in file_service.ALLOWED_SUFFIXES:
        return {"code": 1,
                "message": "不支持的文件格式，仅支持 PDF、Word（docx）和 txt",
                "data": None}

    data = await file.read()
    if len(data) > MAX_UPLOAD_SIZE:
        return {"code": 1, "message": "文件超过 20MB 大小限制", "data": None}

    # 文件名加时间戳，避免重名覆盖
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
        return {"code": 1, "message": str(exc), "data": None}

    return ok({
        "title": parsed["title"],
        "content": rewritten,
        "tags": tags,
        "original_url": saved_name,
    })


# ============ v1.1：笔记 CRUD ============
@app.post("/api/notes")
def create_note(payload: NoteCreateRequest, db: Session = Depends(get_db)):
    """保存预览确认后的笔记。"""
    note = note_service.create_note(
        session=db,
        title=payload.title,
        content=payload.content,
        original_url=payload.original_url,
        tags=payload.tags,
        source=payload.source,
        category=payload.category,
    )
    return ok(note_service.serialize_note(note), message="笔记已保存")


@app.get("/api/notes")
def list_notes(
    keyword: str = Query(default=""),
    tag: str = Query(default=""),
    category: str = Query(default=""),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """笔记列表：支持关键词、标签、分类过滤和分页，按创建时间倒序。"""
    items, total = note_service.list_notes(
        db, keyword=keyword, tag=tag, category=category, page=page, size=size
    )
    return ok({
        "items": [note_service.serialize_note(note) for note in items],
        "total": total,
        "page": page,
        "size": size,
    })


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
    """直接真删笔记。"""
    try:
        note_service.delete_note(db, note_id)
    except note_service.NoteNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ok(None, message="笔记已删除")


# ============ v1.2：标签、分类、笔记更新 ============
@app.get("/api/tags")
def get_tags(db: Session = Depends(get_db)):
    """全部标签及数量。"""
    return ok(note_service.all_tags(db))


@app.get("/api/categories")
def get_categories(db: Session = Depends(get_db)):
    """全部分类及数量。"""
    return ok(note_service.all_categories(db))


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
        )
    except note_service.NoteNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ok(note_service.serialize_note(note), message="笔记已更新")



# ============ v1.4：RSS 订阅与待处理队列 ============
@app.get("/api/rss-sources")
def get_rss_sources(db: Session = Depends(get_db)):
    """返回全部 RSS 源。"""
    sources = rss_service.list_sources(db)
    return ok([rss_service.serialize_source(source) for source in sources])


@app.post("/api/rss-sources")
def post_rss_source(payload: RssSourceRequest, db: Session = Depends(get_db)):
    """验证并添加 RSS 源。"""
    try:
        source = rss_service.add_source(db, payload.name, payload.url)
    except rss_service.RssError as exc:
        return {"code": 1, "message": str(exc), "data": None}
    return ok(rss_service.serialize_source(source), message="RSS 源已添加")


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
        added = rss_service.fetch_source(db, source_id)
    except rss_service.RssSourceNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except rss_service.RssError as exc:
        return {"code": 1, "message": str(exc), "data": None}
    return ok({"added": added}, message=f"抓取完成，新增 {added} 条")


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
        return {"code": 1, "message": str(exc), "data": None}
    return ok(preview, message="改写完成，请确认后保存")


@app.post("/api/pending/{item_id}/save")
def save_pending(item_id: int, payload: ProcessedSaveRequest, db: Session = Depends(get_db)):
    """保存预览笔记并标记队列项完成。"""
    try:
        note = rss_service.save_processed_item(
            db, item_id, payload.title, payload.content, payload.tags
        )
    except rss_service.PendingNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except rss_service.RssError as exc:
        return {"code": 1, "message": str(exc), "data": None}
    return ok(note_service.serialize_note(note), message="笔记已保存")


@app.post("/api/pending/{item_id}/skip")
def skip_pending(item_id: int, db: Session = Depends(get_db)):
    """跳过待处理项。"""
    try:
        rss_service.skip_pending_item(db, item_id)
    except rss_service.PendingNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except rss_service.RssError as exc:
        return {"code": 1, "message": str(exc), "data": None}
    return ok(None, message="已跳过")


@app.delete("/api/pending/{item_id}")
def delete_pending(item_id: int, db: Session = Depends(get_db)):
    """删除待处理项。"""
    try:
        rss_service.delete_pending_item(db, item_id)
    except rss_service.PendingNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ok(None, message="待处理项已删除")



# ============ v1.5：定时自动抓取 ============
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
        return {"code": 1, "message": str(exc), "data": None}
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
        return {"code": 1, "message": str(exc), "data": None}
    return ok(result, message=f"抓取完成，新增 {result['total_added']} 条")


# 静态资源（CSS / JS）放在 /static 下，需在所有路由之后挂载
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
