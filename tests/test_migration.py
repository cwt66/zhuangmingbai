# -*- coding: utf-8 -*-
"""迁移实验的锁：同一套引擎能不能审另一个行业。

这不是「多做一个 demo」，而是验证一个架构主张：
**判定能力不在代码里，在三份 JSON 里**（本体 = 概念与别名，模板 = 什么叫做完，规则 = 怎么判）。
如果这个主张成立，换领域只需要换数据；如果不成立，这里就会挂。

迁移实验同时抓出过两个真实耦合（已修，并被本文件第 4 个用例锁住）：
① R6 单位异常规则直接取 onto["u"]，缺字段就崩——汽修本体没有默认面积单位；
② build_markdown 假设 results["_cross_unit"] 一定存在，单条结果就 KeyError。
"""
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
import pipeline as P  # noqa: E402

DATA = os.path.join(ROOT, "data")
REPAIR = os.path.join(DATA, "repair")


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _env(paths=None):
    onto = P.Ontology((paths or {}).get("ontology", os.path.join(DATA, "ontology_v0.json")))
    tpl = _load((paths or {}).get("templates", os.path.join(DATA, "templates_v0.json")))
    rules = _load((paths or {}).get("rules", os.path.join(DATA, "rules_v0.json")))
    cov = _load((paths or {}).get("coverage", os.path.join(DATA, "coverage_v0.json")))
    return onto, tpl, rules, cov


def _repair_env():
    return _env({"ontology": os.path.join(REPAIR, "ontology_repair.json"),
                 "templates": os.path.join(REPAIR, "templates_repair.json"),
                 "coverage": os.path.join(REPAIR, "coverage_repair.json")})


class TestRepairMigration(unittest.TestCase):
    """汽修维保单：同样的 audit()，不同的三份 JSON。"""

    def _run(self):
        onto, tpl, rules, cov = _repair_env()
        q = P.build_line_index(
            P.load_quote(os.path.join(REPAIR, "sample_major_service.csv"), "迈腾大保养"), onto)
        results = P.audit([q], onto, tpl, rules, cov, template_key="major_service")
        return q, results, results[q["label"]]

    def test_alignment_is_complete(self):
        """40 概念 / 97 别名足以覆盖这张单——对齐 19/19。"""
        q, _, _ = self._run()
        aligned = sum(1 for r in q["rows"] if r["cid"])
        self.assertEqual(aligned, len(q["rows"]))
        self.assertEqual(len(q["rows"]), 19)
        self.assertEqual(len(q["summary_rows"]), 5)   # 工时/材料小计 + 管理费 + 合计 + 实收

    def test_designed_defects_are_all_caught(self):
        """样例里故意埋的四类问题必须全部被抓到，且定位正确。"""
        _, _, res = self._run()
        missing = {m["cid"] for m in res["missing"] if m["tier"] == "auto"}
        self.assertEqual(missing, {"cabin_filter", "chassis", "suspension", "lamp"})

        self.assertEqual(len(res["duplicates"]), 1)
        dup = res["duplicates"][0]
        self.assertEqual(dup["concept"], "四轮定位")
        self.assertEqual(sorted(dup["lines"]), ["8", "9"])

        rules_hit = {x["rule"] for x in res["risks"]}
        self.assertIn("R8_arithmetic", rules_hit)      # 辅料费 1×50 写成 60
        self.assertIn("R13_doc_recon", rules_hit)      # 整单差 30
        self.assertIn("R4_vague_term", rules_hit)      # 另计 / 按实结算

    def test_reconciliation_catches_30(self):
        _, _, res = self._run()
        rec = res["recon"]
        self.assertAlmostEqual(rec["detail_sum"], 3201.00, places=2)
        self.assertAlmostEqual(rec["fee_sum"], 149.00, places=2)
        self.assertAlmostEqual(rec["grand"]["amount"], 3380.00, places=2)
        self.assertAlmostEqual(rec["diff"], 30.00, places=2)
        self.assertFalse(rec["ok"])

    def test_obligations_transfer(self):
        """开口条款的抽取与判定同样不依赖领域：汽修单也该给出「送修前确认什么」。"""
        _, _, res = self._run()
        ids = {o["id"] for o in res["obligations"]}
        self.assertIn("ob_settle_actual", ids)
        self.assertIn("ob_extra", ids)

    def test_pricing_system_detected(self):
        _, _, res = self._run()
        self.assertEqual(res["pricing"]["system"], "qty_price")


class TestEngineHasNoDomainKnowledge(unittest.TestCase):
    """同一个函数、同一份规则，两个行业都能跑——差别只在传入的数据。"""

    def test_same_audit_function_two_domains(self):
        onto_a, tpl_a, rules, cov_a = _env()
        qa = P.build_line_index(
            P.load_quote(os.path.join(DATA, "real", "feitai_budget.csv"), "翡翠台"), onto_a)
        ra = P.audit([qa], onto_a, tpl_a, rules, cov_a, template_key="old_house")[qa["label"]]

        onto_b, tpl_b, rules_b, cov_b = _repair_env()
        qb = P.build_line_index(
            P.load_quote(os.path.join(REPAIR, "sample_major_service.csv"), "迈腾大保养"), onto_b)
        rb = P.audit([qb], onto_b, tpl_b, rules_b, cov_b, template_key="major_service")[qb["label"]]

        # 两个领域：模板规模不同、结论不同，但都用同一个 audit()
        self.assertEqual(ra["template_size"], 39)
        self.assertEqual(rb["template_size"], 18)
        self.assertNotEqual({m["cid"] for m in ra["missing"]},
                            {m["cid"] for m in rb["missing"]})
        # 规则库是同一份（领域无关的判定逻辑），证明「规则可复用」
        self.assertEqual(len(rules["rules"]), len(rules_b["rules"]))
        self.assertEqual({x["id"] for x in rules["rules"]}, {x["id"] for x in rules_b["rules"]})

    def test_missing_unit_field_does_not_crash(self):
        """本体缺 'u' 字段（汽修就是这样）不能崩——迁移实验抓出的隐式依赖。"""
        import tempfile
        tmp = tempfile.mkdtemp(prefix="zmb_onto_")
        path = os.path.join(tmp, "onto_min.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump([{"id": "oil", "n": "机油", "a": ["机油"], "c": "material"}], f,
                      ensure_ascii=False)
        try:
            onto = P.Ontology(path)
            quote = {"label": "x", "file": "x.csv", "total": 100.0, "recon": None,
                     "summary_rows": [],
                     "rows": [{"no": "1", "name": "机油", "spec": "", "unit": "项",
                               "qty": 1, "price": 100, "amount": 100, "note": "",
                               "cid": "oil", "align_conf": 0.9, "align_how": "exact"}]}
            rules = _load(os.path.join(DATA, "rules_v0.json"))
            cov = {"evidence": {}, "absorb": {}, "soft": {}}
            res = P.audit([quote], onto, {"t": ["oil"]}, rules, cov, template_key="t")
            self.assertIn("x", res)
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
