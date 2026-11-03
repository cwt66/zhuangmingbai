# -*- coding: utf-8 -*-
"""XLSX 导出的锁：零依赖、可被 Excel 打开、金额是数值。"""
import os
import sys
import tempfile
import unittest
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
import xlsx  # noqa: E402


class TestXlsx(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="zmb_xlsx_")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_write_minimal_workbook(self):
        path = os.path.join(self.tmp, "t.xlsx")
        xlsx.write_xlsx(path, [
            ("总览", [["公司", "金额"], ["A", 1234.5]], [20, 12]),
            ("说明", [["x"], ["y"]], [10]),
        ])
        self.assertGreater(os.path.getsize(path), 500)
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            self.assertIn("xl/workbook.xml", names)
            self.assertIn("xl/styles.xml", names)
            self.assertIn("xl/worksheets/sheet1.xml", names)
            wb = z.read("xl/workbook.xml").decode()
            self.assertIn("总览", wb)
            self.assertIn("说明", wb)
            sheet = z.read("xl/worksheets/sheet1.xml").decode()
            self.assertIn("1234.5", sheet)       # 数值单元格，不是文本
            self.assertIn("inlineStr", sheet)    # 中文用 inlineStr

    def test_build_audit_sheets(self):
        quotes = [{"label": "A 公司", "total": 1000, "file": "a.csv",
                   "recon": {"detail_sum": 1000, "fee_sum": 0,
                             "grand": {"amount": 1000}, "diff": 0, "ok": True}}]
        res = {"A 公司": {
            "missing": [{"concept": "闭水试验", "ref_amount": 100, "tier": "auto",
                         "evidence": {"basis": "模板", "formula": "T-S"}}],
            "duplicates": [], "risks": [],
            "missing_cost": 100, "duplicate_cut": 0, "effective": 1100,
            "exposure": [{"low": 0, "high": 100}],
            "quote": quotes[0],
        }}
        sheets = xlsx.build_audit_sheets(quotes, res, meta={"time": "t", "fingerprint": "fp"})
        self.assertEqual([s[0] for s in sheets],
                         ["总览", "漏项", "重复计价", "风险", "对账", "说明"])
        path = os.path.join(self.tmp, "audit.xlsx")
        xlsx.write_xlsx(path, sheets)
        with zipfile.ZipFile(path) as z:
            wb = z.read("xl/workbook.xml").decode()
            for name in ["总览", "漏项", "重复计价", "风险", "对账", "说明"]:
                self.assertIn(name, wb)


if __name__ == "__main__":
    unittest.main()
