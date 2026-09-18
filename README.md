# 个人工作台

本地单机运行的个人效率工作台，使用 Streamlit + SQLAlchemy + SQLite 重写。
当前版本是 v1.0.0 项目骨架：7 个页面路由、数据库表和 AI 设置已就绪，业务功能后续逐步实现。

## 技术栈

- 界面：Streamlit
- 数据库：SQLite + SQLAlchemy ORM
- AI：OpenAI 兼容接口（豆包、火山方舟 Agent Plan 或自定义服务）
- API Key 存储：keyring + Windows 凭据管理器

## 快速开始

```powershell
# 安装依赖
pip install -r requirements.txt

# 启动
streamlit run app.py
```

浏览器访问：<http://localhost:8501>

如果使用 Conda，也可以先创建 Python 3.11 环境：

```powershell
conda create -n personal_workbench python=3.11 -y
conda run -n personal_workbench python -m pip install -r requirements.txt
conda run -n personal_workbench streamlit run app.py
```

## 当前页面

- 📊 今日：骨架占位
- 📅 日历：骨架占位
- 📝 计划：骨架占位
- 📔 笔记：骨架占位
- 🔗 收藏：骨架占位
- 📚 知识库：骨架占位
- ⚙️ 设置：已支持服务商、模型名、API Key 配置和连接测试

首次启动会自动创建 `data/database.db`，并建立计划、子任务、笔记、收藏、知识库、回收站相关数据表。

## 数据和密钥

- 运行时数据保存在项目目录的 `data/` 下，该目录已被 Git 忽略。
- API Key 只保存在 Windows 凭据管理器，服务名为 `personal-workbench`。
- `data/llm_config.json` 只保存服务商、API 地址和模型名，不保存 API Key。

## 目录说明

| 路径 | 作用 |
| --- | --- |
| `app.py` | Streamlit 主入口和 7 个页面路由 |
| `config.py` | 项目路径、版本号和目录初始化 |
| `models/models.py` | SQLAlchemy 数据模型 |
| `modules/` | 各页面模块，统一暴露 `show()` 函数 |
| `utils/db.py` | 数据库连接、建表和轻量迁移 |
| `utils/llm_client.py` | OpenAI 兼容 AI 调用封装 |
| `data/` | 本地运行时数据，不提交 Git |
| `versions/` | 里程碑代码快照 |