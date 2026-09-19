# -*- coding: utf-8 -*-
"""
LLM 调用封装（OpenAI 兼容接口）。

密钥和普通配置分开保存：
- API Key 用 keyring 写入 Windows 凭据管理器，不进入项目文件；
- provider、base_url、model 存到 data/llm_config.json。

所有外部调用统一设置 30 秒超时；首次失败后最多重试 2 次。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import config

# Windows 凭据管理器里的服务名和用户名
KEYRING_SERVICE = "personal-workbench"
KEYRING_USERNAME = "llm_api_key"

CONFIG_PATH = Path(config.DATA_DIR) / "llm_config.json"

# 调用参数
TIMEOUT_SECONDS = 30
MAX_RETRIES = 2          # 首次失败后的额外尝试次数
RETRY_BACKOFF = [1, 2]   # 每次重试前等待秒数

# OpenAI SDK 填根地址，由 SDK 自动拼接 /chat/completions
PROVIDER_URLS = {
    "doubao": "https://ark.cn-beijing.volces.com/api/v3",
    "agentplan": "https://ark.cn-beijing.volces.com/api/plan/v3",
}

DEFAULT_CONFIG = {
    "provider": "doubao",
    "base_url": PROVIDER_URLS["doubao"],
    "model": "",
}


class LLMConfigError(Exception):
    """配置不完整或无效。"""


class LLMCallError(Exception):
    """模型调用失败；错误信息已翻译成中文大白话。"""


def load_llm_config() -> dict:
    """读取非敏感配置；文件不存在或损坏时返回默认配置。"""
    if not CONFIG_PATH.exists():
        return dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            saved = json.load(f)
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULT_CONFIG)

    result = dict(DEFAULT_CONFIG)
    if isinstance(saved, dict):
        for key in ("provider", "base_url", "model"):
            value = saved.get(key)
            if isinstance(value, str):
                result[key] = value.strip()

    provider = result["provider"]
    if provider in PROVIDER_URLS:
        # 预置服务商始终以代码中的官方地址为准，避免配置文件被手动改坏
        result["base_url"] = PROVIDER_URLS[provider]
    elif provider != "custom":
        result["provider"] = "custom"
    return result


def save_llm_config(provider: str, base_url: str, model: str) -> None:
    """保存服务商、Base URL 和模型名；不接收也不保存 API Key。"""
    provider = (provider or "").strip()
    base_url = (base_url or "").strip().rstrip("/")
    model = (model or "").strip()

    if provider not in ("doubao", "agentplan", "custom"):
        raise LLMConfigError("服务商选项无效，请重新选择。")
    if provider in PROVIDER_URLS:
        base_url = PROVIDER_URLS[provider]
    if not base_url:
        raise LLMConfigError("自定义服务商必须填写 API 地址。")
    if not model:
        raise LLMConfigError("请填写模型名称。")

    config.ensure_dirs()
    data = {"provider": provider, "base_url": base_url, "model": model}
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_api_key() -> str | None:
    """从 Windows 凭据管理器读取 API Key；读不到时返回 None。"""
    try:
        import keyring
    except ImportError:
        return None
    try:
        return keyring.get_password(KEYRING_SERVICE, KEYRING_USERNAME)
    except Exception:
        # 个别系统后端不可用时，统一按“未配置 Key”处理
        return None


def set_api_key(api_key: str) -> None:
    """把 API Key 写入凭据管理器；传空字符串表示删除已存 Key。"""
    try:
        import keyring
        from keyring.errors import PasswordDeleteError
    except ImportError as exc:
        raise LLMConfigError("当前环境缺少 keyring 库，无法安全保存密钥。") from exc

    api_key = (api_key or "").strip()
    try:
        if api_key:
            keyring.set_password(KEYRING_SERVICE, KEYRING_USERNAME, api_key)
        else:
            try:
                keyring.delete_password(KEYRING_SERVICE, KEYRING_USERNAME)
            except PasswordDeleteError:
                # 本来就没有 Key，删除时忽略即可
                pass
    except Exception as exc:
        raise LLMConfigError(f"写入系统凭据管理器失败：{exc}") from exc


def is_configured() -> bool:
    """是否具备调用条件：API Key、API 地址、模型名三者都要有。"""
    cfg = load_llm_config()
    return bool(get_api_key() and cfg.get("base_url") and cfg.get("model"))


def _build_client():
    """构造 OpenAI 兼容客户端；配置不全时给中文错误。"""
    from openai import OpenAI

    cfg = load_llm_config()
    api_key = get_api_key()
    if not api_key:
        raise LLMConfigError("还没有配置 API Key，请到“⚙️ 设置”填写。")
    if not cfg.get("base_url"):
        raise LLMConfigError("还没有填写 API 地址，请到“⚙️ 设置”填写。")
    if not cfg.get("model"):
        raise LLMConfigError("还没有填写模型名称，请到“⚙️ 设置”填写。")

    client = OpenAI(api_key=api_key, base_url=cfg["base_url"], timeout=TIMEOUT_SECONDS)
    return client, cfg["model"]


def chat(system_prompt: str, user_prompt: str, temperature: float = 0.7) -> str:
    """调用聊天模型并返回文本；失败时按 1 秒、2 秒间隔重试。"""
    client, model = _build_client()
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    last_error = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
            )
            content = resp.choices[0].message.content
            if content and content.strip():
                return content.strip()
            raise LLMCallError("模型返回了空内容，请稍后重试。")
        except LLMConfigError:
            raise
        except Exception as exc:
            last_error = exc
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF[attempt])

    raise LLMCallError(_friendly_error(last_error)) from last_error


def test_connection() -> tuple[bool, str]:
    """发一条最短消息测试连接；返回是否成功和中文说明，不向页面抛异常。"""
    try:
        reply = chat("你是一个连接测试助手。", "只回复两个字：正常", temperature=0)
        return True, f"连接成功，模型回复：{reply[:30]}"
    except (LLMConfigError, LLMCallError) as exc:
        return False, str(exc)


def _friendly_error(exc: Exception | None) -> str:
    """把底层异常翻译成普通用户能看懂的中文提示。"""
    text = str(exc or "").lower()
    if "429" in text or "rate limit" in text or "too many requests" in text:
        return "请求太频繁或账号额度不足，请稍后再试，或检查账户额度。"
    if "authentication" in text or "401" in text or "api key" in text:
        return "API Key 不正确或已失效，请检查后重新填写。"
    if "404" in text or ("model" in text and "not" in text):
        return "找不到这个模型或接口地址，请核对模型名和服务商。"
    if "timeout" in text or "timed out" in text:
        return "请求超时了（超过 30 秒），可能是网络慢，请稍后再试。"
    if "connection" in text or "resolve host" in text or "unreachable" in text:
        return "连不上服务器，请检查网络或 API 地址是否填对。"
    return f"调用失败（已重试 2 次）：{str(exc)[:120]}"