# -*- coding: utf-8 -*-
"""锁住「规则活性」这件事：既锁结果，也锁测试本身可信。

## 为什么给一个评测脚本写测试

`eval/rule_liveness.py` 的用途是判断「13 条规则是否真的在起作用」。
如果它自己有 bug——比如变异没打上、阳性对照失效还照样输出「通过」——
它就变成了一台专门给死规则盖章的机器，比没有更糟。

所以测试分三组：
1. **测试本身可信**：变异真的改了规则、阳性对照真的触发、阴性对照真的干净；
2. **修过的字段不许退化**：enabled / min_lines / strength(R5) / scope(R5) /
   tolerance_pct(R13) 任何一个重新变成死字段，立刻红；
3. **诚实边界不许被刷**：静态不可达配置必须被报告出来，不许为了让数字好看而藏。
"""
import copy
import importlib.util
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
import pipeline as P  # noqa: E402

SPEC = importlib.util.spec_from_file_location(
    "_rule_liveness", os.path.join(ROOT, "eval", "rule_liveness.py"))
LV = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LV)

DATA = os.path.join(ROOT, "data")


def _env():
    onto = P.Ontology(os.path.join(DATA, "ontology_v0.json"))
    def load(name):
        with open(os.path.join(DATA, name), encoding="utf-8") as f:
            return json.load(f)
    return onto, load("templates_v0.json"), load("rules_v0.json"), load("coverage_v0.json")


class TestLivenessHarness(unittest.TestCase):
    """第一组：这个测试工具自己是不是可信。"""

    def test_mutate_really_changes_the_field(self):
        """变异必须真的落到目标字段上——用错规则 id 会静默「全部存活」。"""
        _onto, _tpl, rules, _cov = _env()
        m = LV.mutate(rules, "R9_qty_unit", "enabled", False)
        got = next(x for x in m["rules"] if x["id"] == "R9_qty_unit")
        self.assertFalse(got["enabled"])
        self.assertTrue(next(x for x in rules["rules"] if x["id"] == "R9_qty_unit")["enabled"],
                        "原始规则被就地改坏了")

    def test_mutate_nested_then_field(self):
        _onto, _tpl, rules, _cov = _env()
        m = LV.mutate(rules, "R1_missing_item", "then.evidence.basis", "【坏】")
        got = next(x for x in m["rules"] if x["id"] == "R1_missing_item")
        self.assertEqual(got["then"]["evidence"]["basis"], "【坏】")

    def test_mutate_unknown_field_returns_none(self):
        _onto, _tpl, rules, _cov = _env()
        self.assertIsNone(LV.mutate(rules, "R1_missing_item", "no_such_field", 1))
        self.assertIsNone(LV.mutate(rules, "no_such_rule", "enabled", False))

    def test_positive_controls_actually_fire(self):
        """为真实语料从未触发的规则造的样本，必须真的能触发它。"""
        onto, tpl, rules, cov = _env()
        with tempfile.TemporaryDirectory(prefix="zmb_lv_test_") as d:
            corpus = LV.build_corpus(d)
            base = LV.run_all(LV.prepare_corpus(corpus, onto), onto, tpl, rules, cov)
        for rid in LV.FIXTURES:
            self.assertGreater(LV.triggered(rid, base), 0,
                               "阳性对照 %s 没触发，活性测试不可信" % rid)
        self.assertGreater(LV.triggered("R5_unit_mix", base), 0)

    def test_clean_control_has_no_error(self):
        onto, tpl, rules, cov = _env()
        with tempfile.TemporaryDirectory(prefix="zmb_lv_test_") as d:
            corpus = LV.build_corpus(d)
            base = LV.run_all(LV.prepare_corpus(corpus, onto), onto, tpl, rules, cov)
        clean = next(x for x in base if x["name"].startswith("阴性对照"))
        self.assertNotIn("error", clean)

    def test_real_only_pool_excludes_positive_controls(self):
        """真实语料口径不能把我们自己造的阳性对照算进去。

        否则「R10 在真实单据上从未触发」会被自己造的样本抹平，
        等于自己给自己发奖状。
        """
        onto, tpl, rules, cov = _env()
        with tempfile.TemporaryDirectory(prefix="zmb_lv_test_") as d:
            corpus = LV.build_corpus(d)
            base = LV.run_all(LV.prepare_corpus(corpus, onto), onto, tpl, rules, cov)
        self.assertEqual(LV.triggered("R10_fee_rate", base, real_only=True), 0)
        self.assertGreater(LV.triggered("R10_fee_rate", base), 0)


class TestDecisiveFieldsAreAlive(unittest.TestCase):
    """第二组：本轮修活的字段不许退化。任何一条重新变死，这里就红。"""

    @classmethod
    def setUpClass(cls):
        cls.onto, cls.tpl, cls.rules, cls.cov = _env()
        with tempfile.TemporaryDirectory(prefix="zmb_lv_test_") as d:
            cls.tmp = tempfile.TemporaryDirectory(prefix="zmb_lv_hold_")
            cls.corpus = LV.build_corpus(cls.tmp.name)
            cls.prepared = LV.prepare_corpus(cls.corpus, cls.onto)
            cls.base = LV.run_all(cls.prepared, cls.onto, cls.tpl, cls.rules, cls.cov)
            cls.base_s = LV.canon(cls.base)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _killed(self, rid, field, value):
        m = LV.mutate(self.rules, rid, field, value)
        self.assertIsNotNone(m, "变异没打上：%s.%s" % (rid, field))
        now = LV.run_all(self.prepared, self.onto, self.tpl, m, self.cov)
        return LV.canon(now) != self.base_s

    def test_enabled_switch_is_live_for_every_rule(self):
        """13 条规则的 enabled 全部必须能被杀。第一次跑的时候 13 个全存活。"""
        dead = []
        for r in self.rules["rules"]:
            if not self._killed(r["id"], "enabled", False):
                dead.append(r["id"])
        self.assertEqual(dead, [], "这些规则的 enabled 又变成死字段了：%s" % dead)

    def test_r3_min_lines_is_live(self):
        self.assertTrue(self._killed("R3_duplicate_same_concept", "min_lines", 99))

    def test_r5_strength_and_scope_are_live(self):
        self.assertTrue(self._killed("R5_unit_mix", "strength", 0.01))
        self.assertTrue(self._killed("R5_unit_mix", "scope", "__broken__"))

    def test_r13_tolerance_pct_is_live(self):
        self.assertTrue(self._killed("R13_doc_recon", "tolerance_pct", 0.9))

    def test_no_decisive_survivors_in_full_report(self):
        """整份报告里判定字段存活必须为 0。"""
        rows = []
        for r in self.rules["rules"]:
            for field, value in LV.MUT_ALL.items():
                if self._killed(r["id"], field, value):
                    continue
                rows.append((r["id"], field))
            for field, value in LV.MUT_PER.items():
                if field not in r or self._killed(r["id"], field, value):
                    continue
                rows.append((r["id"], field))
        self.assertEqual(rows, [], "判定字段存活：%s" % rows)


class TestHonestBoundaries(unittest.TestCase):
    """第三组：不许为了让报告好看，把问题藏起来或改分类。"""

    def test_unreachable_pairs_are_reported(self):
        _onto, tpl, rules, _cov = _env()
        bad = LV.unreachable_pairs(rules, tpl)
        self.assertGreaterEqual(len(bad), 5, "静态不可达配置被漏报了")
        pairs = {x["pair"] for x in bad}
        self.assertIn("tile_floor→grout", pairs)

    def test_reachability_does_not_mutate_rules(self):
        _onto, tpl, rules, _cov = _env()
        before = json.dumps(rules, sort_keys=True, ensure_ascii=False)
        LV.unreachable_pairs(rules, tpl)
        self.assertEqual(before, json.dumps(rules, sort_keys=True, ensure_ascii=False))

    def test_declarative_fields_are_not_reclassified_as_decisive(self):
        """不许把 then.evidence.basis 偷偷挪进「判定」类去刷杀死率。"""
        for f in LV.DECLARATIVE:
            self.assertNotIn(f, LV.DECISIVE)

    def test_report_exit_zero_even_with_survivors(self):
        """存活是候选不是缺陷，默认不该让 CI 红。"""
        rc = LV.main(["--json"])
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
