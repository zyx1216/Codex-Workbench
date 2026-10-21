# -*- coding: utf-8 -*-
"""v3.2.3 知乎降级与热点 AI 降级测试。"""

from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models.models import Base, HotTrend
from services import ai_service, trend_service


class TrendV323Tests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine)()
        trend_service._platform_status["zhihu"] = {"available": None, "message": ""}
        trend_service._ai_failure_reported = False

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def test_zhihu_billboard_html_parses_titles_without_api(self):
        response = Mock()
        response.text = '<a href="https://www.zhihu.com/question/1">问题一</a><a href="https://www.zhihu.com/question/2">问题二</a>'
        response.raise_for_status = Mock()
        with patch.object(trend_service.httpx, "get", return_value=response) as http_get:
            items = trend_service._zhihu_items()
        self.assertEqual([item["title"] for item in items], ["问题一", "问题二"])
        self.assertIn("billboard", http_get.call_args.args[0])

    def test_zhihu_failure_is_optional_and_marked_unavailable(self):
        bili = [{"platform": "bilibili", "title": "B站热点", "heat": 1, "url": "https://b"}]
        with patch.object(trend_service, "_bilibili_items", return_value=bili), \
             patch.object(trend_service, "_zhihu_items", side_effect=trend_service.TrendError("401")), \
             patch.object(trend_service, "_weibo_items", return_value=[]), \
             patch.object(trend_service.time, "sleep"), \
             patch.object(ai_service, "chat", return_value='{"score": 60, "reason": "一般", "category": "科技"}'):
            result = trend_service.refresh_trends(self.session)
        zhihu = next(item for item in result["results"] if item["platform"] == "zhihu")
        self.assertFalse(zhihu["available"])
        self.assertTrue(zhihu["optional"])
        self.assertFalse(trend_service.get_stats(self.session)["platform_status"]["zhihu"]["available"])

    def test_ai_config_failure_degrades_without_exception(self):
        bili = [{"platform": "bilibili", "title": "B站热点", "heat": 1, "url": "https://b"}]
        with patch.object(trend_service, "_bilibili_items", return_value=bili), \
             patch.object(trend_service, "_zhihu_items", return_value=[]), \
             patch.object(trend_service, "_weibo_items", return_value=[]), \
             patch.object(trend_service.time, "sleep"), \
             patch.object(ai_service, "chat", side_effect=ai_service.AiError("请先在设置页填写 Base URL 并保存")):
            trend_service.refresh_trends(self.session)
        item = self.session.query(HotTrend).one()
        self.assertEqual(item.ai_score, 50)
        self.assertEqual(item.ai_reason, "AI未配置")


if __name__ == "__main__":
    unittest.main()
