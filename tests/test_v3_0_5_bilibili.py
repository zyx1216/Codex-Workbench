# -*- coding: utf-8 -*-
"""v3.0.5 失效判定与存量补验证测试。"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models.models import Base, BilibiliVideo
from services import bilibili_service


class BilibiliV305Tests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def test_status_zero_is_invalid(self):
        payload = {"code": 0, "data": {"status": 0, "title": "正常标题", "bvid": "BV1"}}
        with patch.object(bilibili_service, "_get_json", return_value=payload):
            result = bilibili_service.fetch_video_detail("BV1", {"is_valid": True})
        self.assertFalse(result["is_valid"])
        self.assertIn("status=0", result["invalid_reason"])

    def test_missing_video_title_is_invalid(self):
        payload = {"code": 0, "data": {"status": 1, "title": "啊叻？视频不见了？", "bvid": "BV2"}}
        with patch.object(bilibili_service, "_get_json", return_value=payload):
            result = bilibili_service.fetch_video_detail("BV2", {"is_valid": True})
        self.assertFalse(result["is_valid"])

    def test_known_delete_code_is_invalid(self):
        with patch.object(bilibili_service, "_get_json", side_effect=bilibili_service.BilibiliError("稿件不可见", code=62002)):
            result = bilibili_service.fetch_video_detail("BV3", {"is_valid": True})
        self.assertFalse(result["is_valid"])
        self.assertIn("62002", result["invalid_reason"])

    def test_backfill_marks_invalid(self):
        video = BilibiliVideo(bvid="BV4", uid="1", title="待验证", is_valid=True)
        self.session.add(video)
        self.session.commit()
        invalid = {"is_valid": False, "invalid_reason": "已删除", "cid": None, "pages": []}
        with patch.object(bilibili_service, "fetch_video_detail", return_value=invalid), patch.object(bilibili_service.time, "sleep"):
            result = bilibili_service.validate_all_existing_videos(self.session)
        self.assertEqual(result["invalidated"], 1)
        self.assertFalse(self.session.get(BilibiliVideo, video.id).is_valid)


if __name__ == "__main__":
    unittest.main()
