# -*- coding: utf-8 -*-
"""
v2 数据库初始化：SQLAlchemy 引擎、会话工厂、init_db。
"""

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from v2 import config
from v2.models import Base

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


def init_db() -> None:
    """创建数据目录和所有数据表（幂等）。"""
    config.ensure_dirs()
    Base.metadata.create_all(bind=engine)
