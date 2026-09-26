# -*- coding: utf-8 -*-
"""
v1.0 ORM 模型：notes、rss_sources、pending_items 三张表。
JSON 字段统一用 Text 存 JSON 字符串，不依赖 SQLite 原生 JSON 类型。
"""

from datetime import datetime

from sqlalchemy import CheckConstraint, Column, DateTime, Integer, String, Text
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class Note(Base):
    """笔记表：v1.0 只建表，不实现 CRUD。"""

    __tablename__ = "notes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(200), nullable=False, comment="笔记标题")
    content = Column(Text, nullable=False, default="", comment="改写后的笔记内容")
    original_url = Column(String(500), nullable=True, comment="原文链接")
    source = Column(String(50), nullable=False, default="手动输入", comment="来源")
    tags = Column(Text, nullable=False, default="[]", comment="标签 JSON 字符串")
    category = Column(String(50), nullable=False, default="默认", comment="分类")
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)


class RssSource(Base):
    """RSS 源表。"""

    __tablename__ = "rss_sources"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False, comment="源名称")
    url = Column(String(500), nullable=False, comment="RSS 地址")
    last_fetched = Column(DateTime, nullable=True, comment="最后抓取时间")
    created_at = Column(DateTime, default=datetime.now, nullable=False)


class PendingItem(Base):
    """待处理队列表。"""

    __tablename__ = "pending_items"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'processing', 'done', 'skipped')",
            name="ck_pending_items_status",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    url = Column(String(500), nullable=False, comment="内容链接")
    title = Column(String(500), nullable=True, comment="标题")
    source = Column(String(50), nullable=True, comment="来源")
    status = Column(String(20), nullable=False, default="pending", comment="状态")
    created_at = Column(DateTime, default=datetime.now, nullable=False)

class FetchLog(Base):
    """RSS 抓取日志表；source_name 为“全部”时表示整批抓取。"""

    __tablename__ = "fetch_logs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('success', 'failed', 'running')",
            name="ck_fetch_logs_status",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_name = Column(String(200), nullable=False, comment="源名称")
    status = Column(String(20), nullable=False, comment="执行状态")
    message = Column(String(500), nullable=False, default="", comment="结果信息")
    new_count = Column(Integer, nullable=False, default=0, comment="新增待处理条数")
    created_at = Column(DateTime, default=datetime.now, nullable=False)
