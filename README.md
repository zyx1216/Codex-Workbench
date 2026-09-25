# 工作台

本地单机项目集合。仓库内目前并存两个项目：

- **个人工作台（Streamlit v1.5.0）**：计划、笔记、收藏、知识库、日历。
- **知识消化平台（FastAPI v1.0.0，`v2/` 目录）**：基于 FastAPI + 原生 HTML/JS 的新版骨架，后续用于「链接/RSS/文件 → AI 改写笔记」。

## 项目一：个人工作台

技术栈：Streamlit + SQLAlchemy + SQLite。

```bash
pip install -r requirements.txt
streamlit run app.py
```

访问 http://localhost:8501 。

## 项目二：知识消化平台

详见 `v2/README.md`。

```bash
python -m uvicorn v2.app:app --reload --port 8000
```

或双击 `v2/run.bat`，访问 http://localhost:8000 。

## 约定

两份项目各自的约定分别在 `v2/` 内 README / AGENTS.md，以及旧 Streamlit 项目的 AGENTS.md 章节中维护。
