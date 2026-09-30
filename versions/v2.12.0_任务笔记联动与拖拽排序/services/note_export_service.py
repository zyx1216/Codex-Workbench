# -*- coding: utf-8 -*-
"""
笔记 Markdown 导出服务。
单篇导出为 Markdown，多篇导出按分类打包成 zip。
"""

import io
import json
import re
import zipfile
from typing import Any

from sqlalchemy import select

from models.models import Note

# Windows 文件名非法字符和控制字符
_INVALID_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def sanitize_filename(name: Any) -> str:
    """清理文件名里的非法字符，空名称交给调用方回退。"""
    value = str(name or "").strip()
    value = _INVALID_FILENAME_CHARS.sub("_", value)
    return value.strip(" .")


def _note_tags(note: Note) -> list[str]:
    """读取笔记标签，异常时按空标签处理。"""
    try:
        tags = json.loads(note.tags) if note.tags else []
    except (TypeError, json.JSONDecodeError):
        return []
    return tags if isinstance(tags, list) else []


def build_markdown(note: Note) -> str:
    """把笔记转换成统一 Markdown 格式。"""
    tags_text = "、".join(str(tag) for tag in _note_tags(note))
    created_text = (
        note.created_at.strftime("%Y-%m-%d %H:%M:%S")
        if note.created_at else ""
    )
    return (
        f"# {note.title or '无标题'}\n\n"
        f"- 分类：{note.category or '默认'}\n"
        f"- 标签：{tags_text}\n"
        f"- 创建时间：{created_text}\n\n"
        f"---\n\n"
        f"{note.content or ''}\n"
    )


def get_notes_by_ids(session: Any, note_ids: list[int]) -> list[Note]:
    """按 ID 查询笔记，并按入参顺序返回。"""
    ids = list(dict.fromkeys(note_ids))
    if not ids:
        return []
    notes = session.scalars(select(Note).where(Note.id.in_(ids))).all()
    note_map = {note.id: note for note in notes}
    return [note_map[note_id] for note_id in ids if note_id in note_map]


def build_zip(notes: list[Note]) -> bytes:
    """把多篇 Markdown 打入 zip，同名文件自动追加序号。"""
    buffer = io.BytesIO()
    used_names: dict[str, set[str]] = {}

    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for note in notes:
            category = sanitize_filename(note.category or "默认") or "默认"
            base_name = sanitize_filename(note.title) or "无标题"
            file_name = f"{base_name}.md"
            used = used_names.setdefault(category, set())

            index = 1
            while file_name in used:
                file_name = f"{base_name}_{index}.md"
                index += 1
            used.add(file_name)

            archive.writestr(
                f"{category}/{file_name}",
                build_markdown(note),
            )

    return buffer.getvalue()
