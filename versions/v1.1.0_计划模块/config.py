# -*- coding: utf-8 -*-
"""
全局配置模块。

只统一定义项目路径、版本号和运行时常量，业务代码不要写死绝对路径。
所有运行时数据都放在项目根目录的 data/ 下。
"""

from pathlib import Path

# 项目根目录（本文件所在目录）
BASE_DIR = Path(__file__).resolve().parent

# 运行时数据目录
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
EXPORT_DIR = DATA_DIR / "exports"

# SQLite 数据库文件和 SQLAlchemy 连接地址
DB_PATH = DATA_DIR / "database.db"
DATABASE_URL = f"sqlite:///{DB_PATH.as_posix()}"

# 应用信息
APP_NAME = "个人工作台"
APP_VERSION = "1.1.0"


def ensure_dirs() -> None:
    """确保程序运行需要的目录存在；已存在时不报错、不清空。"""
    for directory in (DATA_DIR, UPLOAD_DIR, EXPORT_DIR):
        directory.mkdir(parents=True, exist_ok=True)