# -*- coding: utf-8 -*-
"""任务与笔记双向关联的公共逻辑。"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from models.models import Note, Task

MAX_LINKS = 3


class LinkError(Exception):
    """关联参数错误。"""


def parse_ids(raw: Any) -> list[int]:
    """解析 JSON ID 数组；损坏数据按空数组处理。"""
    try:
        values = json.loads(raw) if raw else []
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(values, list):
        return []
    result: list[int] = []
    for value in values:
        try:
            item_id = int(value)
        except (TypeError, ValueError):
            continue
        if item_id > 0 and item_id not in result:
            result.append(item_id)
    return result


def note_ids_for_task(task: Task) -> list[int]:
    """读取任务的关联笔记；旧 note_id 自动作为兼容回退。"""
    ids = parse_ids(task.note_ids)
    if not ids and task.note_id:
        ids = [int(task.note_id)]
    return ids[:MAX_LINKS]


def task_ids_for_note(note: Note) -> list[int]:
    """读取笔记的关联任务。"""
    return parse_ids(note.task_ids)[:MAX_LINKS]


def normalize_ids(values: Any) -> list[int]:
    """杂数组转去重 ID；非法值直接忽略。"""
    if not isinstance(values, (list, tuple, set)):
        return []
    result: list[int] = []
    for value in values:
        try:
            item_id = int(value)
        except (TypeError, ValueError):
            continue
        if item_id > 0 and item_id not in result:
            result.append(item_id)
    return result


def validate_note_ids(session: Session, values: Any) -> list[int]:
    """校验关联笔记数量和存在性。"""
    ids = normalize_ids(values)
    if len(ids) > MAX_LINKS:
        raise LinkError(f"一个任务最多关联 {MAX_LINKS} 篇笔记")
    for note_id in ids:
        if session.get(Note, note_id) is None:
            raise LinkError("关联笔记不存在")
    return ids


def validate_task_ids(session: Session, values: Any) -> list[int]:
    """校验关联任务数量和存在性。"""
    ids = normalize_ids(values)
    if len(ids) > MAX_LINKS:
        raise LinkError(f"一篇笔记最多关联 {MAX_LINKS} 个任务")
    for task_id in ids:
        if session.get(Task, task_id) is None:
            raise LinkError("关联任务不存在")
    return ids


def set_task_notes(session: Session, task: Task, values: Any) -> list[int]:
    """设置任务的关联笔记，并同步对方 task_ids。"""
    ids = validate_note_ids(session, values)
    old_ids = note_ids_for_task(task)

    for note_id in old_ids:
        if note_id in ids:
            continue
        note = session.get(Note, note_id)
        if note is not None:
            note.task_ids = json.dumps(
                [item for item in task_ids_for_note(note) if item != task.id],
                ensure_ascii=False,
            )

    for note_id in ids:
        note = session.get(Note, note_id)
        task_ids = task_ids_for_note(note)
        if task.id not in task_ids:
            if len(task_ids) >= MAX_LINKS:
                raise LinkError(f"笔记《{note.title}》最多关联 {MAX_LINKS} 个任务")
            task_ids.append(task.id)
        note.task_ids = json.dumps(task_ids, ensure_ascii=False)

    task.note_ids = json.dumps(ids, ensure_ascii=False)
    task.note_id = ids[0] if ids else None
    return ids


def set_note_tasks(session: Session, note: Note, values: Any) -> list[int]:
    """设置笔记的关联任务，并同步对方 note_ids 和旧 note_id。"""
    ids = validate_task_ids(session, values)
    old_ids = task_ids_for_note(note)

    for task_id in old_ids:
        if task_id in ids:
            continue
        task = session.get(Task, task_id)
        if task is None:
            continue
        current = [item for item in note_ids_for_task(task) if item != note.id]
        task.note_ids = json.dumps(current, ensure_ascii=False)
        task.note_id = current[0] if current else None

    for task_id in ids:
        task = session.get(Task, task_id)
        current = note_ids_for_task(task)
        if note.id not in current:
            if len(current) >= MAX_LINKS:
                raise LinkError(f"任务《{task.title}》最多关联 {MAX_LINKS} 篇笔记")
            current.append(note.id)
        task.note_ids = json.dumps(current, ensure_ascii=False)
        task.note_id = current[0] if current else None

    note.task_ids = json.dumps(ids, ensure_ascii=False)
    return ids
