# -*- coding: utf-8 -*-
"""
定时抓取服务。

- 用 APScheduler BackgroundScheduler 在后台线程执行每日任务
- 手动立即抓取和定时抓取共用同一套全量抓取逻辑
- 抓取日志写入 fetch_logs 表
"""

from __future__ import annotations

import json
import logging
import re
import threading
from datetime import datetime
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

import config
from models.models import FetchLog
from services import rss_service
from services.db import SessionLocal

logger = logging.getLogger(__name__)

# 每日任务固定 ID，更新时间时直接替换
JOB_ID = "daily_rss_fetch"
TIME_PATTERN = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")

# 防止手动任务和定时任务同时跑两批全量抓取
_running_lock = threading.Lock()
_scheduler = BackgroundScheduler(timezone="Asia/Shanghai")

_UNSET = object()


class SchedulerError(Exception):
    """定时配置错误。"""


class SchedulerBusy(SchedulerError):
    """已有全量抓取任务正在执行。"""


def default_config() -> dict[str, Any]:
    """默认调度配置。"""
    return {
        "enabled": False,
        "fetch_time": "08:00",
        "last_run": None,
    }


def validate_time(fetch_time: str) -> tuple[int, int]:
    """校验 HH:MM，返回小时和分钟。"""
    match = TIME_PATTERN.fullmatch((fetch_time or "").strip())
    if not match:
        raise SchedulerError("抓取时间格式必须是 HH:MM（24小时制）")
    return int(match.group(1)), int(match.group(2))


def load_config() -> dict[str, Any]:
    """读取调度配置；文件缺失或损坏时回退默认值。"""
    config.ensure_dirs()
    data: dict[str, Any] = {}
    try:
        data = json.loads(config.SCHEDULER_CONFIG_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        data = {}

    fetch_time = str(data.get("fetch_time", "08:00"))
    if not TIME_PATTERN.fullmatch(fetch_time):
        fetch_time = "08:00"

    last_run = None
    last_raw = data.get("last_run")
    if isinstance(last_raw, str):
        try:
            last_run = datetime.fromisoformat(last_raw)
        except ValueError:
            last_run = None

    return {
        "enabled": bool(data.get("enabled", False)),
        "fetch_time": fetch_time,
        "last_run": last_run,
    }


def save_config(
    enabled: bool,
    fetch_time: str,
    last_run: datetime | None | object = _UNSET,
) -> None:
    """保存调度配置；last_run 不传时保留原值。"""
    validate_time(fetch_time)
    old = load_config()
    if last_run is _UNSET:
        last_value = old["last_run"].isoformat() if old["last_run"] else None
    elif isinstance(last_run, datetime):
        last_value = last_run.isoformat()
    else:
        last_value = None

    payload = {
        "enabled": bool(enabled),
        "fetch_time": fetch_time,
        "last_run": last_value,
    }
    config.ensure_dirs()
    config.SCHEDULER_CONFIG_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def add_log(
    source_name: str,
    status: str,
    message: str,
    new_count: int = 0,
) -> None:
    """写入一条抓取日志；日志失败只记 logging，不影响抓取主流程。"""
    if status not in ("success", "failed", "running"):
        status = "failed"
    try:
        with SessionLocal() as session:
            session.add(FetchLog(
                source_name=str(source_name or "全部")[:200],
                status=status,
                message=str(message or "")[:500],
                new_count=max(int(new_count or 0), 0),
                created_at=datetime.now(),
            ))
            session.commit()
    except Exception:
        logger.exception("写入抓取日志失败")


def serialize_log(log: FetchLog) -> dict[str, Any]:
    """抓取日志转前端字典。"""
    return {
        "id": log.id,
        "source_name": log.source_name,
        "status": log.status,
        "message": log.message,
        "new_count": log.new_count,
        "created_at": log.created_at.isoformat() if log.created_at else None,
    }


def get_logs(limit: int = 50) -> list[dict[str, Any]]:
    """读取最近抓取日志，按时间倒序。"""
    limit = max(1, min(int(limit), 200))
    with SessionLocal() as session:
        logs = session.scalars(
            select(FetchLog)
            .order_by(FetchLog.created_at.desc(), FetchLog.id.desc())
            .limit(limit)
        ).all()
        return [serialize_log(log) for log in logs]


def _apply_job() -> None:
    """按当前配置新增、替换或移除每日任务。"""
    cfg = load_config()
    if not cfg["enabled"]:
        if _scheduler.get_job(JOB_ID):
            _scheduler.remove_job(JOB_ID)
        return

    hour, minute = validate_time(cfg["fetch_time"])
    _scheduler.add_job(
        _scheduled_job,
        trigger=CronTrigger(hour=hour, minute=minute, timezone=_scheduler.timezone),
        id=JOB_ID,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )


def start_scheduler() -> None:
    """启动后台调度器并按配置加载任务；重复调用不报错。"""
    config.ensure_dirs()
    if not _scheduler.running:
        _scheduler.start()
    _apply_job()


def stop_scheduler() -> None:
    """停止后台调度器。"""
    if _scheduler.running:
        _scheduler.shutdown(wait=False)


def update_schedule(enabled: bool, fetch_time: str) -> dict[str, Any]:
    """保存设置并立即更新每日任务。"""
    validate_time(fetch_time)
    save_config(bool(enabled), fetch_time)
    _apply_job()
    return load_config()


def _scheduled_job() -> None:
    """APScheduler 调用入口；异常不能抛到后台线程外。"""
    try:
        run_now()
    except SchedulerBusy as exc:
        logger.info(str(exc))
    except Exception:
        logger.exception("定时 RSS 抓取失败")


def run_now() -> dict[str, Any]:
    """立即执行一次全部 RSS 抓取。"""
    if not _running_lock.acquire(blocking=False):
        raise SchedulerBusy("已有抓取任务正在执行")

    cfg = load_config()
    now = datetime.now()
    try:
        save_config(cfg["enabled"], cfg["fetch_time"], last_run=now)
        add_log("全部", "running", "开始执行全部 RSS 抓取", 0)

        try:
            with SessionLocal() as session:
                results = rss_service.fetch_all_sources(session, log_summary=False)
        except Exception as exc:
            add_log("全部", "failed", f"全部抓取失败：{exc}", 0)
            raise

        total_added = sum(int(item.get("added", 0)) for item in results)
        failed_count = sum(1 for item in results if item.get("error"))
        status = "failed" if failed_count else "success"
        if failed_count:
            message = f"全部抓取完成，新增 {total_added} 条，{failed_count} 个源失败"
        else:
            message = f"全部抓取完成，新增 {total_added} 条"
        add_log("全部", status, message, total_added)
        return {
            "results": results,
            "total_added": total_added,
        }
    finally:
        _running_lock.release()
