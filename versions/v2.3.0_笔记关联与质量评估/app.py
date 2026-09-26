# -*- coding: utf-8 -*-
"""
知识消化平台 v2.3 · FastAPI 主入口。

- 启动时初始化 SQLite、RSS 调度器，并在需要时后台同步向量库
- 提供链接处理、文件上传、RSS、笔记管理、语义搜索、RAG 问答和 Agent 自然语言操作接口
- 所有 API 统一返回 {code, message, data}
"""

import json
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy.orm import Session

import config
from services import (
    ai_service,
    agent_service,
    crawler_service,
    file_service,
    note_service,
    rag_service,
    rss_service,
    scheduler_service,
    vector_service,
)
from services.db import SessionLocal, get_db, init_db


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """应用生命周期：启动后台服务，关闭时清理调度线程。"""
    init_db()
    scheduler_service.start_scheduler()
    # SQLite 有笔记但向量库为空时后台补齐，不阻塞 FastAPI 启动
    vector_service.start_startup_sync_if_needed()
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
    """测试连接（维持占位）。"""
    return ok(None, message=ai_service.test_connection())


# ============ 链接 / 手动粘贴处理 ============
@app.post("/api/process-url")
def process_url(payload: ProcessUrlRequest):
    """抓取网页或处理手动粘贴正文，AI 改写后返回预览数据（本端点不写库）。"""
    mode = payload.mode if payload.mode in ("url", "text") else "url"
    try:
        if mode == "url":
            return ok(_process_url_mode(payload))
        return ok(_process_text_mode(payload))
    except crawler_service.CrawlError as exc:
        stage = "crawl" if mode == "url" else "text"
        return fail(str(exc), {"stage": stage})
    except ai_service.AiError as exc:
        return fail(str(exc), {"stage": "ai"})


def _process_url_mode(payload: ProcessUrlRequest) -> dict[str, Any]:
    """链接抓取模式。"""
    html_bytes, final_url = crawler_service.fetch_url(payload.url)
    extracted = crawler_service.extract_content(html_bytes, final_url)
    rewritten = ai_service.rewrite_to_plain(
        extracted["title"], extracted["content"]
    )
    tags = ai_service.generate_tags(extracted["title"], extracted["content"])
    return {
        "title": extracted["title"],
        "content": rewritten,
        "tags": tags,
        "original_url": final_url,
        "source": "手动输入",
    }


def _process_text_mode(payload: ProcessUrlRequest) -> dict[str, Any]:
    """手动粘贴模式；不发起网络请求。"""
    extracted = crawler_service.extract_from_text(
        payload.title, payload.content, payload.url
    )
    title = extracted["title"]
    if not title:
        try:
            title = ai_service.generate_title(extracted["content"]) or "无标题"
        except ai_service.AiError:
            title = "无标题"
    rewritten = ai_service.rewrite_to_plain(title, extracted["content"])
    tags = ai_service.generate_tags(title, extracted["content"])
    return {
        "title": title,
        "content": rewritten,
        "tags": tags,
        "original_url": extracted["url"] or None,
        "source": "手动粘贴",
    }


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
    """保存预览确认后的笔记；向量失败时保留笔记并返回警告。"""
    note = note_service.create_note(
        session=db,
        title=payload.title,
        content=payload.content,
        original_url=payload.original_url,
        tags=payload.tags,
        source=payload.source,
        category=payload.category,
    )
    data = note_service.serialize_note(note)
    message = "笔记已保存"
    if data.get("vector_warning"):
        message = f"笔记已保存，但向量索引失败，可稍后手动同步"
    return ok(data, message=message)


@app.get("/api/notes")
def list_notes(
    keyword: str = Query(default=""),
    tag: str = Query(default=""),
    category: str = Query(default=""),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """笔记列表：支持关键词、标签、分类过滤和分页。"""
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
    """立即执行 AI 质量评估。"""
    try:
        result = note_service.evaluate_note_quality(db, note_id)
    except note_service.NoteNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ai_service.AiError, ValueError) as exc:
        return fail(str(exc))
    return ok(result, message="质量评估完成")


@app.post("/api/notes/{note_id}/regenerate")
def regenerate_note(
    note_id: int,
    payload: NoteRegenerateRequest,
    db: Session = Depends(get_db),
):
    """按指定风格重新生成正文、标签、关联和评分。"""
    try:
        note, warnings = note_service.regenerate_note(
            db, note_id, payload.style
        )
    except note_service.NoteNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ai_service.AiError as exc:
        return fail(str(exc))

    message = "笔记已重新生成"
    if warnings:
        message = f"笔记已重新生成，但存在警告：{'；'.join(warnings)}"
    return ok(note_service.serialize_note(note), message=message)


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
        source = rss_service.add_source(db, payload.name, payload.url)
    except rss_service.RssError as exc:
        return fail(str(exc))
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
        return fail(str(exc))
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


# ============ 待处理内容批量处理 ============
@app.post("/api/pending/batch-process")
def batch_process_pending(payload: BatchProcessRequest):
    """顺序处理并直接保存；通过 NDJSON 实时返回进度。"""
    item_ids = list(dict.fromkeys(payload.item_ids))
    if not item_ids:
        return fail("请先选择待处理内容")
    if len(item_ids) > 50:
        return fail("单次最多批量处理 50 条")
    return StreamingResponse(
        _batch_process_events(item_ids),
        media_type="application/x-ndjson",
    )


def _batch_process_events(item_ids: list[int]):
    """生成批量处理流式事件。"""
    total = len(item_ids)
    results = []
    success_count = failed_count = 0

    for index, item_id in enumerate(item_ids, start=1):
        progress = {
            "type": "progress",
            "index": index,
            "total": total,
            "item_id": item_id,
        }
        yield _ndjson_line(progress, message="progress")

        try:
            with SessionLocal() as db:
                preview = rss_service.process_pending_item(db, item_id)
                note = rss_service.save_processed_item(
                    db,
                    item_id,
                    preview["title"],
                    preview["content"],
                    preview.get("tags", []),
                )
                note_id = note.id
            result = {
                "type": "item",
                "index": index,
                "item_id": item_id,
                "status": "success",
                "note_id": note_id,
            }
            success_count += 1
        except Exception as exc:  # 单条失败不能中断批量任务
            result = {
                "type": "item",
                "index": index,
                "item_id": item_id,
                "status": "failed",
                "error": str(exc) or "处理失败",
            }
            failed_count += 1
        results.append(result)
        yield _ndjson_line(result, message="item")

    summary = {
        "type": "summary",
        "success": success_count,
        "failed": failed_count,
        "results": results,
    }
    yield _ndjson_line(summary, message="success")


def _ndjson_line(data: dict[str, Any], message: str) -> str:
    """构造一条统一信封格式的 NDJSON 文本。"""
    return json.dumps(
        {"code": 0, "message": message, "data": data},
        ensure_ascii=False,
    ) + "\n"


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
