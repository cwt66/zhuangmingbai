# -*- coding: utf-8 -*-
"""汇总行分层 / 文档级对账 / 参照价库 / 费类规则 —— 2026-11-01 新增能力的锁。

这些测试存在的理由：这四件事都是「修一个 bug 顺手长出来的能力」，
最容易在后续改动里悄悄失效（尤其是 R10 那种「数据分层后规则静默不触发」）。
"""
import csv
import json
import os
import sys
import tempfile
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


class TestDiscountAndSigns(unittest.TestCase):
    """优惠（减项）与负号变体 —— 2026-11-07 补的洞。

    两个缺陷都是在「造一张带优惠的报价单」时撞出来的，现有 5 份真实样本
    一条负金额都没有，所以过去的评测完全覆盖不到：

    1. `num()` 只认 ASCII "-"：全角「－」、数学减号「−」、破折号「—」全部
       `float()` 失败 → 静默变 0。报告上看不出任何异常，优惠被吞掉。
    2. `PAID_KEYWORDS` 里有「优惠」，但对账公式只减「取费」，从没减过「减项」。
       于是「明细 4,000 + 优惠 -3,000 + 合计 1,000」被判「整单加总对不上」——
       账其实是对的，这是假阳性。
    """

    BASE = ("项目名称,规格工艺,单位,数量,单价,合价\n"
            "拆除墙体,砖混,m2,10,50,500\n"
            "防水,聚氨酯,m2,20,80,1600\n"
            "墙面粉刷,立邦,m2,50,38,1900\n")

    def _quote(self, tail):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "q.csv")
        with open(p, "w", encoding="utf-8-sig") as f:
            f.write(self.BASE + tail)
        return P.load_quote(p, "t")

    def test_all_negative_sign_variants_parse(self):
        """六种负号都要解析成负数。少一种，那份单据的优惠就白写了。"""
        for raw in ("-3000", "\uff0d3000", "\u22123000", "\u20143000",
                    "\u20133000", "\ufe633000"):
            q = self._quote("全屋优惠,,项,1,%s,%s\n" % (raw, raw))
            rows = q["rows"] + (q.get("summary_rows") or [])
            target = [r for r in rows if "优惠" in r["name"]]
            self.assertEqual(len(target), 1, raw)
            self.assertAlmostEqual(target[0]["amount"], -3000.0, places=2,
                                   msg="负号变体未被识别：%r" % raw)

    def test_negative_discount_reconciles(self):
        """优惠写成负数：对账必须闭合，不许报「整单对不上」。"""
        q = self._quote("全屋优惠,,项,1,\uff0d3000,\uff0d3000\n合计,,,,,1000\n")
        rc = q["recon"]
        self.assertAlmostEqual(rc["discount_sum"], -3000.0, places=2)
        self.assertAlmostEqual(rc["detail_sum"], 4000.0, places=2)
        self.assertAlmostEqual(rc["diff"], 0.0, places=2)
        self.assertTrue(rc["ok"], "带优惠的单据被误判为对不上账：diff=%s" % rc["diff"])

    def test_positive_discount_is_flagged_not_consumed(self):
        """优惠写成正数：语义无法从单行判断，只标记、不猜。"""
        q = self._quote("全屋优惠,,项,1,3000,3000\n合计,,,,,1000\n")
        rc = q["recon"]
        self.assertEqual(rc["discount_sum"], 0.0, "正数优惠不该被当成减项算进公式")
        self.assertTrue(rc["discount_ambiguous"], "正数优惠必须被标出来待人工核对")
        self.assertFalse(rc["ok"], "此时对账确实对不上，应当报出来")

    def test_discount_does_not_leak_into_received(self):
        """「优惠」不能冒充「实收」——实收是结果，减项是算式里的一项。"""
        q = self._quote("全屋优惠,,项,1,-3000,-3000\n实收,,,,,1000\n")
        rc = q["recon"]
        self.assertIsNotNone(rc["received"])
        self.assertEqual(rc["received"]["amount"], 1000.0)

    def test_no_discount_unchanged(self):
        """回归：没有优惠行的单据，行为必须和以前一模一样。"""
        q = self._quote("合计,,,,,4000\n")
        rc = q["recon"]
        self.assertEqual(rc["discount_sum"], 0.0)
        self.assertAlmostEqual(rc["diff"], 0.0, places=2)
        self.assertTrue(rc["ok"])


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
