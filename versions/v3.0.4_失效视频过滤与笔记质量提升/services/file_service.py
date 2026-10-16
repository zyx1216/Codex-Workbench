# -*- coding: utf-8 -*-
"""
文件解析服务（v1.3 新增）。

支持 txt / pdf / docx 三类文件：
- parse_file：按后缀分发解析
- 解析失败（格式不支持、文件损坏、加密、空内容）统一抛 FileParseError
- 内容超 10000 字截断并提示
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 支持的后缀白名单（小写，含点）
ALLOWED_SUFFIXES = {".txt", ".pdf", ".docx"}

# 送入 AI 的正文上限
MAX_CONTENT_LENGTH = 10000

# 截断时追加的提示语
TRUNCATE_HINT = "\n\n（内容过长已截断）"


class FileParseError(Exception):
    """文件解析失败，message 为可直接展示的中文信息。"""


def parse_file(file_path: str | Path, filename: str) -> dict[str, str]:
    """按后缀解析文件，返回 {title: 去后缀文件名, content}。"""
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise FileParseError("不支持的文件格式，仅支持 PDF、Word（docx）和 txt")

    if suffix == ".txt":
        content = _parse_txt(file_path)
    elif suffix == ".pdf":
        content = _parse_pdf(file_path)
    else:
        content = _parse_docx(file_path)

    if not content.strip():
        raise FileParseError("文件内容为空，或未能提取到文本")

    if len(content) > MAX_CONTENT_LENGTH:
        content = content[:MAX_CONTENT_LENGTH] + TRUNCATE_HINT

    title = Path(filename).stem  # 去后缀的文件名
    return {"title": title, "content": content}


def _parse_txt(file_path: str | Path) -> str:
    """解析 txt：先 utf-8 后 gbk，都失败则报错。"""
    raw = Path(file_path).read_bytes()
    for encoding in ("utf-8", "gbk"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise FileParseError("文本编码无法识别，请另存为 utf-8 编码后重试")


def _parse_pdf(file_path: str | Path) -> str:
    """解析 PDF：pdfplumber 逐页提取文本并拼接。"""
    try:
        import pdfplumber
    except ImportError as exc:
        raise FileParseError("当前环境缺少 PDF 解析组件") from exc

    pages_text: list[str] = []
    try:
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    pages_text.append(text)
    except FileParseError:
        raise
    except Exception as exc:  # 加密、损坏等异常类型随库变化，统一转换
        logger.warning("PDF 解析失败：%s", exc)
        raise FileParseError("PDF 解析失败，文件可能已加密或损坏") from exc

    return "\n\n".join(pages_text)


def _parse_docx(file_path: str | Path) -> str:
    """解析 docx：python-docx 遍历段落并拼接。"""
    try:
        from docx import Document
    except ImportError as exc:
        raise FileParseError("当前环境缺少 Word 解析组件") from exc

    try:
        document = Document(file_path)
        paragraphs = [p.text for p in document.paragraphs if p.text.strip()]
    except Exception as exc:  # 文件损坏等异常统一转换
        logger.warning("Word 解析失败：%s", exc)
        raise FileParseError("Word 文件解析失败，文件可能已损坏") from exc

    return "\n".join(paragraphs)
