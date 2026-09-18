# -*- coding: utf-8 -*-
"""
设置页面。

当前版本提供 LLM API 配置：
- 预置豆包、火山方舟 Agent Plan 和自定义 OpenAI 兼容服务；
- API Key 存 Windows 凭据管理器；
- Base URL、服务商和模型名存 data/llm_config.json。
"""

import streamlit as st

import config
from utils import llm_client

# 下拉选项的内部值和显示文字分开，配置文件里只保存稳定的内部值
PROVIDER_OPTIONS = ["doubao", "agentplan", "custom"]
PROVIDER_LABELS = {
    "doubao": "豆包（火山引擎）",
    "agentplan": "火山方舟 Agent Plan",
    "custom": "自定义（OpenAI 兼容）",
}


def _llm_config_panel() -> None:
    """渲染 LLM 配置表单。"""
    st.subheader("AI 服务配置")
    cfg = llm_client.load_llm_config()
    saved_provider = cfg.get("provider", "doubao")
    has_key = bool(llm_client.get_api_key())

    if has_key:
        st.success("已保存 API Key。出于安全考虑，页面不显示原文；输入新 Key 会覆盖旧 Key。")
    else:
        st.warning("还没有配置 API Key，AI 功能暂不可用。")

    # 服务商放在表单外：切换后立刻触发 Streamlit 重跑，从而自动填充或解锁 API 地址
    provider = st.selectbox(
        "服务商",
        options=PROVIDER_OPTIONS,
        index=PROVIDER_OPTIONS.index(saved_provider if saved_provider in PROVIDER_OPTIONS else "doubao"),
        format_func=lambda value: PROVIDER_LABELS[value],
        key="settings_llm_provider",
    )

    preset_url = llm_client.PROVIDER_URLS.get(provider)
    saved_url = cfg.get("base_url", "")
    if preset_url:
        base_url_value = preset_url
    elif saved_url in llm_client.PROVIDER_URLS.values():
        # 从预置服务商切到自定义时，不把预置地址误当成用户的自定义地址
        base_url_value = ""
    else:
        base_url_value = saved_url

    with st.form("llm_config_form"):
        base_url = st.text_input(
            "API 地址",
            value=base_url_value,
            disabled=bool(preset_url),
            help="预置服务商地址自动填写；选择自定义后才可修改。",
        )
        model = st.text_input(
            "模型名称",
            value=cfg.get("model", ""),
            placeholder="豆包可填 doubao-pro-32k；Agent Plan 可填 ark-code-latest",
            help="模型名以服务商控制台为准。",
        )
        api_key = st.text_input(
            "API Key",
            type="password",
            placeholder="已保存 Key 时可留空；填写后保存会覆盖旧 Key",
            help="Key 只写入 Windows 凭据管理器，不写入项目文件或 Git。",
        )

        if provider == "agentplan":
            st.caption("Agent Plan 专属 Key 在控制台「开通管理 → Agent Plan」获取，与普通 API Key 不通用。")

        col_save, col_test = st.columns(2)
        save_clicked = col_save.form_submit_button("💾 保存配置", type="primary")
        test_clicked = col_test.form_submit_button("🔌 测试连接")

    if save_clicked or test_clicked:
        try:
            llm_client.save_llm_config(provider, base_url, model)
            # Key 留空表示沿用已保存的 Key；填写新 Key 时覆盖
            if api_key.strip():
                llm_client.set_api_key(api_key.strip())
        except llm_client.LLMConfigError as exc:
            st.error(str(exc))
            return

    if save_clicked:
        st.success("配置已保存。")
        st.rerun()

    if test_clicked:
        with st.spinner("正在连接模型，请稍候……"):
            ok, message = llm_client.test_connection()
        if ok:
            st.success(message)
        else:
            st.error(message)


def _data_panel() -> None:
    """展示本地数据存储位置。"""
    st.subheader("数据存储位置")
    st.write(f"数据库文件：`{config.DB_PATH}`")
    st.write(f"上传文件目录：`{config.UPLOAD_DIR}`")
    st.write(f"导出文件目录：`{config.EXPORT_DIR}`")
    st.write(f"AI 非敏感配置：`{llm_client.CONFIG_PATH}`")
    st.caption("API Key 保存在 Windows 凭据管理器，服务名为 personal-workbench。")


def _about_panel() -> None:
    """展示版本和当前开发范围。"""
    st.subheader("关于")
    st.write(f"**项目名称：** {config.APP_NAME}")
    st.write(f"**当前版本：** v{config.APP_VERSION}")
    st.write("**技术栈：** Streamlit + SQLAlchemy + SQLite，本地单机运行。")
    st.markdown(
        """
        **当前版本已具备**
        - 7 个页面的导航骨架
        - SQLite 数据库和 7 张业务表
        - AI 服务配置、Key 安全保存和连接测试

        **后续版本实现**
        - 今日、日历、计划、笔记、收藏、知识库业务页面
        - 旧 HTML 版本本地数据迁移
        """
    )


def show() -> None:
    """渲染设置页面。"""
    st.title("⚙️ 设置")
    _llm_config_panel()
    st.divider()
    _data_panel()
    st.divider()
    _about_panel()