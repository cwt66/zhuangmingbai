# -*- coding: utf-8 -*-
"""展示面一致性判据的门禁测试：判据不许退化成橡皮图章。"""

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "verify_showcase", ROOT / "scripts" / "verify_showcase.py")
VS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VS)


class TestSelfTest(unittest.TestCase):
    def test_passes(self):
        st = VS.self_test()
        self.assertEqual(st["status"], "PASS", st["cases"])
        self.assertTrue(all(c["ok"] for c in st["cases"]))

    def test_seven_cases(self):
        st = VS.self_test()
        self.assertEqual(len(st["cases"]), 7)  # 1 正向 + 6 故障
        self.assertEqual(st["cases"][0]["expected"], "PASS")
        self.assertTrue(all(c["expected"] == "FAIL" for c in st["cases"][1:]))


class TestScan(unittest.TestCase):
    def setUp(self):
        self.claims = VS.load_claims()
        self.v = VS.current_values(self.claims)
        self.good = VS.minimal_html(self.v)

    def test_clean_page_passes(self):
        rep = VS.scan(self.good, self.claims)
        self.assertEqual(rep["status"], "PASS", rep["violations"])

    def test_stale_commit_hash_fails(self):
        rep = VS.scan(self.good + " 在线 main `c56b706`", self.claims)
        self.assertEqual(rep["status"], "FAIL")

    def test_removing_honesty_boundary_fails(self):
        rep = VS.scan(self.good.replace("未实测", ""), self.claims)
        self.assertEqual(rep["status"], "FAIL")

    def test_stale_rules_never_include_current_step(self):
        """回归：第一版把当前步数也列进旧值正则，干净页被自己判红。"""
        rep = VS.scan(self.good, self.claims)
        self.assertFalse(any("旧步数" in x for x in rep["violations"]))
        rep2 = VS.scan(self.good + " %s 步自检" % self.v["steps"], self.claims)
        self.assertFalse(any("旧步数" in x for x in rep2["violations"]))

    def test_page_number_mismatch_fails(self):
        rep = VS.scan(self.good.replace("02 / 02", "02 / 03"), self.claims)
        self.assertEqual(rep["status"], "FAIL")


class TestWiring(unittest.TestCase):
    def test_check_all_has_step_19(self):
        sh = (ROOT / "scripts" / "check_all.sh").read_text(encoding="utf-8")
        self.assertIn("19/20 评委复验页一致性", sh)
        self.assertIn("verify_showcase.py", sh)

    def test_ci_uploads_report(self):
        ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("out/showcase_consistency.md", ci)

    def test_real_showcase_passes(self):
        text = VS.SHOWCASE.read_text(encoding="utf-8")
        rep = VS.scan(text, VS.load_claims())
        self.assertEqual(rep["status"], "PASS", rep["violations"])
        self.assertEqual(rep["pages"], 11)


if __name__ == "__main__":
    unittest.main()
