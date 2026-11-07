# -*- coding: utf-8 -*-
"""负向知识库的锁：每条「不判」都要有一个测试证明它真的没被判。

## 为什么负向也要测

正向规则改坏了，会有人发现（结论少了）。负向改坏了大都没人发现——
因为它的表现是「多了一条假阳性」，而假阳性长得和真结论一模一样。

最典型的例子：`find_duplicates` 里那句「变体不同就排除」。删掉它，
5 份单据会多出 72 条「重复计价」，而报告看起来更"丰富"了——
一个不懂业务的人只会觉得引擎更敏锐了。

所以这里给每条 NA 配一个断言。断言失败 = 有人把某条「不判」改掉了。
"""
import json
import os
import sys
import tempfile
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


def _audit(fname, label, tk, env):
    q = P.build_line_index(P.load_quote(os.path.join(REAL, fname), label), env[0])
    return q, P.audit([q], env[0], env[1], env[2], env[3], template_key=tk)[label]


class TestIndexIntegrity(unittest.TestCase):
    """索引本身要完整：缺字段的条目等于没有。"""

    def test_schema(self):
        doc = _load("not_applicable_v0.json")
        ids = set()
        for it in doc["items"]:
            for k in ("id", "case", "decision", "why", "evidence", "if_judged", "guard", "status"):
                self.assertIn(k, it, "%s 缺字段 %s" % (it.get("id"), k))
                self.assertTrue(str(it[k]).strip(), "%s 的 %s 是空的" % (it["id"], k))
            self.assertNotIn(it["id"], ids, "id 重复：%s" % it["id"])
            ids.add(it["id"])
            self.assertIn(it["status"], ("enforced", "documented", "known_gap"))
        self.assertGreaterEqual(len(doc["items"]), 10)


class TestNegativeClaims(unittest.TestCase):
    """每条「不判」的实测：证明它确实没被判。"""

    @classmethod
    def setUpClass(cls):
        cls.env = _env()

    def test_NA2_variant_is_not_duplicate(self):
        """同概念不同变体不算重复。篱笆那份 21 组全被排除，重复结论为 0。"""
        q, r = _audit("sh_zhuoguan_halfpack.csv", "篱笆", "old_house", self.env)
        self.assertEqual(len(r["duplicates"]), 0)
        self.assertEqual(r["dup_excluded"], 21)

    def test_NA2_total_excluded_across_corpus(self):
        """全库累计排除 72 组——这个数就是「假阳性会被躲掉多少」的直接度量。"""
        docs = [("sh_zhuoguan_halfpack.csv", "篱笆", "old_house"),
                ("huaxibeian_newhouse.csv", "花溪", "new_house"),
                ("szxinxin_clinic_pdf.csv", "诊所", "new_house"),
                ("feitai_budget.csv", "翡翠台", "old_house"),
                ("guijiao_124_basic.csv", "124㎡", "new_house")]
        total = sum(_audit(f, l, t, self.env)[1]["dup_excluded"] for f, l, t in docs)
        self.assertEqual(total, 72)

    def test_NA5_xiaoji_rows_survive(self):
        """诊所 19 行「…小计」是真工序，不许被当汇总行删掉。"""
        q, _ = _audit("szxinxin_clinic_pdf.csv", "诊所", "new_house", self.env)
        kept = [x for x in q["rows"] if "小计" in x["name"]]
        self.assertEqual(len(kept), 19)
        for x in kept:
            self.assertAlmostEqual(x["qty"] * x["price"], x["amount"], places=2)

    def test_NA6_range_price_only_vague(self):
        """区间价只判开口条款，不判算术不符。"""
        d = tempfile.mkdtemp()
        p = os.path.join(d, "q.csv")
        with open(p, "w", encoding="utf-8-sig") as f:
            f.write("项目名称,规格工艺,单位,数量,单价,合价\n"
                    "水电改造,按实结算,项,1,8000-12000,8000\n")
        q = P.build_line_index(P.load_quote(p, "区间"), self.env[0])
        r = P.audit([q], self.env[0], self.env[1], self.env[2], self.env[3],
                    template_key="old_house")["区间"]
        rules = {x["rule"] for x in r["risks"]}
        self.assertIn("R4_vague_term", rules)
        self.assertNotIn("R8_arithmetic", rules, "区间价被误判成算术错误")

    def test_NA7_gift_rows_not_anomaly(self):
        """赠送项（0 元）不判单位异常/数量缺失。"""
        d = tempfile.mkdtemp()
        p = os.path.join(d, "q.csv")
        with open(p, "w", encoding="utf-8-sig") as f:
            f.write("项目名称,规格工艺,单位,数量,单价,合价\n"
                    "地砖铺贴,800x800, m2,40,120,4800\n"
                    "（赠送）开关面板,,个,20,0,0\n")
        q = P.build_line_index(P.load_quote(p, "赠品"), self.env[0])
        r = P.audit([q], self.env[0], self.env[1], self.env[2], self.env[3],
                    template_key="old_house")["赠品"]
        bad = [x for x in r["risks"] if x["rule"] in ("R6_unit_anomaly", "R9_qty_unit")]
        self.assertEqual(bad, [], "赠送行被当成异常：%s" % bad)

    def test_NA8_positive_discount_is_flagged_not_guessed(self):
        """正数优惠只标歧义，不进算式。"""
        d = tempfile.mkdtemp()
        p = os.path.join(d, "q.csv")
        with open(p, "w", encoding="utf-8-sig") as f:
            f.write("项目名称,单位,数量,单价,合价\n"
                    "拆除墙体,m2,10,50,500\n"
                    "全屋优惠,项,1,3000,3000\n"
                    "合计,,,,,500\n")
        q = P.load_quote(p, "歧义")
        self.assertEqual(q["recon"]["discount_sum"], 0.0)
        self.assertTrue(q["recon"]["discount_ambiguous"])

    def test_NA9_low_confidence_never_enters_money(self):
        """5 份单据的核减金额必须逐分等于 auto 档之和。"""
        docs = [("sh_zhuoguan_halfpack.csv", "篱笆", "old_house"),
                ("huaxibeian_newhouse.csv", "花溪", "new_house"),
                ("feitai_budget.csv", "翡翠台", "old_house"),
                ("guijiao_124_basic.csv", "124㎡", "new_house")]
        for f, l, t in docs:
            q, r = _audit(f, l, t, self.env)
            auto = round(sum(m.get("ref_amount") or 0 for m in r["missing"]
                             if m["tier"] == "auto"), 2)
            self.assertAlmostEqual(r["missing_cost"], auto, places=2,
                                   msg="%s 的核减金额混进了低置信结论" % l)

    def test_NA11_small_rounding_not_arithmetic(self):
        """单行差 ≤1 元不判算术不符。"""
        d = tempfile.mkdtemp()
        p = os.path.join(d, "q.csv")
        with open(p, "w", encoding="utf-8-sig") as f:
            f.write("项目名称,单位,数量,单价,合价\n"
                    "拆除墙体,m2,3,50,150.50\n")   # 3×50=150，差 0.5
        q = P.build_line_index(P.load_quote(p, "舍入"), self.env[0])
        r = P.audit([q], self.env[0], self.env[1], self.env[2], self.env[3],
                    template_key="old_house")["舍入"]
        self.assertFalse([x for x in r["risks"] if x["rule"].startswith("R8")])

    def test_NA12_no_printed_total_is_skip_not_fail(self):
        """没有印刷合计 → 标「未做对账」，不是「对不上账」。"""
        q, r = _audit("sh_zhuoguan_halfpack.csv", "篱笆", "old_house", self.env)
        self.assertIsNone(r["recon"]["ok"])
        gate = G.check(q, r)
        self.assertIn("D3", gate["skipped"])
        d3 = next(c for c in gate["checks"] if c["id"] == "D3")
        self.assertIn("未做整单对账", " ".join(d3["detail"]))
        self.assertEqual(gate["verdict"], "pass")


if __name__ == "__main__":
    unittest.main()
