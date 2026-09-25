# -*- coding: utf-8 -*-
"""
v2 项目 FastAPI 入口。

- 启动时初始化数据目录和 SQLite 表
- 挂载 /static 提供前端资源
- 4 个占位 API 端点（v1.0 只返回骨架数据）
- 根路径返回单页应用入口 index.html
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from v2 import config
from v2.ai_client import (
    KeyringError,
    get_api_key,
    is_configured,
    load_config,
    save_config,
    set_api_key,
)
from v2.database import init_db
from v2.schemas import ApiResult

# 启动时确保表就绪（幂等）
init_db()

app = FastAPI(title=config.APP_NAME, version=config.APP_VERSION)

STATIC_DIR = Path(__file__).resolve().parent / "static"


@app.get("/")
def index() -> FileResponse:
    """单页应用入口。"""
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/stats", response_model=ApiResult)
def get_stats() -> ApiResult:
    """首页三个概览卡片的数据源（v1.0 返回固定 0，v1.2 接真实统计）。"""
    return ApiResult.ok({"total_notes": 0, "today_new": 0, "pending_count": 0})


@app.get("/api/notes", response_model=ApiResult)
def list_notes(keyword: str = "", tag: str = "") -> ApiResult:
    """笔记列表（v1.0 返回空数组，v1.1 接真实查询）。"""
    return ApiResult.ok([])


@app.get("/api/ai-config", response_model=ApiResult)
def get_ai_config() -> ApiResult:
    """读取非敏感 AI 配置；不返回 Key。"""
    return ApiResult.ok(load_config())


@app.post("/api/ai-config", response_model=ApiResult)
def post_ai_config(payload: dict) -> ApiResult:
    """保存 AI 配置：三项非敏感写入 JSON，api_key 非空时写入 keyring。"""
    provider = (payload.get("provider") or "").strip()
    base_url = (payload.get("base_url") or "").strip()
    model = (payload.get("model") or "").strip()
    api_key_raw = payload.get("api_key")
    api_key = api_key_raw.strip() if isinstance(api_key_raw, str) else ""

    save_config(provider, base_url, model)
    key_saved = False
    if api_key:
        try:
            set_api_key(api_key)
            key_saved = True
        except KeyringError as exc:
            # 非敏感三项已落盘，但 Key 没保存成功——告诉用户重试，不要返回 500
            return ApiResult.fail(
                f"非敏感配置已保存，但 API Key 写入凭据管理器失败：{exc}。"
                "请重试，或检查系统凭据管理器是否可用。",
                code=1,
            )

    return ApiResult.ok({"saved": True, "key_saved": key_saved}, message="配置已保存")


@app.post("/api/ai-test", response_model=ApiResult)
def post_ai_test() -> ApiResult:
    """测试 AI 连接：v1.0 不真发请求，仅校验配置齐全性。"""
    if not is_configured():
        return ApiResult.fail("请先在设置页填写并保存完整配置")
    return ApiResult.ok(None, message="配置已保存，测试功能v1.1实现")


# 静态资源（CSS / JS）放在 /static 下
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

