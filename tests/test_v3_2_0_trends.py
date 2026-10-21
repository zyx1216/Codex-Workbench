# -*- coding: utf-8 -*-
"""v3.2.0 热榜聚合服务测试。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models.models import Base, HotTrend
from services import trend_service


class TrendTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def test_parse_heat(self):
        self.assertEqual(trend_service.parse_heat("1.5万"), 15000)
        self.assertEqual(trend_service.parse_heat("2亿"), 200000000)
        self.assertEqual(trend_service.parse_heat("1234"), 1234)

    def test_refresh_skips_failed_platform_and_deduplicates(self):
        bili = [{"platform": "bilibili", "title": "B站热点", "heat": 100, "url": "https://b"}]
        weibo = [{"platform": "weibo", "title": "微博热点", "heat": 200, "url": "https://w"}]
        ai_json = '{"score": 80, "reason": "值得学习", "category": "AI"}'
        with patch.object(trend_service, "_bilibili_items", return_value=bili), \
             patch.object(trend_service, "_zhihu_items", side_effect=trend_service.TrendError("403")), \
             patch.object(trend_service, "_weibo_items", return_value=weibo), \
             patch.object(trend_service.time, "sleep"), \
             patch.object(trend_service.ai_service, "chat", return_value=ai_json):
            result = trend_service.refresh_trends(self.session)
            trend_service.refresh_trends(self.session)
        self.assertEqual(self.session.query(HotTrend).count(), 2)
        self.assertEqual(result["total_added"], 2)
        self.assertTrue(any(item["error"] for item in result["results"]))

    def test_feedback_and_stats(self):
        self.session.add(HotTrend(platform="bilibili", title="热点", url="https://b", heat=10, ai_score=75, fetched_date="2026-10-21"))
        self.session.commit()
        item = self.session.query(HotTrend).one()
        trend_service.set_feedback(self.session, item.id, "值得看")
        stats = trend_service.get_stats(self.session)
        self.assertEqual(stats["feedback_count"], 1)

    def test_generate_note_links_existing_note(self):
        item = HotTrend(platform="bilibili", title="热点", url="https://b", heat=10, ai_score=75, fetched_date="2026-10-21")
        self.session.add(item); self.session.commit()
        fake_note = SimpleNamespace(id=99, title="热点")
        with patch.object(trend_service.ai_service, "chat", return_value='{"background":"背景","impact":["影响"],"questions":["问题"]}'), \
             patch.object(trend_service.note_service, "create_note", return_value=fake_note):
            note, created = trend_service.generate_note(self.session, item.id)
        self.assertTrue(created)
        self.assertEqual(note.id, 99)
        self.assertEqual(self.session.get(HotTrend, item.id).note_id, 99)


if __name__ == "__main__":
    unittest.main()
