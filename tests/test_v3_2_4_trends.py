# -*- coding: utf-8 -*-
"""v3.2.4 热点列表渲染契约测试。"""

from __future__ import annotations

import unittest
from pathlib import Path


class TrendRenderTests(unittest.TestCase):
    def test_trend_render_uses_envelope_data_and_fields(self):
        js = Path("static/app.js").read_text(encoding="utf-8")
        self.assertIn("function extractTrendItems(response)", js)
        self.assertIn("Array.isArray(response?.data)", js)
        self.assertIn("item.platform_name", js)
        self.assertIn("item.ai_score", js)
        self.assertIn("item.ai_reason", js)
        self.assertIn("item.user_feedback", js)
        self.assertIn("暂无热点，点刷新试试", js)

    def test_static_assets_are_versioned(self):
        html = Path("static/index.html").read_text(encoding="utf-8")
        self.assertIn("/static/app.js?v=3.2.4", html)
        self.assertIn("/static/style.css?v=3.2.4", html)


if __name__ == "__main__":
    unittest.main()
