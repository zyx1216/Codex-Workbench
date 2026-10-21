# -*- coding: utf-8 -*-
"""v3.2.2 热点页面 DOM 绑定测试。"""

from __future__ import annotations

import unittest
from pathlib import Path


class TrendDomTests(unittest.TestCase):
    def test_trend_ids_exist_in_html_and_js(self):
        html = Path("static/index.html").read_text(encoding="utf-8")
        js = Path("static/app.js").read_text(encoding="utf-8")
        for element_id in (
            "trends-refresh-btn",
            "trends-platform-filter",
            "trends-quality-filter",
            "trends-list",
            "trends-stats",
        ):
            self.assertIn(f'id="{element_id}"', html)
            self.assertIn(f"#{element_id}", js)

    def test_trends_tab_triggers_load_trends(self):
        js = Path("static/app.js").read_text(encoding="utf-8")
        self.assertIn('target === "trends"', js)
        self.assertGreaterEqual(js.count("loadTrends();"), 2)


if __name__ == "__main__":
    unittest.main()
