# -*- coding: utf-8 -*-
"""
AI 配置与调用封装（v1.1 完善真实调用）。

- get_config / save_config：非敏感配置存 ai_config.json，Key 存 keyring
- chat：调用 OpenAI 兼容接口，30 秒超时，重试 1 次
- rewrite_to_plain：改写成通俗小白笔记
- generate_tags：生成标签，失败降级空列表
- generate_title：根据手动粘贴正文生成标题
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import config

logger = logging.getLogger(__name__)

KEYRING_SERVICE = "knowledge-digest"
KEYRING_USERNAME = "ai_api_key"

# 配置默认值：ai_config.json 不存在或字段缺失时回退
DEFAULT_CONFIG: dict[str, str] = {"base_url": "", "model": ""}

# 模型调用超时 30 秒，SDK 内部重试 1 次（共最多请求 2 次）
CHAT_TIMEOUT = 30.0
CHAT_MAX_RETRIES = 1

# 改写笔记的系统提示词（严格按需求原文）
REWRITE_SYSTEM_PROMPT = (
    "你是一个知识科普专家，擅长把复杂内容改写成零基础小白也能看懂的通俗笔记。"
    "要求：1.用大白话，避免专业术语，必须用术语时要解释；"
    "2.分点说明，逻辑清晰；3.保留核心信息，不丢失关键内容；"
    "4.字数300-800字；5.结尾加一句“一句话总结”。"
)

# 生成标签的提示词：要求只输出逗号分隔的标签，方便解析
TAGS_PROMPT = (
    "请根据下面文章的标题和正文，生成3-5个概括主题的标签。"
    "只输出标签本身，用英文逗号分隔，不要编号、不要解释、不要加“标签：”等前缀。\n\n"
    "标题：{title}\n\n正文：{content}"
)


# 根据正文生成标题：限制长度，方便手动粘贴时自动补标题
TITLE_PROMPT = (
    "请根据下面正文生成一个中文标题，不超过20个字，"
    "只输出标题，不要解释、不要标点结尾。\n\n正文：{content}"
)


class AiError(Exception):
    """AI 调用失败，message 为可直接展示给用户的中文信息。"""


def _load_keyring() -> Any:
    """延迟导入 keyring，缺失时给出明确错误。"""
    try:
        import keyring  # noqa: WPS433
        return keyring
    except ImportError as exc:
        raise RuntimeError("当前环境缺少 keyring 库，无法读取密钥。") from exc


def get_config() -> dict[str, str]:
    """读取非敏感 AI 配置；文件不存在或损坏时返回默认空配置。"""
    if not config.AI_CONFIG_PATH.exists():
        return DEFAULT_CONFIG.copy()
    try:
        raw = json.loads(config.AI_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("ai_config.json 读取失败：%s", exc)
        return DEFAULT_CONFIG.copy()
    result = DEFAULT_CONFIG.copy()
    for key in DEFAULT_CONFIG:
        value = raw.get(key)
        if isinstance(value, str):
            result[key] = value
    return result


def save_config(base_url: str, model: str, api_key: str) -> bool:
    """写入非敏感 AI 配置；api_key 非空时同步写入 keyring。
    keyring 写入失败不抛出（沙箱环境可能无权限访问 Windows 凭据管理器）。"""
    config.ensure_dirs()
    payload = {
        "base_url": (base_url or "").strip(),
        "model": (model or "").strip(),
    }
    config.AI_CONFIG_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    key_saved = False
    if api_key:
        try:
            _load_keyring().set_password(KEYRING_SERVICE, KEYRING_USERNAME, api_key)
            key_saved = True
        except Exception as exc:  # noqa: BLE001 - 跨平台 keyring 异常类型不统一
            logger.warning("keyring 写入失败（沙箱环境常见）：%s", exc)
    return key_saved


def get_api_key() -> str:
    """从 keyring 读取 API Key；读取失败或未配置返回空字符串。"""
    try:
        return _load_keyring().get_password(KEYRING_SERVICE, KEYRING_USERNAME) or ""
    except Exception as exc:  # noqa: BLE001 - keyring 后端异常类型不统一
        logger.warning("keyring 读取失败：%s", exc)
        return ""


def test_connection() -> str:
    """v1.0 占位文案，真实测试连接后续版本再做。"""
    return "测试功能v1.1实现"


def chat(prompt: str, system_prompt: str | None = None) -> str:
    """调用 OpenAI 兼容接口，返回模型生成文本。

    配置缺失或调用失败抛 AiError，不返回错误字符串冒充正文。
    """
    settings = get_config()
    base_url = settings.get("base_url", "").strip()
    model = settings.get("model", "").strip()
    api_key = get_api_key()

    if not base_url:
        raise AiError("请先在设置页填写 Base URL 并保存")
    if not model:
        raise AiError("请先在设置页填写模型名称并保存")
    if not api_key:
        raise AiError("请先在设置页填写 API Key 并保存")

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    try:
        from openai import OpenAI  # 延迟导入，缺库时给出明确提示
    except ImportError as exc:
        raise RuntimeError("当前环境缺少 openai 库，无法调用大模型。") from exc

    try:
        client = OpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=CHAT_TIMEOUT,
            max_retries=CHAT_MAX_RETRIES,
        )
        completion = client.chat.completions.create(
            model=model,
            messages=messages,
        )
    except Exception as exc:  # noqa: BLE001 - SDK 异常类型随版本变化，统一转换
        logger.warning("模型调用失败：%s", exc)
        raise AiError(f"AI 调用失败：{exc}") from exc

    text = completion.choices[0].message.content
    if not text:
        raise AiError("AI 返回内容为空，请重试")
    return text.strip()


def rewrite_to_plain(title: str, content: str) -> str:
    """把文章改写成通俗易懂的小白笔记。失败抛 AiError。"""
    user_prompt = (
        "把下面这篇文章改写成通俗笔记：\n\n"
        f"标题：{title}\n\n正文：{content}"
    )
    return chat(user_prompt, system_prompt=REWRITE_SYSTEM_PROMPT)


def generate_tags(title: str, content: str) -> list[str]:
    """AI 生成 3-5 个标签；失败时降级返回空列表，不拖垮主流程。"""
    # 为省 token，送入的正文再限一次长度
    short_content = (content or "")[:3000]
    prompt = TAGS_PROMPT.format(title=title or "无标题", content=short_content)
    try:
        raw = chat(prompt)
    except AiError as exc:
        logger.warning("标签生成失败，降级为空标签：%s", exc)
        return []

    # 复用笔记服务的标签清洗逻辑
    from services.note_service import parse_tags

    tags = parse_tags(raw)
    # 只保留前 5 个，防止模型不听话输出一大串
    return tags[:5]


def generate_title(content: str) -> str:
    """根据手动粘贴正文生成标题；失败抛 AiError。"""
    short_content = (content or "")[:3000]
    title = chat(TITLE_PROMPT.format(content=short_content))
    return title.strip()[:20]
