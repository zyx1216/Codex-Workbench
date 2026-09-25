# -*- coding: utf-8 -*-
"""
AI 配置存取：keyring 存 Key，data/ai_config.json 存非敏感配置。
v1.0 只做配置存取，不发请求（v1.1 接入 openai 客户端）。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from v2 import config

logger = logging.getLogger(__name__)

KEYRING_SERVICE = "knowledge-digest"
KEYRING_USERNAME = "ai_api_key"

# 配置默认值：用于 ai_config.json 不存在或字段缺失时回退
DEFAULT_CONFIG: dict[str, str] = {
    "provider": "",
    "base_url": "",
    "model": "",
}


def _load_keyring() -> "Any":
    """延迟导入 keyring，缺失时给出明确错误。"""
    try:
        import keyring  # noqa: WPS433
        return keyring
    except ImportError as exc:
        raise RuntimeError("当前环境缺少 keyring 库，无法安全保存密钥。") from exc


def load_config() -> dict[str, str]:
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


def save_config(provider: str, base_url: str, model: str) -> None:
    """写入非敏感 AI 配置到 ai_config.json（覆盖写）。"""
    config.ensure_dirs()
    payload = {
        "provider": (provider or "").strip(),
        "base_url": (base_url or "").strip(),
        "model": (model or "").strip(),
    }
    config.AI_CONFIG_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def get_api_key() -> str | None:
    """从 keyring 读取 API Key；不存在返回 None。"""
    try:
        return _load_keyring().get_password(KEYRING_SERVICE, KEYRING_USERNAME)
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取 keyring 失败：%s", exc)
        return None


class KeyringError(RuntimeError):
    """凭据管理器写入/读取失败。当前 Codex 沙箱进程的 token 无法访问 Windows Credential Manager 时也会触发。"""


def set_api_key(api_key: str) -> None:
    """写入 API Key 到 keyring；失败抛出 KeyringError。"""
    try:
        _load_keyring().set_password(KEYRING_SERVICE, KEYRING_USERNAME, api_key)
    except Exception as exc:  # noqa: BLE001 - 跨平台 keyring 异常类型不统一
        raise KeyringError(f"无法写入凭据管理器：{exc}") from exc


def is_configured() -> bool:
    """provider / base_url / model / api_key 四项都齐全时返回 True。"""
    cfg = load_config()
    return bool(cfg.get("provider") and cfg.get("base_url") and cfg.get("model") and get_api_key())

