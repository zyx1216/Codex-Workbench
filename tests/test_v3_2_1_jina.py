# -*- coding: utf-8 -*-
"""v3.2.1 Jina Reader 与热点笔记测试。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models.models import Base, HotTrend
from services import jina_service, trend_service


class JinaTests(unittest.TestCase):
    def test_fetch_article_text_truncates_to_8000(self):
        response = Mock()
        response.text = "a" * 9000
        response.raise_for_status = Mock()
        with patch.object(jina_service.httpx, "get", return_value=response):
            text = jina_service.fetch_article_text("https://example.com")
        self.assertLessEqual(len(text), 8014)
        self.assertIn("内容过长已截断", text)

    def test_fetch_article_text_returns_empty_on_error(self):
        with patch.object(jina_service.httpx, "get", side_effect=RuntimeError("timeout")):
            self.assertEqual(jina_service.fetch_article_text("https://example.com"), "")


class TrendNoteTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine)()
        self.trend = HotTrend(platform="zhihu", title="AI 热点", url="https://example.com", heat=100, fetched_date="2026-10-21")
        self.session.add(self.trend)
        self.session.commit()

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def test_generate_note_with_fetched_content(self):
        note = SimpleNamespace(id=12, title="AI 热点")
        ai_json = '{"summary":"摘要","core_points":["核心"],"knowledge_points":["知识"],"quotes":["金句"],"actions":["行动"]}'
        with patch.object(trend_service.jina_service, "fetch_article_text", return_value="原文内容"), \
             patch.object(trend_service.ai_service, "chat", return_value=ai_json), \
             patch.object(trend_service.note_service, "create_note", return_value=note) as create:
            result, created = trend_service.generate_note(self.session, self.trend.id)
        self.assertTrue(created)
        self.assertEqual(result.id, 12)
        content = create.call_args.args[2]
        self.assertIn("来源：知乎", content)
        self.assertNotIn("未获取到原文", content)
        self.assertTrue(self.session.get(HotTrend, self.trend.id).content_fetched)

    def test_generate_note_without_content_marks_warning(self):
        note = SimpleNamespace(id=13, title="AI 热点")
        with patch.object(trend_service.jina_service, "fetch_article_text", return_value=""), \
             patch.object(trend_service.ai_service, "chat", side_effect=trend_service.ai_service.AiError("失败")), \
             patch.object(trend_service.note_service, "create_note", return_value=note) as create:
            trend_service.generate_note(self.session, self.trend.id)
        content = create.call_args.args[2]
        self.assertIn("未获取到原文", content)
        self.assertFalse(self.session.get(HotTrend, self.trend.id).content_fetched)


if __name__ == "__main__":
    unittest.main()
