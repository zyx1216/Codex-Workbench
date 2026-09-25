# -*- coding: utf-8 -*-
"""
v2 项目配置：路径、版本、运行时常量。所有运行时数据在 data/ 下。
"""

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent  # 工作台根目录
V2_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
EXPORT_DIR = DATA_DIR / "exports"
DB_PATH = DATA_DIR / "v2_database.db"  # 与旧 Streamlit 项目的 database.db 隔离
DATABASE_URL = f"sqlite:///{DB_PATH.as_posix()}"
AI_CONFIG_PATH = DATA_DIR / "ai_config.json"

APP_NAME = "知识消化平台"
APP_VERSION = "1.0.0"


def ensure_dirs() -> None:
    """确保运行目录存在；已存在时不报错、不清空。"""
    for directory in (DATA_DIR, UPLOAD_DIR, EXPORT_DIR):
        directory.mkdir(parents=True, exist_ok=True)
