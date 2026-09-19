# -*- coding: utf-8 -*-
"""
个人工作台 —— Streamlit 主入口。

职责：
1. 启动时初始化数据目录和 SQLite 数据库；
2. 用侧边栏做 7 个页面的导航；
3. 把主内容区交给对应的页面模块渲染。

侧边栏导航绑定 session_state 的 app_page，业务页（如日历点“编辑计划”）
可以写入该键实现跨页跳转，用户手动点侧边栏也照常生效。
"""

import streamlit as st

import config
from utils.db import init_db
from modules import calendar, knowledge, links, notes, plans, settings, today

# 启动时确保目录存在、数据表就绪（幂等操作，不会清空数据）
init_db()

# 页面基础设置
st.set_page_config(
    page_title=config.APP_NAME,
    page_icon="🧰",
    layout="wide",
)

# 侧边栏导航（状态驱动，允许其他页面通过 session_state 切换页面）
PAGE_OPTIONS = [
    "📊 今日",
    "📅 日历",
    "📝 计划",
    "📔 笔记",
    "🔗 收藏",
    "📚 知识库",
    "⚙️ 设置",
]

st.sidebar.title("🧰 个人工作台")
if "app_page" not in st.session_state:
    st.session_state.app_page = PAGE_OPTIONS[0]
st.sidebar.radio(
    label="功能导航",
    options=PAGE_OPTIONS,
    key="app_page",
    label_visibility="collapsed",
)
st.sidebar.caption(f"版本 v{config.APP_VERSION}")

# 根据选择渲染对应页面（每个模块统一暴露 show() 函数）
page = st.session_state.app_page
if page == "📊 今日":
    today.show()
elif page == "📅 日历":
    calendar.show()
elif page == "📝 计划":
    plans.show()
elif page == "📔 笔记":
    notes.show()
elif page == "🔗 收藏":
    links.show()
elif page == "📚 知识库":
    knowledge.show()
else:
    settings.show()
