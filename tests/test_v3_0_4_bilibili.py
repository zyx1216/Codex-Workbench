# -*- coding: utf-8 -*-
"""v3.0.4 失效过滤与三级笔记测试。"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models.models import Base, BilibiliVideo
from services import bilibili_service


class BilibiliV304Tests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def test_invalid_video_hidden_by_default(self):
        self.session.add(BilibiliVideo(bvid="BVINVALID", uid="1", title="失效", is_valid=False, invalid_reason="status=0"))
        self.session.commit()
        self.assertEqual(bilibili_service.list_videos(self.session), [])
        self.assertEqual(len(bilibili_service.list_videos(self.session, include_invalid=True)), 1)

    def test_prepare_note_prefers_subtitle(self):
        video = BilibiliVideo(bvid="BV1", uid="1", title="标题", cid=2)
        with patch.object(bilibili_service, "fetch_video_subtitle", return_value="完整字幕"), patch.object(
            bilibili_service, "_ai_note_from_context", return_value={"summary": "摘要", "core_points": [], "knowledge_points": [], "quotes": [], "actions": []}
        ):
            note, source = bilibili_service._prepare_note(video, "UP")
        self.assertEqual(source, "subtitle")
        self.assertEqual(note["summary"], "摘要")

    def test_prepare_note_uses_part_list(self):
        video = BilibiliVideo(bvid="BV2", uid="1", title="分P视频", cid=3)
        detail = {"cid": 3, "pages": [{"part": "上集", "desc": "简介1"}]}
        with patch.object(bilibili_service, "fetch_video_subtitle", return_value=None), patch.object(
            bilibili_service, "fetch_video_detail", return_value=detail
        ), patch.object(
            bilibili_service, "_ai_note_from_context", return_value={"summary": "章节摘要", "core_points": [], "knowledge_points": [], "quotes": [], "actions": []}
        ):
            _, source = bilibili_service._prepare_note(video, "UP")
        self.assertEqual(source, "part_list")


if __name__ == "__main__":
    unittest.main()
