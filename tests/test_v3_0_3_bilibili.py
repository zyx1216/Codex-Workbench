# -*- coding: utf-8 -*-
"""v3.0.3 旧接口优先与递进冷却测试。"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from services import bilibili_service


class BilibiliV303Tests(unittest.TestCase):
    def setUp(self):
        bilibili_service._rate_limit_streak = 0
        bilibili_service._rate_limited_until = 0.0

    def tearDown(self):
        bilibili_service._rate_limit_streak = 0
        bilibili_service._rate_limited_until = 0.0

    def test_progressive_cooldown(self):
        with patch.object(bilibili_service.time, "monotonic", return_value=100.0):
            self.assertEqual(bilibili_service._set_rate_limited(), 10)
            self.assertEqual(bilibili_service._set_rate_limited(), 900)
            self.assertEqual(bilibili_service._set_rate_limited(), 3600)

    def test_legacy_endpoint_is_tried_first(self):
        payload = {"code": 0, "data": {"list": {"vlist": [{"bvid": "BV1X", "title": "旧接口成功"}]}}}
        with patch.object(bilibili_service, "_get_json", return_value=payload) as legacy, patch.object(bilibili_service, "_get_signed_json") as wbi:
            result = bilibili_service.fetch_creator_videos("2")
        self.assertEqual(result[0]["bvid"], "BV1X")
        legacy.assert_called_once()
        wbi.assert_not_called()


if __name__ == "__main__":
    unittest.main()
