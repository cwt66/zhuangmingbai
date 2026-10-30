# -*- coding: utf-8 -*-
"""感知层单测：表头映射、算术自校验、失败要失败得明显。

跑法：python3 -m unittest discover -s tests -v
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
import perception as P  # noqa: E402


class TestHeaderMapping(unittest.TestCase):
    def test_real_header_variants(self):
        # 花溪北岸的表头：序号/项目名称/规格工艺/单位/数量/单价/合价/备注
        cells = {"A": "序号", "B": "项目名称", "C": "规格工艺", "D": "单位",
                 "E": "数量", "F": "单价", "G": "合价", "H": "备注"}
        m = P.map_header(cells)
        self.assertEqual(m["项目名称"], "B")
        self.assertEqual(m["合价"], "G")

    def test_units_in_parens(self):
        # 真实单据常写「单价(元)」「合价（元）」
        cells = {"A": "施工项目", "B": "工程量", "C": "单价(元)", "D": "合价（元）"}
        m = P.map_header(cells)
        self.assertEqual(m["项目名称"], "A")
        self.assertEqual(m["数量"], "B")
        self.assertEqual(m["单价"], "C")
        self.assertEqual(m["合价"], "D")

    def test_unknown_header_not_guessed(self):
        m = P.map_header({"A": "随便什么", "B": "另一列"})
        self.assertEqual(m, {})


class TestRowsToQuote(unittest.TestCase):
    def setUp(self):
        self.raw = [
            {"A": "序号", "B": "项目名称", "C": "单位", "D": "数量", "E": "单价",
             "F": "合价", "G": "备注"},
            {"A": "1", "B": "粘贴地砖", "C": "m2", "D": "31", "E": "140.4",
             "F": "4352.4", "G": ""},
            {"A": "2", "B": "滚涂乳胶漆", "C": "m2", "D": "110", "E": "15.6",
             "F": "1716", "G": "面漆两遍"},
        ]

    def test_parses_two_rows_and_total(self):
        rows, rep = P.rows_to_quote(self.raw)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["项目名称"], "粘贴地砖")
        self.assertAlmostEqual(rep["total"], 4352.4 + 1716, places=2)
        self.assertEqual(rep["arith_bad"], [])

    def test_flags_arithmetic_mismatch(self):
        raw = [dict(r) for r in self.raw]
        raw[1]["F"] = "9999"          # 故意写错的合价
        rows, rep = P.rows_to_quote(raw)
        self.assertEqual(len(rep["arith_bad"]), 1)
        self.assertEqual(rep["arith_bad"][0]["name"], "粘贴地砖")

    def test_missing_header_raises_not_silent(self):
        # 关键：识别不出表头必须抛异常，不能返回空表让上层当成"没漏项"
        with self.assertRaises(P.PerceptionError):
            P.rows_to_quote([{"A": "x", "B": "y"}])

    def test_number_parsing_with_separators(self):
        self.assertEqual(P._num("1,234.50 元"), 1234.5)
        self.assertIsNone(P._num(""))


class TestProviders(unittest.TestCase):
    def test_mock_is_marked_as_fixture(self):
        rows, meta, rep = P.perceive("__selftest__", provider="mock")
        self.assertEqual(len(rows), 2)
        self.assertTrue(meta.get("fixture"))
        self.assertIn("warning", meta)

    def test_unknown_provider_raises(self):
        with self.assertRaises(P.PerceptionError):
            P.perceive("__selftest__", provider="gpt-imaginary")

    def test_ocr_without_credentials_gives_actionable_error(self):
        # 不联网也要给出可执行提示，而不是 KeyError
        env = {k: os.environ.pop(k, None) for k in
               ("HW_ACCESS_KEY", "HW_SECRET_KEY", "HW_PROJECT_ID")}
        try:
            with self.assertRaises(P.PerceptionError) as ctx:
                P.perceive("__selftest__", provider="ocr_table")
            msg = str(ctx.exception)
            self.assertTrue(any(k in msg for k in ("HW_ACCESS_KEY", "project_id", "开通")),
                            msg)
        finally:
            for k, v in env.items():
                if v is not None:
                    os.environ[k] = v


if __name__ == "__main__":
    unittest.main(verbosity=2)
