# -*- coding: utf-8 -*-
"""
全局配置：路径、版本、运行时常量。所有运行时数据在 data/ 下。
"""

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
STATIC_DIR = BASE_DIR / "static"
UPLOAD_DIR = DATA_DIR / "uploads"
EXPORT_DIR = DATA_DIR / "exports"
VECTOR_DB_DIR = DATA_DIR / "vectordb"
DB_PATH = DATA_DIR / "database.db"
DATABASE_URL = f"sqlite:///{DB_PATH.as_posix()}"
AI_CONFIG_PATH = DATA_DIR / "ai_config.json"
SCHEDULER_CONFIG_PATH = DATA_DIR / "scheduler_config.json"

APP_NAME = "知识消化平台"
APP_VERSION = "2.3.0"


def ensure_dirs() -> None:
    """确保运行目录存在；已存在时不报错、不清空。"""
    for directory in (DATA_DIR, UPLOAD_DIR, EXPORT_DIR, VECTOR_DB_DIR):
        directory.mkdir(parents=True, exist_ok=True)
