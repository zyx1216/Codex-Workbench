# -*- coding: utf-8 -*-
"""
数据库连接：engine、SessionLocal、init_db()、get_db() FastAPI 依赖。
"""

import json

import config
from models.models import Base
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import sessionmaker

engine = create_engine(
    config.DATABASE_URL,
    echo=False,
    connect_args={"check_same_thread": False},
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    """开启 SQLite 外键约束。"""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _migrate_v24_async_tasks() -> None:
    """把 v2.4 的 tasks 表迁移为 async_tasks；过程幂等。"""
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    if "tasks" not in table_names:
        return

    task_columns = {column["name"] for column in inspector.get_columns("tasks")}
    is_v24_async_table = {"task_type", "status", "progress"}.issubset(task_columns)

    if "async_tasks" in table_names:
        # 新版 tasks 与 async_tasks 并存属于正常状态；两个 v2.4 结构同时存在才报错。
        if is_v24_async_table:
            raise RuntimeError("检测到 tasks 和 async_tasks 都是后台任务表，无法自动迁移")
        return

    if not is_v24_async_table:
        raise RuntimeError("检测到无法识别的旧 tasks 表，已停止启动以避免误覆盖数据")

    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE tasks RENAME TO async_tasks"))


def _ensure_notes_columns() -> None:
    """给旧版数据库补笔记关联、质量评分和会议字段；过程幂等。"""
    inspector = inspect(engine)
    existing = {column["name"] for column in inspector.get_columns("notes")}

    with engine.begin() as connection:
        if "related_ids" not in existing:
            # 旧字段补齐：JSON 数组统一存 Text
            connection.execute(text(
                "ALTER TABLE notes ADD COLUMN related_ids TEXT "
                "NOT NULL DEFAULT '[]'"
            ))
        if "quality_score" not in existing:
            # 内联 CHECK，保证旧库补列后也限制评分范围
            connection.execute(text(
                "ALTER TABLE notes ADD COLUMN quality_score FLOAT "
                "CHECK (quality_score IS NULL OR "
                "(quality_score >= 1 AND quality_score <= 5))"
            ))
        # v2.7 会议字段：四列分开检查，旧库逐列补齐
        if "note_type" not in existing:
            connection.execute(text(
                "ALTER TABLE notes ADD COLUMN note_type TEXT "
                "NOT NULL DEFAULT '普通' "
                "CHECK (note_type IN ('普通', '会议'))"
            ))
        if "meeting_time" not in existing:
            connection.execute(text(
                "ALTER TABLE notes ADD COLUMN meeting_time TIMESTAMP"
            ))
        if "meeting_attendees" not in existing:
            connection.execute(text(
                "ALTER TABLE notes ADD COLUMN meeting_attendees VARCHAR(500)"
            ))
        if "meeting_topic" not in existing:
            connection.execute(text(
                "ALTER TABLE notes ADD COLUMN meeting_topic VARCHAR(300)"
            ))
        # v2.12 任务关联字段
        if "task_ids" not in existing:
            connection.execute(text(
                "ALTER TABLE notes ADD COLUMN task_ids TEXT "
                "NOT NULL DEFAULT '[]'"
            ))


def _ensure_task_columns() -> None:
    """给旧版数据库补任务关联和排序字段；过程幂等。"""
    inspector = inspect(engine)
    existing = {column["name"] for column in inspector.get_columns("tasks")}

    with engine.begin() as connection:
        if "note_ids" not in existing:
            connection.execute(text(
                "ALTER TABLE tasks ADD COLUMN note_ids TEXT "
                "NOT NULL DEFAULT '[]'"
            ))
        if "sort_order" not in existing:
            connection.execute(text(
                "ALTER TABLE tasks ADD COLUMN sort_order INTEGER "
                "NOT NULL DEFAULT 0"
            ))


def _parse_migration_ids(raw: object) -> list[int]:
    """迁移时解析 JSON ID，损坏数据按空数组处理。"""
    try:
        values = json.loads(raw) if raw else []
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(values, list):
        return []
    result: list[int] = []
    for value in values:
        try:
            item_id = int(value)
        except (TypeError, ValueError):
            continue
        if item_id > 0 and item_id not in result:
            result.append(item_id)
    return result


def _backfill_task_note_links() -> None:
    """把旧 note_id 补进双向 JSON 字段；重复执行不产生重复关联。"""
    with engine.begin() as connection:
        note_rows = connection.execute(text(
            "SELECT id, task_ids FROM notes"
        )).mappings().all()
        note_ids_set = {int(row["id"]) for row in note_rows}
        note_task_ids: dict[int, list[int]] = {
            int(row["id"]): _parse_migration_ids(row["task_ids"])
            for row in note_rows
        }

        task_rows = connection.execute(text(
            "SELECT id, note_id, note_ids FROM tasks"
        )).mappings().all()
        for row in task_rows:
            task_id = int(row["id"])
            note_ids = [
                note_id for note_id in _parse_migration_ids(row["note_ids"])
                if note_id in note_ids_set
            ]
            legacy_note_id = int(row["note_id"]) if row["note_id"] else None
            if not note_ids and legacy_note_id in note_ids_set:
                note_ids = [legacy_note_id]
            note_ids = note_ids[:3]
            for note_id in note_ids:
                links = note_task_ids.setdefault(note_id, [])
                if task_id not in links:
                    links.append(task_id)
            connection.execute(
                text(
                    "UPDATE tasks SET note_ids = :note_ids, note_id = :note_id "
                    "WHERE id = :task_id"
                ),
                {
                    "note_ids": json.dumps(note_ids, ensure_ascii=False),
                    "note_id": note_ids[0] if note_ids else None,
                    "task_id": task_id,
                },
            )

        for note_id, task_ids in note_task_ids.items():
            connection.execute(
                text("UPDATE notes SET task_ids = :task_ids WHERE id = :note_id"),
                {
                    "task_ids": json.dumps(task_ids[:3], ensure_ascii=False),
                    "note_id": note_id,
                },
            )



# async_tasks 新表结构（SQLite 无法直接改 CHECK，只能整表重建）
_ASYNC_TASKS_NEW_DDL = """
CREATE TABLE async_tasks__new (
    id INTEGER NOT NULL,
    task_type VARCHAR(30) NOT NULL,
    status VARCHAR(20) NOT NULL,
    progress INTEGER NOT NULL,
    progress_message VARCHAR(200) NOT NULL,
    params TEXT NOT NULL,
    result TEXT,
    error TEXT,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT ck_async_tasks_type CHECK (task_type IN (
        'rewrite_url', 'rewrite_text', 'batch_process',
        'evaluate', 'regenerate', 'organize_meeting'
    )),
    CONSTRAINT ck_async_tasks_status CHECK (status IN (
        'pending', 'running', 'success', 'failed'
    )),
    CONSTRAINT ck_async_tasks_progress CHECK (progress >= 0 AND progress <= 100)
)
"""


def _rebuild_async_tasks_if_needed() -> None:
    """旧 async_tasks 的类型约束缺少 organize_meeting 时整表重建；过程幂等。"""
    inspector = inspect(engine)
    if "async_tasks" not in set(inspector.get_table_names()):
        return

    with engine.connect() as connection:
        ddl = connection.execute(text(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='async_tasks'"
        )).scalar() or ""
    if "organize_meeting" in ddl:
        return

    with engine.begin() as connection:
        connection.execute(text(_ASYNC_TASKS_NEW_DDL))
        connection.execute(text(
            "INSERT INTO async_tasks__new ("
            "id, task_type, status, progress, progress_message, "
            "params, result, error, created_at, updated_at) "
            "SELECT id, task_type, status, progress, progress_message, "
            "params, result, error, created_at, updated_at FROM async_tasks"
        ))
        connection.execute(text("DROP TABLE async_tasks"))
        connection.execute(text(
            "ALTER TABLE async_tasks__new RENAME TO async_tasks"
        ))


# pending_items 新表结构：状态增加 filtered（SQLite 不能直接改 CHECK，只能整表重建）
_PENDING_ITEMS_NEW_DDL = """
CREATE TABLE pending_items__new (
    id INTEGER NOT NULL,
    url VARCHAR(500) NOT NULL,
    title VARCHAR(500),
    source VARCHAR(50),
    status VARCHAR(20) NOT NULL,
    created_at DATETIME NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT ck_pending_items_status CHECK (status IN (
        'pending', 'processing', 'done', 'skipped', 'filtered'
    ))
)
"""


def _ensure_rss_columns() -> None:
    """给旧版数据库补 rss_sources、fetch_logs 的 v2.8 字段；过程幂等。"""
    inspector = inspect(engine)
    table_columns = {
        table: {column["name"] for column in inspector.get_columns(table)}
        for table in ("rss_sources", "fetch_logs")
        if table in set(inspector.get_table_names())
    }

    rss_existing = table_columns.get("rss_sources", set())
    log_existing = table_columns.get("fetch_logs", set())

    with engine.begin() as connection:
        # RSS 源三个字段：布尔列给固定默认值
        if "focus_topics" not in rss_existing:
            connection.execute(text(
                "ALTER TABLE rss_sources ADD COLUMN focus_topics VARCHAR(500)"
            ))
        if "auto_process" not in rss_existing:
            connection.execute(text(
                "ALTER TABLE rss_sources ADD COLUMN auto_process BOOLEAN "
                "NOT NULL DEFAULT 0"
            ))
        if "ai_filter_enabled" not in rss_existing:
            connection.execute(text(
                "ALTER TABLE rss_sources ADD COLUMN ai_filter_enabled BOOLEAN "
                "NOT NULL DEFAULT 1"
            ))
        # 抓取日志两个计数
        if "filtered_count" not in log_existing:
            connection.execute(text(
                "ALTER TABLE fetch_logs ADD COLUMN filtered_count INTEGER "
                "NOT NULL DEFAULT 0"
            ))
        if "auto_saved_count" not in log_existing:
            connection.execute(text(
                "ALTER TABLE fetch_logs ADD COLUMN auto_saved_count INTEGER "
                "NOT NULL DEFAULT 0"
            ))


def _rebuild_pending_items_if_needed() -> None:
    """旧 pending_items 的状态约束缺少 filtered 时整表重建；过程幂等。"""
    inspector = inspect(engine)
    if "pending_items" not in set(inspector.get_table_names()):
        return

    with engine.connect() as connection:
        ddl = connection.execute(text(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='pending_items'"
        )).scalar() or ""
    if "filtered" in ddl:
        return

    with engine.begin() as connection:
        connection.execute(text(_PENDING_ITEMS_NEW_DDL))
        connection.execute(text(
            "INSERT INTO pending_items__new ("
            "id, url, title, source, status, created_at) "
            "SELECT id, url, title, source, status, created_at FROM pending_items"
        ))
        connection.execute(text("DROP TABLE pending_items"))
        connection.execute(text(
            "ALTER TABLE pending_items__new RENAME TO pending_items"
        ))


def init_db() -> None:
    """创建数据目录和所有数据表（幂等）。"""
    config.ensure_dirs()
    _migrate_v24_async_tasks()
    Base.metadata.create_all(bind=engine)
    _ensure_notes_columns()
    _ensure_task_columns()
    _backfill_task_note_links()
    _rebuild_async_tasks_if_needed()
    _ensure_rss_columns()
    _rebuild_pending_items_if_needed()


def get_db():
    """FastAPI 依赖：每个请求一个 session，请求结束自动关闭。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
