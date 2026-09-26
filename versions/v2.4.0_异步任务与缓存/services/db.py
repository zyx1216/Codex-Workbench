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
    Base.metadata.create_all(bind=engine)
    _ensure_notes_columns()


def get_db():
    """FastAPI 依赖：每个请求一个 session，请求结束自动关闭。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
