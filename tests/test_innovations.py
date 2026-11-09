# -*- coding: utf-8 -*-
"""四个创新点的锁：报价范围裁剪 / 计价体系识别 / 条款义务 / 审计包。

写这些测试的理由和别处一样：能力刚做出来时是对的，改两次就悄悄退化了。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
import obligations as oblig  # noqa: E402
import pipeline as P  # noqa: E402
import pricing_system as pricing  # noqa: E402

DATA = os.path.join(ROOT, "data")
REAL = os.path.join(DATA, "real")


def _load(name):
    with open(os.path.join(DATA, name), encoding="utf-8") as f:
        return json.load(f)


def _env():
    return (P.Ontology(os.path.join(DATA, "ontology_v0.json")),
            _load("templates_v0.json"), _load("rules_v0.json"), _load("coverage_v0.json"))


def _row(unit, qty, price, amount=None, name="x"):
    return {"no": "1", "name": name, "unit": unit, "qty": qty, "price": price,
            "amount": amount if amount is not None else qty * price, "spec": "", "note": ""}


class TestScopeTrim(unittest.TestCase):
    """报价范围声明：只减少「要求项」，不改任何已列项的判定。"""

    def test_base_scope_drops_material_items(self):
        onto, tpl, rules, cov = _env()
        q = P.build_line_index(P.load_quote(os.path.join(REAL, "guijiao_124_basic.csv"), "124㎡"), onto)
        full = P.audit([q], onto, tpl, rules, cov, template_key="new_house", scope="full")[q["label"]]
        base = P.audit([q], onto, tpl, rules, cov, template_key="new_house", scope="base")[q["label"]]
        self.assertEqual(full["template_size"], 28)
        self.assertEqual(base["template_size"], 20)
        full_auto = {m["cid"] for m in full["missing"] if m["tier"] == "auto"}
        base_auto = {m["cid"] for m in base["missing"] if m["tier"] == "auto"}
        self.assertIn("door_install", full_auto)          # 全包口径下木门是漏项
        self.assertNotIn("door_install", base_auto)       # 基础口径下不判漏
        self.assertIn("pressure_test", base_auto)         # 基础工序仍然判

    def test_scope_never_changes_present_decisions(self):
        """裁剪只能减少要求项：全包口径下判 present 的，基础口径下必须仍 present。"""
        onto, tpl, rules, cov = _env()
        q = P.build_line_index(P.load_quote(os.path.join(REAL, "feitai_budget.csv"), "翡翠台"), onto)
        full = P.audit([q], onto, tpl, rules, cov, template_key="old_house", scope="full")[q["label"]]
        base = P.audit([q], onto, tpl, rules, cov, template_key="old_house", scope="base")[q["label"]]
        full_flag = {m["cid"] for m in full["missing"]}
        base_flag = {m["cid"] for m in base["missing"]}
        self.assertTrue(base_flag.issubset(full_flag), "基础口径不该新增任何漏项")

    def test_unknown_scope_falls_back_to_full(self):
        template = ["threshold", "tile_floor"]
        self.assertEqual(P.trim_template(template, "whatever"), template)


class TestPricingSystem(unittest.TestCase):
    """四类计价体系必须能分开——真实误判（套餐被当成清单）靠合成样本测出来过。"""

    def test_qty_price(self):
        rows = [_row(u, 10, 80) for u in ("m2", "m", "个", "根", "套", "樘", "kg", "m2", "m", "块")]
        self.assertEqual(pricing.classify({"rows": rows})["system"], "qty_price")

    def test_area_rate(self):
        rows = [_row("m2", 124, 105)] + [_row("m2", q, 680) for q in (6, 8, 8, 5, 13, 12)]
        self.assertEqual(pricing.classify({"rows": rows})["system"], "area_rate")

    def test_package(self):
        rows = [_row("项", 1, 3000) for _ in range(20)] + [_row("m2", 50, 90)]
        self.assertEqual(pricing.classify({"rows": rows})["system"], "package")

    def test_lump_sum(self):
        rows = [_row("", 0, 0, 46000)] * 12
        self.assertEqual(pricing.classify({"rows": rows})["system"], "lump_sum")

    def test_real_docs_are_qty_price(self):
        """现有 5 份真实单据确实都是量价清单——识别结果应一致，别为了好看造差异。"""
        for fn, lb in [("feitai_budget.csv", "翡翠台"), ("guijiao_124_basic.csv", "124㎡"),
                       ("szxinxin_clinic_pdf.csv", "诊所"), ("sh_zhuoguan_halfpack.csv", "篱笆"),
                       ("huaxibeian_newhouse.csv", "花溪")]:
            q = P.load_quote(os.path.join(REAL, fn), lb)
            r = pricing.classify(q)
            self.assertEqual(r["system"], "qty_price", lb)
            self.assertGreaterEqual(r["confidence"], 0.6)

    def test_evidence_is_repeatable(self):
        """依据必须可复述（占比/行数），不能是黑箱分数。"""
        q = P.load_quote(os.path.join(REAL, "feitai_budget.csv"), "翡翠台")
        r = pricing.classify(q)
        self.assertEqual(len(r["evidence"]), 3)
        self.assertIn("行", r["evidence"][0])


class TestObligations(unittest.TestCase):
    """开口条款 → 签约前必须书面确认的问题。"""

    def test_settle_actual_detected(self):
        onto, tpl, rules, cov = _env()
        q = P.build_line_index(P.load_quote(os.path.join(REAL, "sh_zhuoguan_halfpack.csv"), "篱笆"), onto)
        res = P.audit([q], onto, tpl, rules, cov, template_key="old_house")[q["label"]]
        ids = {o["id"] for o in res["obligations"]}
        self.assertIn("ob_settle_actual", ids)
        hit = [o for o in res["obligations"] if o["id"] == "ob_settle_actual"][0]
        self.assertEqual(hit["severity"], "high")
        self.assertEqual(hit["exposure"], "open")          # 金额上不封顶
        self.assertTrue(hit["confirm"])                    # 必须给出「要确认什么」
        self.assertTrue(hit["evidence"])                   # 必须给出「要留什么证据」

    def test_aggregates_same_obligation(self):
        """同一义务只出一条，行号聚合——否则一份单能刷出几十条，用户直接不看。"""
        quote = {"rows": [
            {"no": "1", "name": "甲", "spec": "", "note": "灯具另计", "unit": "项",
             "qty": 1, "price": 100, "amount": 100},
            {"no": "2", "name": "乙", "spec": "", "note": "五金另计", "unit": "项",
             "qty": 1, "price": 200, "amount": 200}]}
        out = oblig.extract(quote)
        extra = [o for o in out if o["id"] == "ob_extra"]
        self.assertEqual(len(extra), 1)
        self.assertEqual(extra[0]["lines"], ["1", "2"])
        self.assertAlmostEqual(extra[0]["amount"], 300.0)

    def test_no_obligation_when_clean(self):
        quote = {"rows": [{"no": "1", "name": "贴砖", "spec": "", "note": "含水泥砂浆",
                           "unit": "m2", "qty": 10, "price": 80, "amount": 800}]}
        self.assertEqual(oblig.extract(quote), [])
        self.assertIn("未发现开口条款", oblig.summary([]))


class TestBundleDigest(unittest.TestCase):
    """结论摘要：同输入同标准必须同摘要；改一个数字必须变。"""

    def test_digest_is_stable_and_sensitive(self):
        onto, tpl, rules, cov = _env()
        path = os.path.join(REAL, "feitai_budget.csv")
        q1 = P.build_line_index(P.load_quote(path, "翡翠台"), onto)
        r1 = P.audit([q1], onto, tpl, rules, cov, template_key="old_house")[q1["label"]]
        d1 = P.result_digest(q1, r1)

        q2 = P.build_line_index(P.load_quote(path, "翡翠台"), onto)
        r2 = P.audit([q2], onto, tpl, rules, cov, template_key="old_house")[q2["label"]]
        self.assertEqual(d1, P.result_digest(q2, r2), "同输入必须同摘要")

        q3 = P.build_line_index(P.load_quote(path, "翡翠台"), onto)
        for row in q3["rows"]:
            if row["no"] == "7":
                row["amount"] = round(row["amount"] + 1, 2)
        q3["total"] = round(sum(x["amount"] for x in q3["rows"]), 2)
        r3 = P.audit([q3], onto, tpl, rules, cov, template_key="old_house")[q3["label"]]
        self.assertNotEqual(d1, P.result_digest(q3, r3), "改一个数字必须变")

    def test_report_fingerprint_equals_bundle_digest(self):
        """报告封面的指纹必须与审计包里的指纹**同一个数**。

        曾经的实现是对整个 result 做 sha256，于是「报告上的指纹」和
        「审计包里的指纹」不相等——业主拿报告核对审计包时对不上，
        第一反应就是被篡改。指纹只该对判定敏感。
        """
        onto, tpl, rules, cov = _env()
        q = P.build_line_index(P.load_quote(os.path.join(REAL, "feitai_budget.csv"), "翡翠台"), onto)
        r = P.audit([q], onto, tpl, rules, cov, template_key="old_house")[q["label"]]
        self.assertEqual(P._report_fingerprint({q["label"]: r}), P.result_digest(q, r))

    def test_report_fingerprint_ignores_irrelevant_fields(self):
        """加一个只给界面用的字段，判定指纹不许变。"""
        import copy
        onto, tpl, rules, cov = _env()
        q = P.build_line_index(P.load_quote(os.path.join(REAL, "feitai_budget.csv"), "翡翠台"), onto)
        r = P.audit([q], onto, tpl, rules, cov, template_key="old_house")[q["label"]]
        before = P._report_fingerprint({q["label"]: r})
        noisy = copy.deepcopy(r)
        noisy["_ui_hint"] = "只影响渲染"
        noisy["generated_at"] = "2026-11-07 12:00"
        self.assertEqual(before, P._report_fingerprint({q["label"]: noisy}))

    def test_report_fingerprint_moves_when_conclusion_changes(self):
        """删掉一条漏项，判定指纹必须变——否则指纹就白盖了。"""
        import copy
        onto, tpl, rules, cov = _env()
        q = P.build_line_index(P.load_quote(os.path.join(REAL, "feitai_budget.csv"), "翡翠台"), onto)
        r = P.audit([q], onto, tpl, rules, cov, template_key="old_house")[q["label"]]
        before = P._report_fingerprint({q["label"]: r})
        trimmed = copy.deepcopy(r)
        trimmed["missing"] = trimmed["missing"][:-1]
        self.assertNotEqual(before, P._report_fingerprint({q["label"]: trimmed}))

    def test_bundle_roundtrip_and_tamper(self):
        """真打一个包、真跑一次校验器：PASS；改一个数字后必须 FAIL。"""
        tmp = tempfile.mkdtemp(prefix="zmb_test_bundle_")
        try:
            zp = os.path.join(tmp, "b.zip")
            # 单测必须用自己的临时账本：默认账本是生产链，
            # 让单测往里写测试数据，等于自己污染自己的防篡改日志。
            # 2026-11-09 真踩过——124㎡ 这条测试记录混进了 seq=7。
            r = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "make_bundle.py"),
                                "--csv", os.path.join(REAL, "guijiao_124_basic.csv"),
                                "--label", "124㎡", "--template", "new_house",
                                "--scope", "base", "--out", zp,
                                "--ledger", os.path.join(tmp, "ledger.jsonl"),
                                "--anchor-file", os.path.join(tmp, "anchor.json")],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue(zipfile.is_zipfile(zp))

            work = os.path.join(tmp, "v")
            with zipfile.ZipFile(zp) as z:
                z.extractall(work)
            ok = subprocess.run([sys.executable, os.path.join(work, "verify.py")],
                                capture_output=True, text=True)
            self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)
            self.assertIn("[PASS]", ok.stdout)

            # 篡改输入：把数量 65 改成 66
            csv_path = os.path.join(work, "input.csv")
            with open(csv_path, encoding="utf-8-sig") as f:
                text = f.read()
            with open(csv_path, "w", encoding="utf-8-sig") as f:
                f.write(text.replace("65,0", "66,0", 1))
            bad = subprocess.run([sys.executable, os.path.join(work, "verify.py")],
                                 capture_output=True, text=True)
            self.assertEqual(bad.returncode, 1)
            self.assertIn("[FAIL]", bad.stdout)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
