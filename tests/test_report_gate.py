# -*- coding: utf-8 -*-
"""交付门的锁：证明它真的会拦，而不只是会放行。

## 为什么这些测试比「5 份真实单据都 pass」重要

一个永远返回 pass 的检查函数，绩效和不存在一样。
5 份真实单据全过只能说明「我们的报告本来就没病」，
不能说明「有病的时候它会被拦下来」。

所以这里做的是**故障注入**：拿一份真的、健康的审计结果，
每次只破坏一处，看门能不能认出来。六种破坏对应六条检查：

| 破坏 | 应当被谁抓住 | 期望档位 |
|---|---|---|
| 删掉 R13 但对账差异还在 | D3 | block（账不对还不说） |
| 把 review 档金额并进核减 | D5 | block（低分进钱了） |
| 给漏项编一个参照金额、不给来源 | D4 | block（钱是编的） |
| 报告指纹与基准不一致 | D6 | block（不可复现） |
| 结论缺计算公式 | D2 | needs_fix（能看懂，缺依据） |
| 结论既无行号也无基准 | D1 | needs_fix（说了但指不到） |

**block 与 needs_fix 的分界是有意的**：钱的问题一律 block，
表述的问题一律 needs_fix。这条线如果松动，
要么业主永远拿不到报告（全都 block），要么报告带着假钱出门（全都 pass）。
"""
import copy
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
import pipeline as P  # noqa: E402
import report_gate as G  # noqa: E402

DATA = os.path.join(ROOT, "data")
REAL = os.path.join(DATA, "real")


def _load(name):
    with open(os.path.join(DATA, name), encoding="utf-8") as f:
        return json.load(f)


def _env():
    return (P.Ontology(os.path.join(DATA, "ontology_v0.json")),
            _load("templates_v0.json"), _load("rules_v0.json"), _load("coverage_v0.json"))


class _Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.onto, cls.tpl, cls.rules, cls.cov = _env()

    def _audit(self, fname, label, tk):
        q = P.build_line_index(P.load_quote(os.path.join(REAL, fname), label), self.onto)
        r = P.audit([q], self.onto, self.tpl, self.rules, self.cov, template_key=tk)[label]
        return q, r


class TestRealDocsPass(_Base):
    """回归：5 份真实单据本来就是合规的，加了门禁也不许把它们拦下来。"""

    def test_five_real_docs_all_deliverable(self):
        docs = [("sh_zhuoguan_halfpack.csv", "篱笆", "old_house"),
                ("huaxibeian_newhouse.csv", "花溪", "new_house"),
                ("szxinxin_clinic_pdf.csv", "诊所", "new_house"),
                ("feitai_budget.csv", "翡翠台", "old_house"),
                ("guijiao_124_basic.csv", "124㎡", "new_house")]
        for fname, label, tk in docs:
            q, r = self._audit(fname, label, tk)
            gate = G.check(q, r)
            self.assertEqual(gate["verdict"], "pass",
                             "%s 被误拦：%s" % (label, gate["blocking"] + gate["fixable"]))

    def test_recon_diff_with_r13_passes(self):
        """对不上账但有 R13 结论 → 应判可交付。真实单据 2/5 属于这种。"""
        q, r = self._audit("feitai_budget.csv", "翡翠台", "old_house")
        self.assertFalse(r["recon"]["ok"], "前置条件变了：翡翠台应当对不上账")
        self.assertTrue(any(x["rule"].startswith("R13") for x in r["risks"]))
        self.assertEqual(G.check(q, r)["verdict"], "pass")


class TestFaultInjection(_Base):
    """每次只破坏一处，验门禁认得出来。"""

    def _healthy(self):
        q, r = self._audit("feitai_budget.csv", "翡翠台", "old_house")
        return q, copy.deepcopy(r)

    def _verdict(self, q, r):
        return G.check(q, r)

    def test_hidden_recon_diff_is_blocked(self):
        """对账差异还在、R13 结论被删掉 —— 这是掩盖，必须 block。"""
        q, r = self._healthy()
        r["risks"] = [x for x in r["risks"] if not x["rule"].startswith("R13")]
        gate = self._verdict(q, r)
        self.assertEqual(gate["verdict"], "block")
        self.assertIn("D3", gate["blocking"])

    def test_downgraded_tier_without_deducting_is_blocked(self):
        """把一条 auto 降成 review，但钱没从核减里扣回来 —— 典型的口径漂移。"""
        q, r = self._healthy()
        auto = next(m for m in r["missing"] if m["tier"] == "auto" and m.get("ref_amount"))
        auto["tier"] = "review"
        gate = self._verdict(q, r)
        self.assertEqual(gate["verdict"], "block")
        self.assertIn("D5", gate["blocking"])

    def test_upgraded_tier_without_adding_money_is_blocked(self):
        """反向：把一条 review 提成 auto，钱却没加进核减 —— 一样是口径漂移。"""
        q, r = self._healthy()
        review = next(m for m in r["missing"] if m["tier"] != "auto")
        review["tier"] = "auto"
        review["ref_amount"] = 1200.0
        review["ref_source"] = ["test"]
        gate = self._verdict(q, r)
        self.assertEqual(gate["verdict"], "block")
        self.assertIn("D5", gate["blocking"])

    def test_invented_reference_amount_is_blocked(self):
        """给漏项编一个参照金额、不给来源 —— 钱是编的，必须 block。"""
        q, r = self._healthy()
        target = next(m for m in r["missing"] if not m.get("ref_amount"))
        target["ref_amount"] = 8888.0
        target["ref_source"] = []
        target["ref_basis"] = None
        r["missing_cost"] = round(r["missing_cost"] + 8888.0, 2)  # 同时绕过 D5，逼 D4 单独生效
        gate = self._verdict(q, r)
        self.assertEqual(gate["verdict"], "block")
        self.assertIn("D4", gate["blocking"])

    def test_fingerprint_mismatch_is_blocked(self):
        """指纹和基准对不上 —— 不可复现，必须 block。"""
        q, r = self._healthy()
        gate = G.check(q, r, expected_digest="deadbeefdeadbeef")
        self.assertEqual(gate["verdict"], "block")
        self.assertIn("D6", gate["blocking"])

    def test_missing_formula_is_needs_fix_only(self):
        """缺公式只是说不清依据，不影响对错 —— needs_fix，不许 block。"""
        q, r = self._healthy()
        for m in r["missing"]:
            (m.get("evidence") or {}).pop("formula", None)
        gate = self._verdict(q, r)
        self.assertEqual(gate["verdict"], "needs_fix")
        self.assertIn("D2", gate["fixable"])
        self.assertEqual(gate["blocking"], [])

    def test_no_line_no_basis_is_needs_fix(self):
        """说了但指不到 —— needs_fix。金额为 0 时才只触发 D1。"""
        q, r = self._healthy()
        target = next(x for x in r["risks"] if not x.get("amount") and x.get("line"))
        target.pop("line", None)
        target["evidence"] = {}
        gate = self._verdict(q, r)
        self.assertEqual(gate["verdict"], "needs_fix")
        self.assertIn("D1", gate["fixable"])

    def test_duplicate_without_lines_is_blocked(self):
        """重复计价有金额却没行号 —— 说不出这钱在哪，必须 block（走 D4）。"""
        q, r = self._healthy()
        self.assertTrue(r["duplicates"], "前置条件变了：翡翠台应有重复计价")
        r["duplicates"][0]["lines"] = []
        gate = self._verdict(q, r)
        self.assertIn("D4", gate["blocking"])


class TestUncoveredDrift(_Base):
    """D7：规则之间的空隙。R8 管 >1 元/行，R13 管 >0.5%，中间没人管。"""

    def _drift(self, per_row, n_rows):
        """在 feitai 上做 n_rows 行各 +per_row 元的小额注入。"""
        import copy
        q = P.build_line_index(P.load_quote(os.path.join(REAL, "feitai_budget.csv"), "漂移"),
                               self.onto)
        picks = [r for r in q["rows"] if r["qty"] > 0 and r["price"] > 0][:n_rows]
        for r in picks:
            r["amount"] = round(r["amount"] + per_row, 2)
        q["total"] = round(sum(x["amount"] for x in q["rows"]), 2)
        q["recon"] = P.reconcile(q["rows"], q.get("summary_rows") or [])
        res = P.audit([q], self.onto, self.tpl, self.rules, self.cov,
                      template_key="old_house")["漂移"]
        return q, res

    def test_ant_moving_is_caught(self):
        """100 行各虚 0.99 元，累计 99 元 —— R8 与 R13 都沉默，D7 必须开口。"""
        # 先拿基线：翡翠台本来就有 2 条 R8（窗帘盒 +10、餐厅柜 -8840），
        # 要看的是「注入有没有**新增**」，不是「有没有」。
        qb = P.build_line_index(P.load_quote(os.path.join(REAL, "feitai_budget.csv"), "基线"),
                                self.onto)
        rb = P.audit([qb], self.onto, self.tpl, self.rules, self.cov,
                     template_key="old_house")["基线"]
        base_r8 = {str(x.get("line")) for x in rb["risks"] if x["rule"].startswith("R8")}

        q, r = self._drift(0.99, 100)
        now_r8 = {str(x.get("line")) for x in r["risks"] if x["rule"].startswith("R8")}
        self.assertEqual(now_r8, base_r8,
                         "前置条件变了：小额注入竟然新增了 R8 结论，D7 的前提不成立")
        gate = G.check(q, r)
        self.assertIn("D7", gate["fixable"])
        self.assertEqual(gate["verdict"], "needs_fix")

    def test_random_rounding_is_not_flagged(self):
        """随机方向的舍入不是问题 —— 篱笆那份 15 行净额只有 +0.23 元，必须放过。"""
        q = P.build_line_index(P.load_quote(os.path.join(REAL, "sh_zhuoguan_halfpack.csv"), "篱笆"),
                               self.onto)
        r = P.audit([q], self.onto, self.tpl, self.rules, self.cov,
                    template_key="old_house")["篱笆"]
        gate = G.check(q, r)
        self.assertNotIn("D7", gate["fixable"], "正常舍入被误报成系统性偏移")

    def test_small_sample_not_flagged(self):
        """只有两三行偏差时不下结论——样本太少，可能就是笔误。"""
        q, r = self._drift(0.99, 3)
        gate = G.check(q, r)
        self.assertNotIn("D7", gate["fixable"])


class TestReproducibility(_Base):
    """D6 是唯一「只有确定性引擎能做」的一条，单独锁。"""

    def test_replay_gives_same_digest(self):
        q, r = self._audit("guijiao_124_basic.csv", "124㎡", "new_house")

        def replay():
            return P.audit([q], self.onto, self.tpl, self.rules, self.cov,
                           template_key="new_house")["124㎡"]

        gate = G.check(q, r, replay=replay)
        d6 = next(c for c in gate["checks"] if c["id"] == "D6")
        self.assertTrue(d6["ok"], d6["detail"])
        self.assertIn("重跑两次指纹一致", " ".join(d6["detail"]))

    def test_no_replay_is_marked_not_verified(self):
        """不给 replay 就必须写「未做重跑验证」，不能假装验过。"""
        q, r = self._audit("guijiao_124_basic.csv", "124㎡", "new_house")
        gate = G.check(q, r)
        self.assertIn("D6", gate["skipped"])
        d6 = next(c for c in gate["checks"] if c["id"] == "D6")
        self.assertIn("未做重跑验证", " ".join(d6["detail"]))


class TestRendering(unittest.TestCase):
    """判定必须能落进报告，否则只有开发者看得到。"""

    @classmethod
    def setUpClass(cls):
        cls.onto, cls.tpl, cls.rules, cls.cov = _env()

    def test_markdown_and_html_contain_verdict(self):
        q = P.build_line_index(P.load_quote(os.path.join(REAL, "feitai_budget.csv"), "翡翠台"), self.onto)
        r = P.audit([q], self.onto, self.tpl, self.rules, self.cov,
                    template_key="old_house")["翡翠台"]
        gate = G.check(q, r)
        md = G.to_markdown(gate)
        html = G.to_html(gate)
        self.assertIn("交付判定", md)
        self.assertIn("D1", md)
        self.assertIn("交付判定", html)
        self.assertIn("判定指纹", html)


if __name__ == "__main__":
    unittest.main()
