# -*- coding: utf-8 -*-
"""
数据模型定义模块。

集中定义个人工作台的 7 张 SQLite 表：
计划、子任务、笔记、收藏、知识库文件夹、知识库文档、回收站。

JSON 类字段统一用 Text 存 JSON 字符串，避免依赖不同 SQLite 版本的 JSON 类型。
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import declarative_base, relationship

# 所有 ORM 模型的基类
Base = declarative_base()


class Plan(Base):
    """计划表：一条计划可以包含多个子任务。"""

    __tablename__ = "plans"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'done')", name="ck_plans_status"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(200), nullable=False, comment="计划标题")
    description = Column(Text, nullable=True, comment="计划描述")
    date = Column(Date, nullable=True, index=True, comment="计划日期；空表示暂未排期")
    start_time = Column(String(5), nullable=True, comment="开始时间，格式 HH:MM")
    end_time = Column(String(5), nullable=True, comment="结束时间，格式 HH:MM")
    category = Column(String(50), nullable=False, default="默认", comment="分类")
    status = Column(String(20), nullable=False, default="pending", comment="pending 未完成 / done 已完成")
    created_at = Column(DateTime, default=datetime.now, nullable=False, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False, comment="更新时间")

    subtasks = relationship(
        "Subtask",
        back_populates="plan",
        cascade="all, delete-orphan",
        order_by="Subtask.order",
    )


class Subtask(Base):
    """子任务表：从属于计划，不单独设置开始和结束时间。"""

    __tablename__ = "subtasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    plan_id = Column(Integer, ForeignKey("plans.id", ondelete="CASCADE"), nullable=False, index=True)
    text = Column(Text, nullable=False, comment="子任务内容")
    done = Column(Boolean, nullable=False, default=False, comment="是否完成")
    order = Column(Integer, nullable=False, default=0, comment="同一计划内的排序")
    note_ids = Column(Text, nullable=False, default="[]", comment="关联笔记 ID，JSON 数组字符串")
    link_ids = Column(Text, nullable=False, default="[]", comment="关联收藏 ID，JSON 数组字符串")
    doc_ids = Column(Text, nullable=False, default="[]", comment="关联知识库文档 ID，JSON 数组字符串")
    created_at = Column(DateTime, default=datetime.now, nullable=False, comment="创建时间")

    plan = relationship("Plan", back_populates="subtasks")


class Note(Base):
    """笔记表：同时保存间隔复习所需的状态。"""

    __tablename__ = "notes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(200), nullable=False, comment="笔记标题")
    content = Column(Text, nullable=False, default="", comment="笔记正文，支持 Markdown")
    tags = Column(Text, nullable=False, default="[]", comment="标签，JSON 数组字符串")
    category = Column(String(50), nullable=False, default="默认", comment="分类")
    spaced = Column(Boolean, nullable=False, default=False, comment="是否开启间隔复习")
    spaced_level = Column(Integer, nullable=False, default=0, comment="复习阶段：0 未开始，1-5 对应间隔")
    spaced_next = Column(Date, nullable=True, comment="下次复习日期；已掌握或未开启时为空")
    created_at = Column(DateTime, default=datetime.now, nullable=False, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False, comment="更新时间")


class Link(Base):
    """收藏表：保存网页链接和简短备注。"""

    __tablename__ = "links"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False, comment="收藏名称")
    url = Column(Text, nullable=False, comment="链接地址")
    note = Column(Text, nullable=True, comment="备注")
    category = Column(String(50), nullable=False, default="默认", comment="分类")
    created_at = Column(DateTime, default=datetime.now, nullable=False, index=True, comment="创建时间")


class KbFolder(Base):
    """知识库文件夹表：parent_id 为空表示根文件夹。"""

    __tablename__ = "kb_folders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False, comment="文件夹名称")
    parent_id = Column(Integer, ForeignKey("kb_folders.id", ondelete="CASCADE"), nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False, comment="创建时间")

    parent = relationship("KbFolder", remote_side=[id], back_populates="children")
    children = relationship(
        "KbFolder",
        back_populates="parent",
        cascade="all, delete-orphan",
    )
    docs = relationship(
        "KbDoc",
        back_populates="folder",
        cascade="all, delete-orphan",
    )


class KbDoc(Base):
    """知识库文档表：正文按纯文本或 Markdown 存储。"""

    __tablename__ = "kb_docs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    folder_id = Column(Integer, ForeignKey("kb_folders.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(200), nullable=False, comment="文档名称")
    content = Column(Text, nullable=False, default="", comment="文档内容")
    created_at = Column(DateTime, default=datetime.now, nullable=False, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False, comment="更新时间")

    folder = relationship("KbFolder", back_populates="docs")


class TrashItem(Base):
    """回收站表：用 JSON 快照保存被删除对象，便于以后还原。"""

    __tablename__ = "trash_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    item_type = Column(String(30), nullable=False, index=True, comment="对象类型，如 plan/note/link/doc")
    item_id = Column(String(50), nullable=True, comment="原对象 ID")
    snapshot = Column(Text, nullable=False, comment="删除前对象快照，JSON 字符串")
    deleted_at = Column(DateTime, default=datetime.now, nullable=False, index=True, comment="删除时间")