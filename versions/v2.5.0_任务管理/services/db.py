# -*- coding: utf-8 -*-
"""
数据库连接：engine、SessionLocal、init_db()、get_db() FastAPI 依赖。
"""

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
    """给旧版数据库补笔记关联和质量评分字段；过程幂等。"""
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


def init_db() -> None:
    """创建数据目录和所有数据表（幂等）。"""
    config.ensure_dirs()
    _migrate_v24_async_tasks()
    Base.metadata.create_all(bind=engine)
    _ensure_notes_columns()


def get_db():
    """FastAPI 依赖：每个请求一个 session，请求结束自动关闭。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
