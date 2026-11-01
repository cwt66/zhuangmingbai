# -*- coding: utf-8 -*-
"""汇总行分层 / 文档级对账 / 参照价库 / 费类规则 —— 2026-11-01 新增能力的锁。

这些测试存在的理由：这四件事都是「修一个 bug 顺手长出来的能力」，
最容易在后续改动里悄悄失效（尤其是 R10 那种「数据分层后规则静默不触发」）。
"""
import csv
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
import pipeline as P  # noqa: E402

DATA = os.path.join(ROOT, "data")
REAL = os.path.join(DATA, "real")


def _load(name):
    with open(os.path.join(DATA, name), encoding="utf-8") as f:
        return json.load(f)


def _env():
    return (P.Ontology(os.path.join(DATA, "ontology_v0.json")),
            _load("templates_v0.json"), _load("rules_v0.json"), _load("coverage_v0.json"))


class TestSummarySplit(unittest.TestCase):
    """汇总行必须从工序明细里分出去，但不能误删名字里带「小计」的真工序。"""

    def test_feitai_splits_8_summary_rows(self):
        q = P.load_quote(os.path.join(REAL, "feitai_budget.csv"), "翡翠台")
        self.assertEqual(len(q["summary_rows"]), 8)
        self.assertEqual(len(q["rows"]), 123)
        names = {r["name"] for r in q["summary_rows"]}
        for expect in ("装修造价", "工程总造价", "装饰主材小计", "合计"):
            self.assertIn(expect, names)

    def test_clinic_keeps_rows_named_xiaoji(self):
        """诊所 PDF 有 19 行「批腻子…小计」是真工序（数量×单价=合价 精确）。"""
        q = P.load_quote(os.path.join(REAL, "szxinxin_clinic_pdf.csv"), "诊所")
        self.assertEqual(len(q["summary_rows"]), 0)
        self.assertEqual(len(q["rows"]), 168)
        self.assertTrue(any("小计" in r["name"] for r in q["rows"]))

    def test_total_excludes_summary(self):
        """修复前翡翠台单份报价算出 743,920.30（汇总行被加了两遍）。"""
        q = P.load_quote(os.path.join(REAL, "feitai_budget.csv"), "翡翠台")
        self.assertAlmostEqual(q["total"], 153384.00, places=2)


class TestReconcile(unittest.TestCase):
    """对账层：印刷合计 vs 明细之和，差额本身是结论。"""

    def test_124_detects_506(self):
        q = P.load_quote(os.path.join(REAL, "guijiao_124_basic.csv"), "124㎡")
        rec = q["recon"]
        self.assertAlmostEqual(rec["detail_sum"], 75762.56, places=2)
        self.assertAlmostEqual(rec["grant"] if "grant" in rec else rec["grand"]["amount"],
                               76269.16, places=2)
        self.assertAlmostEqual(rec["diff"], 506.60, places=2)
        self.assertFalse(rec["ok"])

    def test_no_printed_total_is_skip_not_pass(self):
        """没有印刷合计时 ok 必须是 None（跳过），不能写成 True（对平）。"""
        q = P.load_quote(os.path.join(REAL, "sh_zhuoguan_halfpack.csv"), "篱笆")
        self.assertIsNone(q["recon"]["grand"])
        self.assertIsNone(q["recon"]["ok"])

    def test_recon_becomes_risk(self):
        onto, tpl, rules, cov = _env()
        q = P.build_line_index(P.load_quote(os.path.join(REAL, "guijiao_124_basic.csv"), "124㎡"), onto)
        r = P.audit([q], onto, tpl, rules, cov, template_key="new_house")[q["label"]]
        recon_risks = [x for x in r["risks"] if x["rule"] == "R13_doc_recon"]
        self.assertEqual(len(recon_risks), 1)
        self.assertIn("507", recon_risks[0]["issue"])  # 506.60 → fmt_money 四舍五入为 507


class TestFeeRuleAlive(unittest.TestCase):
    """R10 在数据分层后必须仍然有效——失效的规则比没有规则更危险。"""

    def _quote_with_fee(self, fee_rate):
        rows = [{"no": "1", "name": "地面贴砖", "spec": "分区：客厅", "unit": "m2",
                 "qty": 100, "price": 100, "amount": 10000, "note": ""}]
        total = 10000.0
        fee = round(total * fee_rate, 2)
        summary = [{"no": "2", "name": "工程管理费", "spec": "", "unit": "项",
                    "qty": fee_rate, "price": total, "amount": fee, "note": ""}]
        return {"label": "合成单", "file": "synthetic.csv", "rows": rows,
                "summary_rows": summary, "total": total,
                "recon": P.reconcile(rows, summary)}

    def test_fires_above_threshold(self):
        rule = {x["id"]: x for x in _load("rules_v0.json")["rules"]}["R10_fee_rate"]
        out = P.find_fee_rate(self._quote_with_fee(0.15), rule)
        self.assertEqual(len(out), 1)
        self.assertIn("15.0%", out[0]["issue"])

    def test_silent_below_threshold(self):
        rule = {x["id"]: x for x in _load("rules_v0.json")["rules"]}["R10_fee_rate"]
        self.assertEqual(P.find_fee_rate(self._quote_with_fee(0.05), rule), [])


class TestReferenceBook(unittest.TestCase):
    """单份上传没有同批参照时，退到参照价库；没有样本的必须留空，不编数。"""

    def test_single_doc_gets_money(self):
        onto, tpl, rules, cov = _env()
        q = P.build_line_index(P.load_quote(os.path.join(REAL, "guijiao_124_basic.csv"), "124㎡"), onto)
        r = P.audit([q], onto, tpl, rules, cov, template_key="new_house")[q["label"]]
        self.assertGreater(r["missing_cost"], 0)
        self.assertTrue(any(m.get("ref_basis") == "corpus" for m in r["missing"]))

    def test_process_items_stay_empty(self):
        """闭水试验这类工艺项在 4 份单据里从未单列计价，必须留空。"""
        onto, tpl, rules, cov = _env()
        q = P.build_line_index(P.load_quote(os.path.join(REAL, "feitai_budget.csv"), "翡翠台"), onto)
        r = P.audit([q], onto, tpl, rules, cov, template_key="old_house")[q["label"]]
        empty = [m for m in r["missing"] if not m["ref_amount"]]
        self.assertTrue(empty, "应当存在无参照的工艺项")
        self.assertTrue(all(m["ref_basis"] is None for m in empty))

    def test_batch_reference_wins_over_book(self):
        """有同批参照时优先用同批（更可比），不从库里取。"""
        onto, tpl, rules, cov = _env()
        paths = [("A", os.path.join(DATA, "samples", "company_a.csv")),
                 ("B", os.path.join(DATA, "samples", "company_b.csv"))]
        quotes = [P.build_line_index(P.load_quote(p, lb), onto) for lb, p in paths]
        res = P.audit(quotes, onto, tpl, rules, cov)
        for q in quotes:
            for m in res[q["label"]]["missing"]:
                if m["ref_amount"]:
                    self.assertEqual(m["ref_basis"], "batch")


if __name__ == "__main__":
    unittest.main()
