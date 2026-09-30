# -*- coding: utf-8 -*-
"""
数据备份与恢复（zipfile 标准库，不引入新依赖）。

- build_backup_bytes：一键导出备份 zip（不含 API Key）
- prepare_restore：上传 zip 后只读校验、暂存，返回清单供确认
- apply_restore：确认后先备份当前数据，再热重载并原子替换，失败不动现有数据
- auto_backup_on_startup：每天首次启动备份数据库，只留最近 7 天
备份范围：database.db、ai_config.json、scheduler_config.json、uploads/。
"""

from __future__ import annotations

import io
import logging
import os
import secrets
import shutil
import sqlite3
import stat
import tempfile
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import config

logger = logging.getLogger(__name__)

_OPTIONAL_FILES = ("ai_config.json", "scheduler_config.json")
_UPLOAD_DIR_NAME = "uploads"

# 恢复会话：token -> {zip_path, files, backup_time, created_at}
_restore_sessions: dict[str, dict[str, Any]] = {}
_SESSION_TTL = timedelta(minutes=10)


class BackupError(Exception):
    """备份/恢复业务错误，message 为中文提示。"""


# ============ 打包 ============
def _iter_zip_entries() -> list[tuple[Path, str]]:
    """收集要打包的文件与 zip 内相对路径；必含数据库，其余缺失则跳过。"""
    data_dir = config.DATA_DIR
    db_path = config.DB_PATH
    if not db_path.is_file():
        raise BackupError("未找到数据库文件，无法备份")

    entries = [(db_path, "database.db")]
    for name in _OPTIONAL_FILES:
        path = data_dir / name
        if path.is_file():
            entries.append((path, name))

    upload_dir = data_dir / _UPLOAD_DIR_NAME
    if upload_dir.is_dir():
        for path in sorted(upload_dir.rglob("*")):
            if path.is_file():
                rel = path.relative_to(data_dir).as_posix()
                entries.append((path, rel))
    return entries


def _write_zip(entries: list[tuple[Path, str]]) -> bytes:
    """把给定条目写成 zip 字节。"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, arcname in entries:
            zf.write(path, arcname)
    return buffer.getvalue()


def build_backup_bytes() -> bytes:
    """一键导出全部数据为 zip 字节。"""
    return _write_zip(_iter_zip_entries())


def backup_filename(now: datetime | None = None) -> str:
    """备份下载文件名：工作台备份_YYYYMMDD_HHMMSS.zip。"""
    now = now or datetime.now()
    return f"工作台备份_{now.strftime('%Y%m%d_%H%M%S')}.zip"


def save_pre_restore_backup() -> str:
    """恢复前把当前数据备份为 data/backup_before_restore_时间戳.zip，返回文件名。"""
    now = datetime.now()
    name = f"backup_before_restore_{now.strftime('%Y%m%d_%H%M%S')}.zip"
    target = config.DATA_DIR / name
    target.write_bytes(_write_zip(_iter_zip_entries()))
    return name


# ============ 恢复：预检 ============
def _is_safe_member(info: zipfile.ZipInfo) -> bool:
    """校验 zip 条目：不能路径穿越、不能是符号链接。"""
    arcname = info.filename
    if not arcname or arcname.startswith("/") or ":" in arcname:
        return False
    if ".." in Path(arcname).parts:
        return False

    mode = info.external_attr >> 16
    if mode and stat.S_ISLNK(mode):
        return False
    return True


def _check_database(db_bytes: bytes) -> None:
    """校验备份数据库为 SQLite 且完整性正常。"""
    if not db_bytes.startswith(b"SQLite format 3\x00"):
        raise BackupError("备份中的数据库格式不正确")

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        tmp.write(db_bytes)
        tmp_path = tmp.name

    try:
        conn = sqlite3.connect(tmp_path)
        try:
            row = conn.execute("PRAGMA integrity_check").fetchone()
        finally:
            conn.close()
        if not row or row[0] != "ok":
            raise BackupError("备份数据库完整性检查未通过")
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def prepare_restore(zip_bytes: bytes) -> dict[str, Any]:
    """校验上传 zip 并暂存，返回 token、文件清单与备份时间；不改动现有数据。"""
    if not zip_bytes:
        raise BackupError("备份文件内容为空")

    with io.BytesIO(zip_bytes) as source:
        if not zipfile.is_zipfile(source):
            raise BackupError("文件不是有效的 zip 备份包")
        source.seek(0)
        with zipfile.ZipFile(source) as zf:
            infos = [info for info in zf.infolist() if not info.is_dir()]
            if not all(_is_safe_member(info) for info in infos):
                raise BackupError("备份包含非法路径或链接，已拒绝")

            names = [info.filename for info in infos]
            if "database.db" not in names:
                raise BackupError("备份中缺少 database.db，无法恢复")

            _check_database(zf.read("database.db"))
            backup_time = _read_backup_time(zf)

    token = secrets.token_hex(8)
    session_dir = Path(tempfile.mkdtemp(prefix="restore_"))
    (session_dir / "backup.zip").write_bytes(zip_bytes)
    _expire_sessions()
    _restore_sessions[token] = {
        "zip_path": str(session_dir / "backup.zip"),
        "dir": str(session_dir),
        "files": names,
        "backup_time": backup_time,
        "created_at": datetime.now(),
    }
    return {
        "token": token,
        "files": names,
        "backup_time": backup_time,
    }


def _read_backup_time(zf: zipfile.ZipFile) -> str:
    """从 database.db 的 zip 条目读取备份时间。"""
    info = zf.getinfo("database.db")
    try:
        return "%04d-%02d-%02d %02d:%02d" % info.date_time[:5]
    except (TypeError, ValueError):
        return "未知"


# ============ 恢复：应用 ============
def _extract_zip(zip_path: Path, extract_dir: Path) -> None:
    """安全解压 zip：逐个文件复制，拒绝路径穿越和符号链接。"""
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            if not _is_safe_member(info):
                raise BackupError("备份包含非法路径或链接，已拒绝")

            target = extract_dir / Path(info.filename)
            target.resolve().relative_to(extract_dir.resolve())
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)


def _atomic_copy(src: Path, target: Path) -> None:
    """同目录生成临时文件后原子替换，避免复制中断导致目标文件损坏。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=".restore_", suffix=target.suffix, dir=target.parent
    )
    try:
        with os.fdopen(fd, "wb") as output, src.open("rb") as source:
            shutil.copyfileobj(source, output)
        os.replace(tmp_name, target)
    except Exception:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def apply_restore(token: str) -> dict[str, Any]:
    """确认恢复：先备份当前数据，热重载连接后原子替换，再触发向量对账。"""
    _expire_sessions()
    session = _restore_sessions.get(token)
    if session is None:
        raise BackupError("恢复会话已过期，请重新选择备份文件")

    zip_path = Path(session["zip_path"])
    if not zip_path.is_file():
        raise BackupError("暂存备份已丢失，请重新选择")

    extract_dir = Path(session["dir"]) / "extract"
    try:
        _extract_zip(zip_path, extract_dir)
        new_db = extract_dir / "database.db"
        _check_database(new_db.read_bytes())
    except BackupError:
        raise
    except OSError as exc:
        raise BackupError(f"备份解压失败，当前数据未改动：{exc}")

    # 先备份当前数据，防止恢复失败
    pre_backup = save_pre_restore_backup()

    from services import db as db_service

    # 释放连接池，避免数据库文件被占用
    db_service.engine.dispose()

    try:
        # 数据库必须原子替换，不能直接覆盖
        _atomic_copy(new_db, config.DB_PATH)

        for src in sorted(extract_dir.rglob("*")):
            if not src.is_file() or src == new_db:
                continue
            rel = src.relative_to(extract_dir).as_posix()
            parts = Path(rel).parts
            if not rel or rel.startswith("/") or ":" in rel or ".." in parts:
                continue
            _atomic_copy(src, config.DATA_DIR / Path(rel))
    except OSError as exc:
        raise BackupError(f"恢复写入中断，可用 {pre_backup} 回滚：{exc}")

    # 替换后执行建表和旧库迁移，保证恢复库结构可用
    try:
        db_service.init_db()
    except Exception as exc:
        raise BackupError(f"恢复后数据库初始化失败，可用 {pre_backup} 回滚：{exc}")

    vector_warning = ""
    try:
        from services import vector_service
        vector_service.start_background_sync()
    except Exception as exc:  # 向量对账失败只提示，不影响恢复结果
        vector_warning = f"向量重建未启动：{exc}"
        logger.warning(vector_warning)

    _discard_session(token)
    return {
        "restored": True,
        "pre_backup": pre_backup,
        "vector_warning": vector_warning,
    }


def _discard_session(token: str) -> None:
    """删除一次性恢复会话及其临时文件。"""
    session = _restore_sessions.pop(token, None)
    if not session:
        return
    shutil.rmtree(session["dir"], ignore_errors=True)


def _expire_sessions() -> None:
    """清理过期恢复会话。"""
    now = datetime.now()
    expired = [
        token for token, session in _restore_sessions.items()
        if now - session["created_at"] > _SESSION_TTL
    ]
    for token in expired:
        _discard_session(token)


# ============ 启动自动备份 ============
def auto_backup_on_startup() -> None:
    """每天首次启动备份 database.db，并清理超过 7 天的自动备份。"""
    config.AUTO_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    today_name = f"auto_{datetime.now().strftime('%Y%m%d')}.db"
    today_path = config.AUTO_BACKUP_DIR / today_name

    try:
        if not today_path.exists() and config.DB_PATH.is_file():
            shutil.copy2(config.DB_PATH, today_path)
            logger.info("已生成当日自动备份 %s", today_name)
    except OSError:
        logger.exception("启动自动备份失败")

    cutoff = datetime.now() - timedelta(days=7)
    for path in config.AUTO_BACKUP_DIR.glob("auto_*.db"):
        try:
            if datetime.fromtimestamp(path.stat().st_mtime) < cutoff:
                path.unlink()
        except OSError:
            logger.warning("清理旧自动备份失败：%s", path)
