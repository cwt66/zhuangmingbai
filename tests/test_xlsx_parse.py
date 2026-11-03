# -*- coding: utf-8 -*-
"""多 sheet 的锁：主表在第二个 sheet 时也能读到。"""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
import perception  # noqa: E402
import xlsx  # noqa: E402


class TestXlsxMultiSheet(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="zmb_xlsx_parse_")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_picks_largest_sheet(self):
        path = os.path.join(self.tmp, "multi.xlsx")
        xlsx.write_xlsx(path, [
            ("说明", [["说明"], ["这是封面"]], [20]),
            ("报价", [["项目名称", "单位", "数量", "单价", "合价"],
                      ["拆墙", "㎡", 10, 50, 500],
                      ["水电", "项", 1, 8000, 8000],
                      ["油漆", "㎡", 80, 35, 2800]], [20, 10, 10, 10, 10]),
        ])
        with open(path, "rb") as f:
            rows = perception._xlsx_to_rows(f.read())
        self.assertGreaterEqual(len(rows), 4)
        self.assertEqual(rows[0].get("A"), "项目名称")
        self.assertEqual(rows[1].get("A"), "拆墙")

    def test_single_sheet(self):
        path = os.path.join(self.tmp, "one.xlsx")
        xlsx.write_xlsx(path, [("Sheet1", [["A", "B"], [1, 2]], [10, 10])])
        with open(path, "rb") as f:
            rows = perception._xlsx_to_rows(f.read())
        self.assertEqual(rows[0], {"A": "A", "B": "B"})


if __name__ == "__main__":
    unittest.main()
