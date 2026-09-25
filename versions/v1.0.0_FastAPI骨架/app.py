# -*- coding: utf-8 -*-
"""
知识消化平台 v1.0 · FastAPI 主入口。

- 启动时初始化数据目录和 SQLite 表
- 挂载 /static 提供前端资源
- 5 个 API 端点（v1.0 全部占位返回）
- 根路径返回单页应用入口 index.html
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import config
from services import ai_service
from services.db import init_db

# 启动时确保表就绪（幂等）
init_db()

app = FastAPI(title=config.APP_NAME, version=config.APP_VERSION)

STATIC_DIR = Path(__file__).resolve().parent / "static"


class AIConfig(BaseModel):
    """AI 配置请求体。"""

    base_url: str = ""
    model: str = ""
    api_key: str = ""


def ok(data=None, message: str = "success"):
    """统一成功响应信封。"""
    return {"code": 0, "message": message, "data": data}


@app.get("/")
def index() -> FileResponse:
    """单页应用入口。"""
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/stats")
def get_stats():
    """首页三个概览卡片的数据源（v1.0 返回固定 0，v1.2 接真实统计）。"""
    return ok({"total_notes": 0, "today_notes": 0, "pending": 0})


@app.get("/api/notes")
def list_notes():
    """笔记列表（v1.0 返回空数组，v1.1 接真实查询）。"""
    return ok([])


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
    """测试 AI 连接（v1.0 占位，不真发请求）。"""
    return ok(None, message=ai_service.test_connection())


# 静态资源（CSS / JS）放在 /static 下
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
