# -*- coding: utf-8 -*-
"""回归线：这三个数字变了就说明改坏了。

不是"指标好看"的检查，是"别把已经对的东西弄坏"的检查。
任何改动（补别名、改规则、加模板项）跑一遍这个，全绿才算完。
"""
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
import pipeline as P  # noqa: E402

# 冻结的基线。改这里必须说明原因，否则等于把尺子改了去量自己。
BASELINE = {
    "A 公司（规范型）": {"missing_cost": 400, "missing": 1, "duplicate_cut": 0,
                        "effective": 86898},
    "B 公司（低价引流型）": {"missing_cost": 22164, "missing": 17, "duplicate_cut": 0,
                            "effective": 82066},
    "C 公司（高价项全型）": {"missing_cost": 0, "missing": 0, "duplicate_cut": 3168,
                            "effective": 96978},
}
REAL = {
    "sh_zhuoguan": {"rows": 127, "aligned": 111, "auto": 10, "review": 3},
    "huaxibeian": {"rows": 120, "aligned": 105, "auto": 4, "review": 2},
    "template": {"sh_zhuoguan": "old_house", "huaxibeian": "new_house"},
}


def _load(name):
    with open(os.path.join(ROOT, "data", name), encoding="utf-8") as f:
        return json.load(f)


class TestSyntheticBaseline(unittest.TestCase):
    def test_three_samples_unchanged(self):
        onto = P.Ontology(os.path.join(ROOT, "data", "ontology_v0.json"))
        templates, rules, cover = _load("templates_v0.json"), _load("rules_v0.json"), _load(
            "coverage_v0.json")
        files = [("company_a.csv", "A 公司（规范型）"), ("company_b.csv", "B 公司（低价引流型）"),
                 ("company_c.csv", "C 公司（高价项全型）")]
        quotes = [P.build_line_index(
            P.load_quote(os.path.join(ROOT, "data", "samples", fn), label), onto)
            for fn, label in files]
        res = P.audit(quotes, onto, templates, rules, cover)
        for label, exp in BASELINE.items():
            got = res[label]
            with self.subTest(label=label):
                self.assertAlmostEqual(got["missing_cost"], exp["missing_cost"], delta=0.5)
                self.assertEqual(len(got["missing"]), exp["missing"])
                self.assertAlmostEqual(got["duplicate_cut"], exp["duplicate_cut"], delta=0.5)
                self.assertAlmostEqual(got["effective"], exp["effective"], delta=0.5)


class TestRealDocuments(unittest.TestCase):
    def test_alignment_and_missing_counts(self):
        onto = P.Ontology(os.path.join(ROOT, "data", "ontology_v0.json"))
        templates, rules, cover = _load("templates_v0.json"), _load("rules_v0.json"), _load(
            "coverage_v0.json")
        paths = {"sh_zhuoguan": "data/real/sh_zhuoguan_halfpack.csv",
                 "huaxibeian": "data/real/huaxibeian_newhouse.csv"}
        for slug, rel in paths.items():
            q = P.build_line_index(P.load_quote(os.path.join(ROOT, rel), slug), onto)
            key = REAL["template"][slug]
            r = P.audit([q], onto, templates, rules, cover, template_key=key)[q["label"]]
            exp = REAL[slug]
            with self.subTest(doc=slug):
                self.assertEqual(len(q["rows"]), exp["rows"])
                self.assertEqual(sum(1 for x in q["rows"] if x["cid"]), exp["aligned"])
                self.assertEqual(len([m for m in r["missing"] if m["tier"] == "auto"]),
                                 exp["auto"])
                self.assertEqual(len([m for m in r["missing"] if m["tier"] != "auto"]),
                                 exp["review"])


class TestInvariants(unittest.TestCase):
    def test_every_conclusion_has_trace(self):
        """可溯源率 100%：每条漏项必须带来 basis/formula，且能指到行或词表。"""
        onto = P.Ontology(os.path.join(ROOT, "data", "ontology_v0.json"))
        templates, rules, cover = _load("templates_v0.json"), _load("rules_v0.json"), _load(
            "coverage_v0.json")
        q = P.build_line_index(
            P.load_quote(os.path.join(ROOT, "data/real/sh_zhuoguan_halfpack.csv"), "x"), onto)
        r = P.audit([q], onto, templates, rules, cover, template_key="old_house")[q["label"]]
        self.assertTrue(r["missing"])
        for m in r["missing"]:
            ev = m["evidence"]
            self.assertTrue(ev.get("basis"), m["concept"])
            self.assertTrue(ev.get("formula"), m["concept"])
            self.assertIn("checked_words", ev, m["concept"])

    def test_no_low_confidence_enters_money(self):
        """低分结论一律不进核减金额——这是项目的伦理底线，不是参数。"""
        onto = P.Ontology(os.path.join(ROOT, "data", "ontology_v0.json"))
        templates, rules, cover = _load("templates_v0.json"), _load("rules_v0.json"), _load(
            "coverage_v0.json")
        q = P.build_line_index(
            P.load_quote(os.path.join(ROOT, "data/real/huaxibeian_newhouse.csv"), "x"), onto)
        r = P.audit([q], onto, templates, rules, cover, template_key="new_house")[q["label"]]
        for m in r["missing"]:
            if m["tier"] == "auto":
                self.assertGreaterEqual(m["confidence"], 0.90,
                                        "%s 被判确定却低于阈值" % m["concept"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
