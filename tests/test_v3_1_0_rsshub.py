# -*- coding: utf-8 -*-
"""v3.1.0 RSSHub 视频列表测试。"""

from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import httpx

from services import bilibili_service


RSS_XML = '''<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>UP动态</title>
<item>
  <title>RAG 实战教程</title>
  <link>https://www.bilibili.com/video/BV1TEST001/</link>
  <description><![CDATA[<p>从零实现 RAG</p>]]></description>
  <pubDate>Tue, 20 Oct 2026 10:20:00 GMT</pubDate>
</item>
<item><title>非视频动态</title><link>https://t.bilibili.com/123</link></item>
</channel></rss>'''


class RsshubTests(unittest.TestCase):
    def test_parse_rsshub_items_extracts_bvid_and_metadata(self):
        items = bilibili_service._parse_rsshub_items(RSS_XML, "2")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["bvid"], "BV1TEST001")
        self.assertEqual(items[0]["title"], "RAG 实战教程")
        self.assertEqual(items[0]["description"], "从零实现 RAG")
        self.assertIsNotNone(items[0]["publish_time"])

    def test_rsshub_uses_next_instance_after_failure(self):
        response = Mock()
        response.text = RSS_XML
        response.raise_for_status = Mock()
        calls = []

        def fake_get(url, **kwargs):
            calls.append(url)
            if len(calls) == 1:
                raise httpx.ConnectError("主实例超时")
            return response

        with patch.object(bilibili_service.httpx, "get", side_effect=fake_get):
            items = bilibili_service.fetch_creator_videos("2")
        self.assertEqual(items[0]["bvid"], "BV1TEST001")
        self.assertEqual(len(calls), 2)
        self.assertIn("rsshub.app", calls[0])
        self.assertIn("rssforever", calls[1])

    def test_all_rsshub_instances_fail_with_chinese_error(self):
        with patch.object(bilibili_service.httpx, "get", side_effect=httpx.ConnectError("失败")):
            with self.assertRaisesRegex(bilibili_service.BilibiliError, "RSSHub暂时不可用"):
                bilibili_service.fetch_creator_videos("2")


if __name__ == "__main__":
    unittest.main()
