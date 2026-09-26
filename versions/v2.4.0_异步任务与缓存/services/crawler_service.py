# -*- coding: utf-8 -*-
"""
网页抓取与正文提取（v1.1 新增）。

- fetch_url：httpx 抓取，浏览器 UA，15 秒超时，自动跟随跳转
- extract_content：BeautifulSoup 提取标题和正文，去掉无关标签
网络错误、非 2xx、超时等统一抛 CrawlError，由 API 层转成 code=1。
- extract_from_text：处理用户手动粘贴的正文，不发起网络请求
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# 伪装成桌面浏览器，降低被简单反爬拦截的概率
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# 抓取超时：连接 / 读取都限 15 秒，避免页面卡死
FETCH_TIMEOUT = httpx.Timeout(15.0, connect=10.0)

# 送入模型的正文上限，超出截断，保护上下文窗口
MAX_CONTENT_LENGTH = 8000

# 提取正文时直接剔除的噪声标签
DROP_TAGS = ("script", "style", "nav", "footer", "header", "aside", "noscript")

# 连续空行压缩用
_BLANK_LINES = re.compile(r"\n{3,}")
# 行内多余空白压缩
_INLINE_SPACES = re.compile(r"[ \t\u3000]{2,}")


class CrawlError(Exception):
    """抓取或正文提取失败，message 为可直接展示给用户的中文信息。"""


def fetch_url(url: str) -> tuple[bytes, str]:
    """抓取网页，返回（HTML 字节内容, 最终 URL）。跳转后最终 URL 可能与输入不同。"""
    url = (url or "").strip()
    if not url:
        raise CrawlError("请输入链接")
    if not url.lower().startswith(("http://", "https://")):
        raise CrawlError("链接需以 http:// 或 https:// 开头")

    try:
        with httpx.Client(
            headers={"User-Agent": USER_AGENT},
            timeout=FETCH_TIMEOUT,
            follow_redirects=True,
        ) as client:
            response = client.get(url)
    except httpx.TimeoutException as exc:
        raise CrawlError("抓取超时，请检查网页能否正常打开后重试") from exc
    except httpx.HTTPError as exc:
        logger.warning("网页抓取失败：%s", exc)
        raise CrawlError("网页打不开，请检查链接或网络") from exc

    if response.status_code >= 400:
        raise CrawlError(f"网页返回错误状态码 {response.status_code}")

    return response.content, str(response.url)


def extract_content(html_bytes: bytes, url: str) -> dict[str, str]:
    """从 HTML 提取标题和正文，返回 {title, content, url}。

    编码由 BeautifulSoup 根据页面声明自动识别（兼容 gbk 页面）。
    正文优先 article 标签，否则取文本最多的块，兜底整页文本。
    """
    try:
        soup = BeautifulSoup(html_bytes, "html.parser")
    except Exception as exc:  # 解析器本身异常极少见，仍需兜底
        raise CrawlError("网页解析失败") from exc

    # 先删除噪声标签，避免导航、页脚文字混进正文
    for tag_name in DROP_TAGS:
        for tag in soup.find_all(tag_name):
            tag.decompose()

    title = _extract_title(soup)

    # 优先使用 article 标签；没有则在主要容器里挑文本最多的
    candidates = []
    article = soup.find("article")
    if article:
        candidates.append(article)
    for container in soup.find_all(("div", "section", "main")):
        text = container.get_text(strip=True)
        if text:
            candidates.append(container)

    body_text = ""
    if candidates:
        best = max(candidates, key=lambda node: len(node.get_text(strip=True)))
        body_text = _clean_text(best.get_text("\n"))
    if not body_text:
        # 兜底：整页取文本
        body_text = _clean_text(soup.get_text("\n"))

    if not body_text:
        raise CrawlError("未提取到有效正文，可能是动态渲染页面")

    if len(body_text) > MAX_CONTENT_LENGTH:
        body_text = body_text[:MAX_CONTENT_LENGTH]

    return {"title": title, "content": body_text, "url": url}



def extract_from_text(title: str, content: str, url: str) -> dict[str, str]:
    """处理用户手动粘贴的标题、正文和来源链接；整个过程不请求网络。"""
    title = (title or "").strip()
    url = (url or "").strip()
    body_text = _clean_text(content or "")
    if not body_text:
        raise CrawlError("请粘贴正文内容")

    if len(body_text) > MAX_CONTENT_LENGTH:
        body_text = body_text[:MAX_CONTENT_LENGTH].rstrip()
        body_text += "\n\n内容过长已截断"

    return {"title": title, "content": body_text, "url": url}

def _extract_title(soup: BeautifulSoup) -> str:
    """标题优先 og:title，其次 title 标签，最后 h1。"""
    og_title = soup.find("meta", attrs={"property": "og:title"})
    if og_title and og_title.get("content", "").strip():
        return og_title["content"].strip()
    if soup.title and soup.title.get_text(strip=True):
        return soup.title.get_text(strip=True)
    h1 = soup.find("h1")
    if h1 and h1.get_text(strip=True):
        return h1.get_text(strip=True)
    return ""


def _clean_text(text: str) -> str:
    """整理正文空白：逐行去首尾空字符，压缩连续空行。"""
    lines = [line.strip() for line in text.splitlines()]
    # 空行保留为段落间隔，随后把 3 个以上换行压成 2 个
    merged = "\n".join(lines)
    merged = _INLINE_SPACES.sub(" ", merged)
    merged = _BLANK_LINES.sub("\n\n", merged)
    return merged.strip()
