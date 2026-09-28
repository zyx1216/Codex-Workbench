# 项目约定

## 版本留存与对比（必须遵守）

目标：**每次改动后，"上一版"都必须完整保留，既能在 Git 中 diff/回退，也能导出成可直接双击打开的文件。** 采用"Git 提交 + 历史版本文件"双留存：不使用零散的 `.bak`/`.old` 临时副本，历史版本统一归档到 `历史版本/` 文件夹并规范命名。

1. **改动前先存档**：动手修改任何已纳入 Git 的文件前，先运行 `git status`。
   - 若工作区干净（上一版已提交），直接开始修改；
   - 若有未提交的改动，先提交一次（信息如 `chore: 改动前存档`），确保"改动前状态"是一个可回溯的提交快照。
2. **改动后再提交**：每次功能或修改完成并通过验证（如 `node --check`、构建/测试）后，及时 `git add` + `git commit`，提交信息用简体中文，写清"改了什么、为什么"。
3. **一个任务一个提交点**：保证相邻两次提交即为「上一版 → 新版」，可用 `git diff <旧> <新>` 逐行对比，用 `git show <hash>:<文件>` 查看任意旧版全文。
4. **导出可打开的版本文件**：每完成一个步骤并提交后，把当前 `index.html` 复制一份到 `历史版本/`，命名为 `工作台（第X.Y版）.html`（X=阶段、Y=步骤，如步骤 1-2 → `工作台（第1.2版）.html`；初始版本为 `工作台（初始）.html`），并纳入 Git。双击即可在浏览器打开、并排对比，无需命令行。
5. **维护 PDF 版本说明**：`历史版本/工作台版本说明.pdf` 记录每个版本的创建时间（精确到分钟，取自该版本提交时间）与改动内容（初始版本记录最初的功能）。可编辑源为同目录 `版本说明.html`；每次发布新版本就在源中追加/更新对应行，再用浏览器无头打印重新导出并**覆盖同名 PDF**（始终只维护这一个 PDF，不新增多个）。
6. **提交后主动汇报**：给出本次提交哈希与简短改动清单；如用户需要，提供 `git diff --stat HEAD~1` 或完整 diff，并提示对应的历史版本文件。
7. 提交仅在本地进行；**关联远程、推送等对外操作必须先征得用户同意。**

> 说明：Git 只在 `commit` 时留存快照，两次提交之间的中间改动不可回溯。因此"先存档、再修改、后提交"是保证旧版不丢的关键。


---

## v2 项目（个人工作台，FastAPI）

> 自 v1.0 起，仓库根目录并存两个独立项目。旧 Streamlit 项目（个人工作台 v1.5.0）原封不动，新 FastAPI 项目放在 `v2/` 子目录。

### 路径与依赖

- 入口：`v2/app.py`，启动 `python -m uvicorn v2.app:app --reload --port 8000`
- 数据库：`data/v2_database.db`，与旧 `data/database.db` 物理隔离
- 依赖装在 `workbench` conda 环境：`fastapi`、`uvicorn[standard]`、`pydantic`、`httpx`、`beautifulsoup4`、`feedparser`、`python-multipart`、`sqlalchemy`、`openai`、`keyring`
- `v2/requirements.txt` 列出全部依赖

### 配置与密钥

- API Key：keyring，服务名 `knowledge-digest`、用户名 `ai_api_key`
- 非敏感 AI 配置：`data/ai_config.json`（不入库）

### v2 路线图（追加在原工作台路线图之后）

- [x] v1.0.0 个人工作台骨架（FastAPI + 原生 HTML/JS，3 张表，4 占位 API）
- [ ] v1.1.0 笔记 CRUD（按链接/RSS/文件输入）
- [ ] v1.2.0 AI 改写与首页真实统计
- [ ] v1.3.0 RSS 抓取与定时调度
- [ ] v1.4.0 数据导入导出

### v2 死规矩

1. v1.0 只搭骨架，不实现任何业务（AI 改写、RSS 抓取、文件解析、笔记 CRUD 等都归后续版本）
2. 前端用原生 HTML/CSS/JS，不引入任何前端框架
3. 所有 API 返回统一信封 `{code, message, data}`，用 `v2/schemas.py` 的 `ApiResult.ok/fail` 构造
4. 数据库用 SQLAlchemy ORM，不写裸 SQL
5. JSON 字段统一用 `Text` 存 JSON 字符串
6. 旧的 Streamlit 项目代码不删不改，`requirements.txt` 旧版本保留
7. keyring 写入失败必须优雅降级（返回 `code=1` 业务错），不允许返回 500
8. 注释和提交信息用中文
