# -*- coding: utf-8 -*-
"""v3.0 B站跟踪服务回归测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import config
from models.models import Base, BilibiliCreator, BilibiliVideo
from services import bilibili_service


class BilibiliServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.session = self.Session()

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def test_parse_creator_uid_accepts_uid_and_space_url(self):
        self.assertEqual(bilibili_service.parse_creator_uid("123456"), "123456")
        self.assertEqual(bilibili_service.parse_creator_uid("https://space.bilibili.com/123456/video"), "123456")

    def test_refresh_creator_adds_video_and_applies_ai_filter(self):
        creator = BilibiliCreator(uid="123", name="测试UP", face="")
        self.session.add(creator)
        self.session.commit()
        items = [{"bvid": "BV1TEST001", "uid": "123", "title": "AI 实战教程", "description": "从零实现 RAG", "publish_time": None, "duration": 600, "play": 10000, "like": 800, "coin": 100, "favorite": 200, "comment": 50, "share": 20, "cover": ""}]
        with patch.object(bilibili_service, "fetch_creator_info", return_value={"uid": "123", "name": "测试UP", "face": ""}), patch.object(bilibili_service, "fetch_creator_videos", return_value=items), patch.object(bilibili_service, "fetch_video_detail", side_effect=lambda bvid, fallback: fallback), patch.object(bilibili_service.ai_service, "chat", return_value='{"score": 88, "decision": "值得看", "reason": "有实操教程", "category": "AI教程"}'):
            result = bilibili_service.refresh_creator(self.session, creator)
        video = self.session.query(BilibiliVideo).one()
        self.assertEqual(result["added"], 1)
        self.assertEqual(video.ai_score, 88)
        self.assertEqual(video.ai_decision, "值得看")
        self.assertEqual(video.category, "AI教程")

    def test_feedback_and_stats_are_persisted(self):
        video = BilibiliVideo(bvid="BV1TEST002", uid="123", title="测试视频")
        self.session.add(video)
        self.session.commit()
        bilibili_service.set_feedback(self.session, video.id, "不值得看")
        stats = bilibili_service.get_stats(self.session)
        self.assertEqual(stats["feedback_count"], 1)

    def test_export_video_writes_obsidian_markdown(self):
        video = BilibiliVideo(bvid="BV1TEST003", uid="123", title="测试导出", description="简介", play=123, like=45, comment=6, share=2, ai_score=80, ai_decision="值得看", category="AI教程", popularity_tag="正常")
        self.session.add(video)
        self.session.commit()
        with tempfile.TemporaryDirectory() as tmp, patch.object(config, "OBSIDIAN_NOTES_DIR", Path(tmp)), patch.object(bilibili_service, "_ai_summary", return_value={"summary": "一句话", "core_points": ["要点"], "knowledge_points": ["知识"]}):
            exported, path = bilibili_service.export_video(self.session, video.id)
            self.assertTrue(path.exists())
            text = path.read_text(encoding="utf-8")
            self.assertIn("source: bilibili", text)
            self.assertIn("## 一句话概括", text)
            self.assertTrue(exported.note_exported)


if __name__ == "__main__":
    unittest.main()