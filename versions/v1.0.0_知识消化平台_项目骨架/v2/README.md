# 知识消化平台 v1.0

本地单机运行的「知识消化」工具：把外部链接、RSS、文件喂进来，让 AI 帮你消化成结构化笔记。

v1.0 只搭骨架（3 张表、4 个占位 API、3 个 tab 的前端）。

## 技术栈

- 后端：FastAPI + Uvicorn
- 数据库：SQLite + SQLAlchemy
- 前端：原生 HTML/CSS/JS（单页应用，无构建）
- AI 调用：openai 兼容接口（v1.0 仅配置存取，v1.1 接入）
- API Key：keyring 写入 Windows 凭据管理器（服务名 `knowledge-digest`，用户名 `ai_api_key`）

## 启动

激活环境后双击 `run.bat`，或在项目根目录执行：

```bash
python -m uvicorn v2.app:app --reload --port 8000
```

浏览器访问 http://localhost:8000

## 目录

| 路径 | 作用 |
|---|---|
| `v2/app.py` | FastAPI 入口和 4 个占位 API |
| `v2/config.py` | 路径和版本常量 |
| `v2/database.py` | SQLAlchemy 引擎和 init_db |
| `v2/models.py` | 三张 ORM 表（notes / rss_sources / pending_items） |
| `v2/ai_client.py` | AI 配置存取和 keyring 封装 |
| `v2/schemas.py` | 统一响应信封 `ApiResult[T]` |
| `v2/static/` | 单页应用前端（index.html / css / js） |
| `v2/run.bat` | 双击启动脚本 |
| `data/v2_database.db` | v2 项目数据库（与旧 Streamlit 项目的 `database.db` 隔离） |
| `data/ai_config.json` | 非敏感 AI 配置（不入库） |
