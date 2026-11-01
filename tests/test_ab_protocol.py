# -*- coding: utf-8 -*-
"""对照实验口径的锁。

这些测试守的是「度量不要骗自己」：
- 漏项类结论没有行号，不能被判成不可溯源（v1.1 修正）；
- partial 必须按 no 计（防自评放水）；
- 我们这侧的行必须真的带基准与公式，不能空着也记 yes；
- OCR 闭环脚本必须能跑出对照结论。
"""
import contextlib
import csv
import importlib.util
import io
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _rows(*specs):
    return [dict(zip(("system", "doc", "item_id", "conclusion", "has_line",
                      "has_basis", "has_formula", "grounded", "note"), s)) for s in specs]


class TestTallySemantics(unittest.TestCase):
    def _tally(self, rows):
        mod = _load(os.path.join(ROOT, "eval", "ab_tally.py"), "ab_tally")
        tmp = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False,
                                          encoding="utf-8-sig", newline="")
        with tmp as f:
            w = csv.DictWriter(f, fieldnames=["system", "doc", "item_id", "conclusion",
                                              "has_line", "has_basis", "has_formula",
                                              "grounded", "note"])
            w.writeheader()
            w.writerows(rows)
        out = tmp.name + ".md"
        old = sys.argv
        try:
            sys.argv = ["ab_tally.py", "--csv", tmp.name, "--out", out]
            with contextlib.redirect_stdout(io.StringIO()):
                mod.main()
        finally:
            sys.argv = old
            os.unlink(tmp.name)
        with open(out, encoding="utf-8") as f:
            text = f.read()
        os.unlink(out)
        return text

    def test_missing_item_with_na_line_counts_as_traceable(self):
        """漏项无行号（n/a）＋有基准＋有公式 → 应算可溯源。"""
        text = self._tally(_rows(
            ("zhuangmingbai", "d", "1", "漏项：闭水试验", "n/a", "yes", "yes", "yes", "")))
        self.assertIn("100%", text)

    def test_partial_still_counts_as_no(self):
        text = self._tally(_rows(
            ("renopit", "d", "1", "某结论", "yes", "partial", "yes", "yes", "")))
        self.assertIn("0%", text)

    def test_hallucination_counted(self):
        text = self._tally(_rows(
            ("renopit", "d", "1", "某结论", "yes", "yes", "yes", "no", "")))
        self.assertIn("| M4 幻觉条目数（回原文不成立） | 1 | 0 |", text)


class TestOurRows(unittest.TestCase):
    def test_every_row_has_basis_and_formula(self):
        mod = _load(os.path.join(ROOT, "eval", "fill_our_rows.py"), "fill_our_rows")
        old = sys.argv
        try:
            sys.argv = ["fill_our_rows.py"]
            with contextlib.redirect_stdout(io.StringIO()):
                mod.main()
        finally:
            sys.argv = old
        path = os.path.join(ROOT, "eval", "ab_runs", "zhuangmingbai_rows.csv")
        self.assertTrue(os.path.isfile(path))
        with open(path, encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        self.assertGreater(len(rows), 10)
        for r in rows:
            self.assertEqual(r["system"], "zhuangmingbai")
            self.assertEqual(r["has_basis"], "yes", r["conclusion"])
            self.assertEqual(r["has_formula"], "yes", r["conclusion"])
            self.assertIn(r["has_line"], ("yes", "n/a"))

    def test_missing_rows_use_na_not_fake_line(self):
        path = os.path.join(ROOT, "eval", "ab_runs", "zhuangmingbai_rows.csv")
        with open(path, encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        miss = [r for r in rows if r["conclusion"].startswith("漏项")]
        self.assertTrue(miss)
        for r in miss:
            self.assertEqual(r["has_line"], "n/a")


class TestOcrLoop(unittest.TestCase):
    def test_demo_runs_and_reports_delta(self):
        mod = _load(os.path.join(ROOT, "eval", "ocr_loop_demo.py"), "ocr_loop")
        noise = mod.ocr_noise_stats(os.path.join(ROOT, "data", "real", "feitai_budget_ocr.csv"))
        self.assertGreater(noise["rows"], 100)
        self.assertGreaterEqual(noise["arith_ok_ratio"], 50)
        q1, r1 = mod.audit_of(os.path.join(ROOT, "data", "real", "feitai_budget_ocr.csv"), "ocr")
        q2, r2 = mod.audit_of(os.path.join(ROOT, "data", "real", "feitai_budget.csv"), "fix")
        # 校对后对齐率必须显著高于原始 OCR——这是整条闭环的核心事实
        al1 = sum(1 for x in q1["rows"] if x["cid"]) / len(q1["rows"])
        al2 = sum(1 for x in q2["rows"] if x["cid"]) / len(q2["rows"])
        self.assertGreater(al2, al1 + 0.2, "校对后对齐率应提高 20pp 以上")


if __name__ == "__main__":
    unittest.main()
