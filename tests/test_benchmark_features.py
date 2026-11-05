# -*- coding: utf-8 -*-
"""对标 RenoPit 后新增能力的锁：文档守门 + 确定性增项预测。

守三件事：
1. 五份真实单据必须判为报价单（含「项目名里没有报价/预算字样」的篱笆单）；
   合同 / 无关文档必须被拒绝，且给出可核对的理由。
2. 增项预测**不给概率**，金额区间来自参照价库，开口条款已列金额不重复计入。
3. 审计包必须带上新模块，否则离线校验器会 ModuleNotFoundError。
"""
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import pipeline as P  # noqa: E402
import doc_gate as G  # noqa: E402
import addon_forecast as A  # noqa: E402

REAL = [("feitai_budget.csv", "old_house"), ("guijiao_124_basic.csv", "new_house"),
        ("sh_zhuoguan_halfpack.csv", "old_house"), ("huaxibeian_newhouse.csv", "new_house"),
        ("szxinxin_clinic_pdf.csv", "new_house")]


def _env():
    onto = P.Ontology(os.path.join(ROOT, "data", "ontology_v0.json"))
    with open(os.path.join(ROOT, "data", "templates_v0.json"), encoding="utf-8") as f:
        tpl = json.load(f)
    with open(os.path.join(ROOT, "data", "rules_v0.json"), encoding="utf-8") as f:
        rules = json.load(f)
    with open(os.path.join(ROOT, "data", "coverage_v0.json"), encoding="utf-8") as f:
        cov = json.load(f)
    return onto, tpl, rules, cov


def _mkrows(names, amounts=None):
    amounts = amounts or [0] * len(names)
    return [{"no": str(i + 1), "name": n, "spec": "", "unit": "", "qty": 0,
             "price": 0, "amount": amounts[i], "note": ""} for i, n in enumerate(names)]


class TestDocGate(unittest.TestCase):
    def test_all_real_quotes_accepted(self):
        for f, _ in REAL:
            with self.subTest(f=f):
                q = P.load_quote(os.path.join(ROOT, "data", "real", f), f)
                ok, info = G.gate(q["rows"], f)
                self.assertTrue(ok, (f, info["message"]))
                self.assertEqual(info["doc_type"], "quotation", f)
                self.assertGreaterEqual(info["confidence"], 0.85)

    def test_structure_beats_keywords(self):
        """篱笆单项目名里没有「报价/预算」，但 8 列齐全 + 金额列有数 → 仍是报价单。"""
        q = P.load_quote(os.path.join(ROOT, "data", "real", "sh_zhuoguan_halfpack.csv"), "x")
        info = G.classify(q["rows"], "sh_zhuoguan_halfpack.csv")
        self.assertEqual(info["doc_type"], "quotation")
        self.assertGreaterEqual(len(info["structure_cols"]), 5)
        self.assertGreaterEqual(info["amount_ratio"], 0.9)

    def test_contract_rejected(self):
        rows = _mkrows(["甲方与乙方双方约定", "本合同工期90天", "付款方式分期",
                        "违约责任条款", "争议解决方式", "签字盖章生效"], [100] * 6)
        ok, info = G.gate(rows, "装修合同.txt")
        self.assertFalse(ok)
        self.assertEqual(info["doc_type"], "contract")
        self.assertIn("不审合同条款", info["message"])
        self.assertTrue(info["contract_hits"])

    def test_irrelevant_rejected(self):
        rows = _mkrows(["宫保鸡丁 38", "鱼香肉丝 32", "米饭 2", "菜单 今日特价"])
        ok, info = G.gate(rows, "菜单.txt")
        self.assertFalse(ok)
        self.assertEqual(info["doc_type"], "irrelevant")

    def test_evidence_is_inspectable(self):
        q = P.load_quote(os.path.join(ROOT, "data", "real", "feitai_budget.csv"), "x")
        info = G.classify(q["rows"], "a.csv")
        self.assertTrue(info["reasons"])
        self.assertTrue(info["quote_hits"])
        self.assertIn("不是概率", info["note"])


class TestAddonForecast(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        onto, tpl, rules, cov = _env()
        q = P.build_line_index(P.load_quote(
            os.path.join(ROOT, "data", "real", "feitai_budget.csv"), "翡翠台"), onto)
        cls.res = P.audit([q], onto, tpl, rules, cov,
                          template_key="old_house")[q["label"]]
        cls.af = cls.res["addon_forecast"]

    def test_no_probability_anywhere(self):
        self.assertIsNone(self.af["method"]["probability"])
        for x in self.af["items"]:
            self.assertIsNone(x["probability"], x["title"])

    def test_open_clause_amount_not_double_counted(self):
        sm = self.af["summary"]
        opens = [x for x in self.af["items"] if x["kind"] == "open_clause"]
        self.assertTrue(opens)
        self.assertGreater(sm["exposure_listed"], 0)
        # 开口条款的金额区间为空，所以不会进入 amount_low/high
        for x in opens:
            self.assertIsNone(x["amount_range"])
        self.assertLess(sm["amount_low"], sm["exposure_listed"])

    def test_process_items_have_no_amount(self):
        proc = [x for x in self.af["items"] if x["kind"] == "missing_process"]
        self.assertTrue(proc)
        for x in proc:
            self.assertIsNone(x["amount_range"])
            self.assertIn("过程记录", x["prevention"])

    def test_amount_range_comes_from_reference_book(self):
        onto, tpl, rules, cov = _env()
        q = P.build_line_index(P.load_quote(
            os.path.join(ROOT, "data", "real", "guijiao_124_basic.csv"), "124㎡"), onto)
        r = P.audit([q], onto, tpl, rules, cov, template_key="new_house")[q["label"]]
        priced = [x for x in r["addon_forecast"]["items"] if x["amount_range"]]
        self.assertTrue(priced)
        self.assertTrue(any("参照价库" in x["amount_basis"] for x in priced))

    def test_report_renders_forecast(self):
        onto, _, _, _ = _env()
        q = P.build_line_index(P.load_quote(
            os.path.join(ROOT, "data", "real", "feitai_budget.csv"), "翡翠台"), onto)
        html = P.build_html([q], {q["label"]: self.res})
        self.assertIn("签约前增项风险预测", html)
        self.assertIn("不输出发生概率", html)


class TestBundleIncludesNewModules(unittest.TestCase):
    def test_code_list(self):
        import importlib.util
        path = os.path.join(ROOT, "scripts", "make_bundle.py")
        spec = importlib.util.spec_from_file_location("make_bundle", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        for f in ("addon_forecast.py", "doc_gate.py"):
            self.assertIn(f, mod.CODE)
            self.assertTrue(os.path.isfile(os.path.join(ROOT, "src", f)))


if __name__ == "__main__":
    unittest.main()


class TestShareablePDF(unittest.TestCase):
    """RenoPit 有「一键 PDF」，我们的 PDF 必须由同一份 audit 结果生成。"""

    def test_pdf_from_same_result(self):
        import importlib.util
        import tempfile
        try:
            from pypdf import PdfReader  # noqa: F401
            import report_pdf  # noqa: E402
        except ImportError:
            self.skipTest("reportlab / pypdf 未安装")
        onto, tpl, rules, cov = _env()
        q = P.build_line_index(P.load_quote(
            os.path.join(ROOT, "data", "real", "feitai_budget.csv"), "翡翠台"), onto)
        res = P.audit([q], onto, tpl, rules, cov, template_key="old_house")
        tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        tmp.close()
        try:
            report_pdf.export_pdf(tmp.name, [q], res, source="翡翠台", fingerprint="abc123")
            from pypdf import PdfReader
            t = "".join((p.extract_text() or "") for p in PdfReader(tmp.name).pages)
            self.assertIn("装明白", t)
            self.assertIn("总览", t)
            self.assertIn("增项风险", t)
            self.assertIn("不输出发生概率", t)
            self.assertIn("不构成法律意见", t)
            self.assertGreater(os.path.getsize(tmp.name), 5000)
        finally:
            os.unlink(tmp.name)

    def test_cli_script_exists(self):
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "scripts", "make_report_pdf.py")))
