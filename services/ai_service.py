# -*- coding: utf-8 -*-
"""
AI 配置与调用封装（v1.0 框架占位）。

v1.0 只做配置存取：
- get_config() 读 ai_config.json
- save_config(base_url, model, api_key) 写 json + keyring
- test_connection() 返回占位字符串
- chat(prompt) 返回占位字符串
"""

from __future__ import annotations

import json
import logging
from typing import Any

import config

logger = logging.getLogger(__name__)

KEYRING_SERVICE = "knowledge-digest"
KEYRING_USERNAME = "ai_api_key"

# 配置默认值：ai_config.json 不存在或字段缺失时回退
DEFAULT_CONFIG: dict[str, str] = {"base_url": "", "model": ""}


def _load_keyring() -> Any:
    """延迟导入 keyring，缺失时给出明确错误。"""
    try:
        import keyring  # noqa: WPS433
        return keyring
    except ImportError as exc:
        raise RuntimeError("当前环境缺少 keyring 库，无法安全保存密钥。") from exc


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


def test_connection() -> str:
    """v1.0 不真发请求，只返回占位文案。"""
    return "测试功能v1.1实现"


def chat(prompt: str) -> str:
    """v1.0 占位函数，v1.1 接入 openai 客户端。"""
    return "AI功能v1.1实现"
