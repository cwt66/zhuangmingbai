# -*- coding: utf-8 -*-
"""锁住「Web 报告上必须能看到交付门判定」。

工作流梳理发现：`report_gate` 在 `main.py` 里零引用，
D1–D7 只活在 CLI 与测试里。评委在页面上点开的那份报告，
从来没问过自己「能不能交出去」。

接线时还撞出一个更具体的原因：`report_gate.py` 原本写死
`import pipeline as P`，在 `from src import report_gate` 的 Web 场景下
直接 ModuleNotFoundError——**不是设计取舍，是 import 崩了**。

这几条测试锁住两件事：模块在 Web 导入方式下能加载，且报告顶部带判定横幅。
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def self_main():
    import main
    return main


class TestWebGateWiring(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        import main  # noqa: E402
        cls.main = main

    def test_report_gate_imports_in_web_style(self):
        """`from src import report_gate` 必须能加载，不能退回「只有 scripts/ 能跑」。"""
        from src import report_gate
        self.assertTrue(hasattr(report_gate, "check"))
        self.assertEqual(len(report_gate.CHECKS), 7)

    def test_demo_report_has_gate_banner(self):
        """/demo 的每份单据都要有一张交付门横幅。"""
        paths = [(lb, os.path.join(ROOT, "data", "samples", f))
                 for lb, f in self._demo_files()]
        html, _res, quotes = self.main._audit(paths, template_key="old_house")
        self.assertIn("交付门 ·", html)
        self.assertGreaterEqual(html.count("交付门 ·"), len(quotes))
        self.assertIn("D1–D7", html)

    def test_banner_reports_verdict_word(self):
        """横幅必须写出三档里的具体一档，不能只画个颜色。"""
        paths = [(lb, os.path.join(ROOT, "data", "samples", f))
                 for lb, f in self._demo_files()]
        html, _res, _q = self.main._audit(paths, template_key="old_house")
        self.assertTrue(any(w in html for w in ("可交付", "需修正", "禁止交付")))

    def test_banner_escapes_label(self):
        """标签里带 HTML 不能打穿页面（XSS 面）。"""
        path = os.path.join(ROOT, "data", "samples", self._one_sample())
        html, _res, _q = self.main._audit(
            [("<script>alert(1)</script>", path)], template_key="old_house")
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("交付门 ·", html)

    @staticmethod
    def _demo_files():
        """复用 main.py 里 /demo 用的样例清单，别在这边另抄一份。"""
        return list(self_main().SAMPLES)

    @staticmethod
    def _one_sample():
        return next(iter(self_main().SAMPLES))[1]


if __name__ == "__main__":
    unittest.main()
