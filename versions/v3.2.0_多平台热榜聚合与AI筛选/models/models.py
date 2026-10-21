# -*- coding: utf-8 -*-
"""
ORM 模型：notes、rss_sources、pending_items、fetch_logs、async_tasks、tasks、schedules。
JSON 字段统一用 Text 存 JSON 字符串，不依赖 SQLite 原生 JSON 类型。
"""

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class Note(Base):
    """笔记表。"""

    __tablename__ = "notes"
    __table_args__ = (
        CheckConstraint(
            "quality_score IS NULL OR (quality_score >= 1 AND quality_score <= 5)",
            name="ck_notes_quality_score",
        ),
        CheckConstraint(
            "note_type IN ('普通', '会议')",
            name="ck_notes_note_type",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(200), nullable=False, comment="笔记标题")
    content = Column(Text, nullable=False, default="", comment="改写后的笔记内容")
    original_url = Column(String(500), nullable=True, comment="原文链接")
    source = Column(String(50), nullable=False, default="手动输入", comment="来源")
    tags = Column(Text, nullable=False, default="[]", comment="标签 JSON 字符串")
    related_ids = Column(
        Text,
        nullable=False,
        default="[]",
        comment="向量相似度关联笔记 ID JSON 字符串",
    )
    task_ids = Column(
        Text,
        nullable=False,
        default="[]",
        comment="关联任务 ID JSON 字符串",
    )
    quality_score = Column(Float, nullable=True, comment="AI 改写质量评分")
    category = Column(String(50), nullable=False, default="默认", comment="分类")
    note_type = Column(String(10), nullable=False, default="普通", comment="笔记类型：普通/会议")
    meeting_time = Column(DateTime, nullable=True, comment="会议时间")
    meeting_attendees = Column(String(500), nullable=True, comment="参会人，逗号分隔")
    meeting_topic = Column(String(300), nullable=True, comment="会议主题")
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)


class RssSource(Base):
    """RSS 源表。"""

    __tablename__ = "rss_sources"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False, comment="源名称")
    url = Column(String(500), nullable=False, comment="RSS 地址")
    last_fetched = Column(DateTime, nullable=True, comment="最后抓取时间")
    focus_topics = Column(String(500), nullable=True, comment="关注主题，逗号分隔")
    auto_process = Column(
        Boolean, nullable=False, default=False, comment="相关内容是否直接改写存笔记"
    )
    ai_filter_enabled = Column(
        Boolean, nullable=False, default=True, comment="是否开启 AI 相关性筛选"
    )
    created_at = Column(DateTime, default=datetime.now, nullable=False)


class PendingItem(Base):
    """待处理队列表。"""

    __tablename__ = "pending_items"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'processing', 'done', 'skipped', 'filtered')",
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
    filtered_count = Column(Integer, nullable=False, default=0, comment="AI 筛掉条数")
    auto_saved_count = Column(Integer, nullable=False, default=0, comment="自动保存条数")
    created_at = Column(DateTime, default=datetime.now, nullable=False)


class AsyncTask(Base):
    """后台异步任务表。"""

    __tablename__ = "async_tasks"
    __table_args__ = (
        CheckConstraint(
            "task_type IN ('rewrite_url', 'rewrite_text', 'batch_process', "
            "'evaluate', 'regenerate', 'organize_meeting')",
            name="ck_async_tasks_type",
        ),
        CheckConstraint(
            "status IN ('pending', 'running', 'success', 'failed')",
            name="ck_async_tasks_status",
        ),
        CheckConstraint(
            "progress >= 0 AND progress <= 100",
            name="ck_async_tasks_progress",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_type = Column(String(30), nullable=False, comment="任务类型")
    status = Column(String(20), nullable=False, default="pending", comment="任务状态")
    progress = Column(Integer, nullable=False, default=0, comment="进度百分比")
    progress_message = Column(String(200), nullable=False, default="", comment="进度说明")
    params = Column(Text, nullable=False, default="{}", comment="任务参数 JSON")
    result = Column(Text, nullable=True, comment="成功结果 JSON")
    error = Column(Text, nullable=True, comment="错误信息")
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)


class Task(Base):
    """日常/工作任务表。"""

    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint("category IN ('日常', '工作')", name="ck_tasks_category"),
        CheckConstraint("priority IN ('高', '中', '低')", name="ck_tasks_priority"),
        CheckConstraint("completed IN (0, 1)", name="ck_tasks_completed"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(200), nullable=False, comment="任务标题")
    category = Column(String(20), nullable=False, default="日常", comment="任务分类")
    priority = Column(String(10), nullable=False, default="中", comment="任务优先级")
    due_date = Column(DateTime, nullable=True, comment="截止日期")
    completed = Column(Boolean, nullable=False, default=False, comment="是否完成")
    note_id = Column(Integer, nullable=True, comment="兼容旧版的第一个关联笔记 ID")
    note_ids = Column(
        Text,
        nullable=False,
        default="[]",
        comment="关联笔记 ID JSON 字符串",
    )
    sort_order = Column(Integer, nullable=False, default=0, comment="未完成任务排序值")
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    completed_at = Column(DateTime, nullable=True, comment="完成时间")


class Schedule(Base):
    """日程计划表。"""

    __tablename__ = "schedules"
    __table_args__ = (
        CheckConstraint(
            "schedule_type IN ('日常', '旅行', '工作', '其他')",
            name="ck_schedules_type",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(200), nullable=False, comment="日程标题")
    schedule_type = Column(String(20), nullable=False, default="日常", comment="日程类型")
    start_time = Column(DateTime, nullable=False, comment="开始时间")
    end_time = Column(DateTime, nullable=True, comment="结束时间")
    location = Column(String(200), nullable=True, comment="地点")
    description = Column(Text, nullable=True, comment="备注或行程详情")
    note_id = Column(Integer, nullable=True, comment="关联笔记 ID")
    color = Column(String(20), nullable=False, default="#5b8def", comment="日历颜色")
    created_at = Column(DateTime, default=datetime.now, nullable=False)


class BilibiliCreator(Base):
    """B 站 UP 主跟踪表。"""

    __tablename__ = "bilibili_creators"

    id = Column(Integer, primary_key=True, autoincrement=True)
    uid = Column(String(32), nullable=False, unique=True, index=True, comment="B站 UID")
    name = Column(String(200), nullable=False, default="", comment="UP主名称")
    face = Column(String(500), nullable=False, default="", comment="头像 URL")
    added_at = Column(DateTime, default=datetime.now, nullable=False, comment="添加时间")
    last_check_at = Column(DateTime, nullable=True, comment="最后检查时间")
    enabled = Column(Boolean, nullable=False, default=True, comment="是否启用")


class BilibiliVideo(Base):
    """B 站视频表。"""

    __tablename__ = "bilibili_videos"
    __table_args__ = (
        CheckConstraint(
            "ai_score IS NULL OR (ai_score >= 0 AND ai_score <= 100)",
            name="ck_bilibili_videos_ai_score",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    bvid = Column(String(32), nullable=False, unique=True, index=True, comment="BV号")
    uid = Column(String(32), nullable=False, index=True, comment="B站 UID")
    title = Column(String(500), nullable=False, default="", comment="视频标题")
    description = Column(Text, nullable=False, default="", comment="视频简介")
    publish_time = Column(DateTime, nullable=True, comment="发布时间")
    duration = Column(Integer, nullable=False, default=0, comment="时长秒数")
    play = Column(Integer, nullable=False, default=0, comment="播放量")
    like = Column(Integer, nullable=False, default=0, comment="点赞数")
    coin = Column(Integer, nullable=False, default=0, comment="投币数")
    favorite = Column(Integer, nullable=False, default=0, comment="收藏数")
    comment = Column(Integer, nullable=False, default=0, comment="评论数")
    share = Column(Integer, nullable=False, default=0, comment="转发数")
    cover = Column(String(500), nullable=False, default="", comment="封面 URL")
    cid = Column(Integer, nullable=True, comment="视频 CID")
    is_valid = Column(Boolean, nullable=False, default=True, comment="视频是否有效")
    invalid_reason = Column(String(200), nullable=False, default="", comment="失效原因")
    last_validated_at = Column(DateTime, nullable=True, comment="最后有效性检查时间")
    ai_score = Column(Integer, nullable=True, comment="AI 值得看分数 0-100")
    ai_decision = Column(String(20), nullable=False, default="一般", comment="值得看/一般/不值得看")
    ai_reason = Column(String(300), nullable=False, default="", comment="AI 推荐理由")
    category = Column(String(50), nullable=False, default="未分类", comment="AI 内容分类")
    user_feedback = Column(String(20), nullable=False, default="待确认", comment="待确认/值得看/不值得看")
    popularity_tag = Column(String(20), nullable=False, default="正常", comment="爆款/有潜力/正常/低迷")
    popularity_reason = Column(String(200), nullable=False, default="", comment="爆款判断说明")
    note_exported = Column(Boolean, nullable=False, default=False, comment="是否已导出 Obsidian 笔记")
    exported_path = Column(String(500), nullable=False, default="", comment="导出文件路径")
    created_at = Column(DateTime, default=datetime.now, nullable=False, comment="入库时间")


class HotTrend(Base):
    """多平台热点聚合表。"""

    __tablename__ = "hot_trends"
    __table_args__ = (
        UniqueConstraint("platform", "title", "fetched_date", name="uq_hot_trends_platform_title_date"),
        CheckConstraint("ai_score IS NULL OR (ai_score >= 0 AND ai_score <= 100)", name="ck_hot_trends_ai_score"),
        CheckConstraint("user_feedback IN ('待确认', '值得看', '不值得看')", name="ck_hot_trends_feedback"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    platform = Column(String(20), nullable=False, index=True, comment="bilibili/zhihu/weibo")
    title = Column(String(500), nullable=False, comment="热点标题")
    url = Column(String(1000), nullable=False, default="", comment="原文或搜索链接")
    heat = Column(Integer, nullable=False, default=0, comment="热度值")
    ai_score = Column(Integer, nullable=True, comment="AI 推荐分 0-100")
    ai_reason = Column(String(300), nullable=False, default="", comment="AI 推荐理由")
    ai_category = Column(String(50), nullable=False, default="未分类", comment="AI 内容分类")
    user_feedback = Column(String(20), nullable=False, default="待确认", comment="用户反馈")
    note_id = Column(Integer, nullable=True, comment="生成的笔记 ID")
    created_at = Column(DateTime, default=datetime.now, nullable=False, comment="抓取时间")
    fetched_date = Column(String(10), nullable=False, index=True, comment="抓取日期 YYYY-MM-DD")
