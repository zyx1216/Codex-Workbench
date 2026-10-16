# -*- coding: utf-8 -*-
"""v3.0.1 WBI 签名与限流测试。"""

from __future__ import annotations

import re
import time
import unittest
from unittest.mock import Mock, patch

from services import bilibili_service


class WbiTests(unittest.TestCase):
    def setUp(self):
        bilibili_service._wbi_cache = None
        bilibili_service._rate_limited_until = 0.0

    def tearDown(self):
        bilibili_service._wbi_cache = None
        bilibili_service._rate_limited_until = 0.0

    def test_extract_wbi_key_from_url(self):
        self.assertEqual(
            bilibili_service._extract_wbi_key(
                "//i0.hdslb.com/bfs/wbi/7cd08e9b5a084e8e.png"
            ),
            "7cd08e9b5a084e8e",
        )

    def test_nav_unlogged_response_still_provides_keys(self):
        response = Mock()
        response.status_code = 200
        response.url = "https://api.bilibili.com/x/web-interface/nav"
        response.raise_for_status = Mock()
        response.json.return_value = {
            "code": -101,
            "message": "账号未登录",
            "data": {
                "wbi_img": {
                    "img_url": "//i0.hdslb.com/bfs/wbi/imgkey.png",
                    "sub_url": "//i0.hdslb.com/bfs/wbi/subkey.png",
                }
            },
        }
        with patch.object(bilibili_service.httpx, "get", return_value=response), \
             patch.object(bilibili_service.time, "sleep"):
            img_key, sub_key = bilibili_service._get_wbi_keys()
        self.assertEqual(img_key, "imgkey")
        self.assertEqual(sub_key, "subkey")

    def test_sign_params_contains_wts_and_md5_wrid(self):
        with patch.object(bilibili_service, "_get_wbi_keys", return_value=("a" * 64, "b" * 64)), \
             patch.object(bilibili_service.time, "time", return_value=1700000000):
            signed = bilibili_service._sign_wbi_params({"mid": "123", "ps": 10})
        self.assertEqual(signed["wts"], 1700000000)
        self.assertRegex(signed["w_rid"], r"^[0-9a-f]{32}$")

    def test_rate_limit_blocks_followup_request(self):
        bilibili_service._rate_limited_until = time.monotonic() + 3600
        with patch.object(bilibili_service.httpx, "get") as http_get:
            with self.assertRaises(bilibili_service.BilibiliRateLimited):
                bilibili_service._get_json("https://api.bilibili.com/test")
            http_get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
