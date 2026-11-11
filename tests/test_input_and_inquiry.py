# -*- coding: utf-8 -*-
"""输入兼容与问询单的锁。

这两件事看起来「只是体验」，实际上是真实用户能不能用的分水岭：
- Windows 版 Excel 另存 CSV 默认 GBK，读不了 = 一半用户第一步就失败；
- 从 Excel 复制粘贴是 TAB 分隔，读进来列全是空 = 静默出错，比崩更糟；
- 装企模板写「项目 / 工程量 / 金额」而不是「项目名称 / 数量 / 合价」；
- 审出结论却不知道第一句该跟对方说什么 = 报告没人用。
"""
import csv
import json
import os
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
import inquiry  # noqa: E402
import pipeline as P  # noqa: E402

DATA = os.path.join(ROOT, "data")
REAL = os.path.join(DATA, "real")


def _write(path, rows, enc="utf-8", delim=","):
    with open(path, "w", encoding=enc, newline="") as f:
        csv.writer(f, delimiter=delim).writerows(rows)
    return path


HDR = ["序号", "项目名称", "规格工艺", "单位", "数量", "单价", "合价", "备注"]
BODY = [["1", "墙面乳胶漆", "分区：客厅", "m2", "100", "18", "1800", "立邦"],
        ["2", "地面贴砖", "分区：客厅", "m2", "40", "75", "3000", ""]]


class TestInputCompatibility(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="zmb_in_")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_gbk_encoding(self):
        """Windows Excel 另存为 CSV 的默认编码是 GBK/GB18030。"""
        path = _write(os.path.join(self.tmp, "gbk.csv"), [HDR] + BODY, enc="gbk")
        q = P.load_quote(path, "gbk")
        self.assertEqual(len(q["rows"]), 2)
        self.assertIn("gb", q["encoding"].lower())
        self.assertEqual(q["rows"][0]["name"], "墙面乳胶漆")

    def test_tab_delimited(self):
        """从 Excel 复制粘贴出来是 TAB 分隔——不能静默读成空列。"""
        path = _write(os.path.join(self.tmp, "t.tsv"), [HDR] + BODY, delim="\t")
        q = P.load_quote(path, "tsv")
        self.assertEqual(q["delimiter"], "\t")
        self.assertEqual(len(q["rows"]), 2)
        self.assertEqual(q["rows"][1]["amount"], 3000.0)

    def test_header_aliases(self):
        """装企模板常见写法：项目 / 工程量 / 金额。"""
        hdr = ["编号", "项目", "做法", "计量单位", "工程量", "综合单价", "金额", "附注"]
        path = _write(os.path.join(self.tmp, "alias.csv"), [hdr] + BODY, enc="gbk")
        q = P.load_quote(path, "alias")
        self.assertEqual(q["columns"]["项目名称"], "项目")
        self.assertEqual(q["columns"]["合价"], "金额")
        self.assertEqual(q["rows"][0]["qty"], 100.0)
        self.assertEqual(q["rows"][0]["amount"], 1800.0)

    def test_missing_name_column_raises_clear_error(self):
        path = _write(os.path.join(self.tmp, "bad.csv"), [["序号", "金额"], ["1", "100"]])
        with self.assertRaises(ValueError) as ctx:
            P.load_quote(path, "bad")
        self.assertIn("项目名称", str(ctx.exception))

    def test_existing_fixtures_unchanged(self):
        """新逻辑不能改变既有 5 份真实单据的读取结果。"""
        q = P.load_quote(os.path.join(REAL, "feitai_budget.csv"), "翡翠台")
        self.assertEqual(len(q["rows"]), 123)
        self.assertEqual(len(q["summary_rows"]), 8)
        self.assertEqual(q["encoding"], "utf-8-sig")


class TestInquiry(unittest.TestCase):
    def _build(self):
        onto = P.Ontology(os.path.join(DATA, "ontology_v0.json"))
        with open(os.path.join(DATA, "templates_v0.json"), encoding="utf-8") as f:
            tpl = json.load(f)
        with open(os.path.join(DATA, "rules_v0.json"), encoding="utf-8") as f:
            rules = json.load(f)
        with open(os.path.join(DATA, "coverage_v0.json"), encoding="utf-8") as f:
            cov = json.load(f)
        q = P.build_line_index(P.load_quote(os.path.join(REAL, "feitai_budget.csv"), "翡翠台"), onto)
        res = P.audit([q], onto, tpl, rules, cov, template_key="old_house")[q["label"]]
        return inquiry.build(q, res)

    def _build_multi(self):
        """三份样例跑一次对照审计，再给 B 公司出问询单。

        跨单口径只有**对照**才存在：单份单据里「按根」是合法写法，
        要跟另外两家「按米」放在一起才成为「不可直接比」。
        """
        onto = P.Ontology(os.path.join(DATA, "ontology_v0.json"))
        with open(os.path.join(DATA, "templates_v0.json"), encoding="utf-8") as f:
            tpl = json.load(f)
        with open(os.path.join(DATA, "rules_v0.json"), encoding="utf-8") as f:
            rules = json.load(f)
        with open(os.path.join(DATA, "coverage_v0.json"), encoding="utf-8") as f:
            cov = json.load(f)
        files = [("A 公司（规范型）", "company_a.csv"),
                 ("B 公司（低价引流型）", "company_b.csv"),
                 ("C 公司（高价项全型）", "company_c.csv")]
        quotes = [P.build_line_index(
            P.load_quote(os.path.join(DATA, "samples", fn), lb), onto) for lb, fn in files]
        full = P.audit(quotes, onto, tpl, rules, cov, template_key="old_house")
        target = quotes[1]
        return inquiry.build(target, full[target["label"]],
                             cross_unit=full.get("_cross_unit")), full

    def test_sections_cover_all_finding_types(self):
        inq = self._build()
        titles = " ".join(s["title"] for s in inq["sections"])
        for expect in ("请补齐或说明的工序", "重复计价", "算术与合计", "开口条款"):
            self.assertIn(expect, titles)
        self.assertGreater(inq["meta"]["n_items"], 0)

        # 多单：问询单要能问出「贵司按根、别家按米」——单份问询单里永远没有这一节
        multi, full = self._build_multi()
        self.assertTrue(full.get("_cross_unit"), "样例三份应当存在跨单口径差异")
        sec = [s for s in multi["sections"] if "计量口径" in s["title"]]
        self.assertTrue(sec, "有跨单口径差异时必须有计量口径一节")
        asks = " ".join(it["ask"] for it in sec[0]["items"])
        self.assertIn("折算", asks)
        self.assertTrue(any("按" in it["basis"] or "行" in it["basis"] for it in sec[0]["items"]))

        # 编号必须连续：多一节、或某一节为空，都不许出现「三、」后面直接跟「五、」
        heads = [s["title"].split("、", 1)[0] for s in multi["sections"]]
        self.assertEqual(heads, list("一二三四五")[:len(heads)])

    def test_every_item_has_ask_and_basis(self):
        inq = self._build()
        for s in inq["sections"]:
            for it in s["items"]:
                self.assertTrue(it["ask"], it["subject"])
                self.assertIsInstance(it["basis"], str)

    def test_priced_and_process_missing_asked_differently(self):
        """能估价的问「补多少钱」，不能估价的问「做不做、留什么记录」——不能一句话问到底。"""
        inq = self._build()
        sec = [s for s in inq["sections"] if "补齐" in s["title"]][0]
        asks = {it["ask"] for it in sec["items"]}
        self.assertGreaterEqual(len(asks), 2, "工艺项与计价项应使用不同问法")
        self.assertTrue(any("补充单价" in a for a in asks))
        self.assertTrue(any("过程记录" in a for a in asks))

    def test_priced_items_come_first(self):
        inq = self._build()
        sec = [s for s in inq["sections"] if "补齐" in s["title"]][0]
        priced = [i for i, it in enumerate(sec["items"]) if "补充单价" in it["ask"]]
        process = [i for i, it in enumerate(sec["items"]) if "过程记录" in it["ask"]]
        if priced and process:
            self.assertLess(max(priced), min(process))

    def test_renders_markdown_and_html(self):
        inq = self._build()
        md = inquiry.to_markdown(inq)
        html = inquiry.to_html(inq)
        self.assertIn("# 报价问询单", md)
        self.assertIn("不评价价格高低", md)
        self.assertTrue(html.startswith("<!DOCTYPE html>"))
        self.assertIn("问询单", html)

    def test_disclaimer_present(self):
        """问询单是去问事实，不是去指控——免责边界必须写在文件里。"""
        md = inquiry.to_markdown(self._build())
        self.assertIn("不评价施工质量", md)


class TestAssets(unittest.TestCase):
    def test_svgs_are_valid_xml(self):
        for name in ("architecture.svg", "decision-flow.svg"):
            path = os.path.join(ROOT, "docs", "assets", name)
            self.assertTrue(os.path.isfile(path), name)
            ET.parse(path)   # 解析失败会抛异常


if __name__ == "__main__":
    unittest.main()
