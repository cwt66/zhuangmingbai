# -*- coding: utf-8 -*-
"""锁住「提交包数字一致性」判据本身，尤其是别再误报。

第一次跑的时候它把 `11/15`（日期）和 `第 5、15 步`（步骤编号）都当成
「自检 15 步」的旧值，还把讲「以前写着 243 个测试」的诚实叙述判成漂移。
误报率决定一套判据能活多久——这两类回归必须锁住。
"""
import importlib.util
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC = importlib.util.spec_from_file_location(
    "_verify_package_numbers", os.path.join(ROOT, "scripts", "verify_package_numbers.py"))
PN = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PN)


def _step_pattern():
    return next(r[2] for r in PN.STALE_RULES if r[0] == "steps")


class PackageNumbersTests(unittest.TestCase):
    def test_self_test_is_bidirectional(self):
        st = PN.self_test()
        self.assertEqual(st["status"], "PASS",
                         [c for c in st["cases"] if not c["ok"]])
        self.assertTrue(all(c["ok"] for c in st["cases"]))
        self.assertEqual(len(st["cases"]), 7)
        self.assertEqual(st["cases"][0]["expected"], "PASS")
        for c in st["cases"]:
            if c["name"].startswith("发布快照落在"):
                self.assertEqual(c["expected"], "PASS")
            elif c is not st["cases"][0]:
                self.assertEqual(c["expected"], "FAIL")

    def test_step_regex_does_not_match_date(self):
        self.assertIsNone(re.search(_step_pattern(), "账号持有人 | 11/15 |"))

    def test_step_regex_does_not_match_step_reference(self):
        self.assertIsNone(re.search(_step_pattern(),
                                    "是 `check_all.sh` 第 5、15 步生成的产物"))

    def test_step_regex_still_matches_real_stale_value(self):
        self.assertIsNotNone(re.search(_step_pattern(), "「15 步自检」"))
        self.assertIsNotNone(re.search(_step_pattern(), "一键自检（15 步）"))

    def test_history_narrative_is_exempted_not_deleted(self):
        # 讲「以前写着什么」的句子要被豁免，而不是逼人删掉诚实记录
        pat = next(r[2] for r in PN.STALE_RULES if r[0] == "tests")
        line = "一份材料里写着「243 个测试」「15 步自检」"
        self.assertIsNotNone(re.search(pat, line))
        self.assertTrue(any(m in line for m in PN.NARRATIVE_MARKERS))

    @unittest.skipUnless(PN.DEFAULT_PACKAGE.is_dir(), "提交包在仓库外，CI 上没有")
    def test_real_package_has_no_drift(self):
        result = PN.scan(PN.DEFAULT_PACKAGE)
        self.assertEqual(result["violations"], [])
        self.assertGreater(result["stats"]["exempted_lines"], 0)


if __name__ == "__main__":
    unittest.main()
