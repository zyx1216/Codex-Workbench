# -*- coding: utf-8 -*-
"""
数据库初始化与连接模块。

提供：
- engine：SQLAlchemy 数据库引擎（SQLite 单文件）；
- SessionLocal：会话工厂；
- init_db()：创建所有数据表，并对旧库做轻量补列迁移。

单人单机工具不引入 Alembic。以后新增可空字段时，在 _PENDING_COLUMNS
里登记“表名 -> [(列名, SQLite 列定义)]”，启动时自动 ALTER TABLE 补列。
"""

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import sessionmaker

import config
from models.models import Base

# check_same_thread=False：Streamlit 脚本可能在不同线程访问同一个 SQLite 文件
engine = create_engine(
    config.DATABASE_URL,
    echo=False,
    connect_args={"check_same_thread": False},
)

# 业务代码统一用 with SessionLocal() as session 操作数据库
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    """每个 SQLite 连接都开启外键约束，让级联规则生效。"""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


# 首版所有表都由 create_all 直接创建；这里保留迁移清单结构，方便后续平滑加列
_PENDING_COLUMNS: dict[str, list[tuple[str, str]]] = {}


def _add_missing_columns() -> None:
    """对已存在的旧表补齐新增的可空列；新表由 create_all 建好。"""
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table_name, columns in _PENDING_COLUMNS.items():
            if table_name not in existing_tables:
                continue
            present = {col["name"] for col in inspector.get_columns(table_name)}
            for col_name, col_type in columns:
                if col_name not in present:
                    conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {col_name} {col_type}"))


def init_db() -> None:
    """创建数据目录和所有数据表，并执行轻量迁移（幂等，不清空数据）。"""
    config.ensure_dirs()
    Base.metadata.create_all(bind=engine)
    _add_missing_columns()