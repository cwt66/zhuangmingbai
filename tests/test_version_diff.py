# -*- coding: utf-8 -*-
"""两版报价对比的锁：配对要靠本体，差额要能对上账。

背景：业主最常问的第二句话是「你上次报 15 万，这次怎么 17 万，多在哪」。
这一层如果只按文本 diff，会把「拆墙 / 墙体拆除」当成两行；如果只比总价，
又说不出多在哪。所以这里锁四件事：

1. **同义叫法必须配成一对**（靠 cid，不靠字面）；
2. **增 − 减 + 同项调整 = 账面差额**，一分不差；
3. **单位变了不比金额**（m → ㎡ 单列），避免把量纲错误算成涨价；
4. 同一概念多行时按行序配对，不把「两行防水」揉成一行。
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import version_diff as V  # noqa: E402


def _row(no, name, unit, qty, price, amount, cid=None):
    return {"no": str(no), "name": name, "spec": "", "unit": unit,
            "qty": qty, "price": price, "amount": amount, "note": "", "cid": cid}


def _quote(label, rows):
    return {"label": label, "file": label + ".csv", "rows": rows,
            "summary_rows": [], "total": sum(r["amount"] for r in rows)}


class TestVersionDiff(unittest.TestCase):
    def test_add_remove_change_adds_up(self):
        q1 = _quote("v1", [_row(1, "拆墙", "㎡", 10, 50, 500, "demo_wall"),
                           _row(2, "防水", "㎡", 20, 80, 1600, "waterproof"),
                           _row(3, "墙面粉刷", "㎡", 100, 30, 3000)])
        q2 = _quote("v2", [_row(1, "拆墙", "㎡", 12, 50, 600, "demo_wall"),
                           _row(2, "防水", "㎡", 20, 90, 1800, "waterproof"),
                           _row(3, "瓷砖铺贴", "㎡", 40, 120, 4800, "tile_wall")])
        d = V.diff(q1, q2)
        s = d["summary"]
        self.assertEqual(s["counts"], {"added": 1, "removed": 1, "changed": 2,
                                       "unchanged": 0, "unit_mismatch": 0})
        self.assertEqual(s["added_amount"], 4800)
        self.assertEqual(s["removed_amount"], 3000)
        self.assertEqual(s["changed_delta"], 300)
        self.assertEqual(s["delta"], 2100)
        self.assertAlmostEqual(s["unexplained"], 0.0, places=6)

    def test_aliases_match_by_concept(self):
        """「拆墙」与「墙体拆除」是同一概念，不能算成一增一减。"""
        q1 = _quote("v1", [_row(1, "拆墙", "㎡", 10, 50, 500, "demo_wall")])
        q2 = _quote("v2", [_row(1, "墙体拆除", "㎡", 10, 50, 500, "demo_wall")])
        d = V.diff(q1, q2)
        self.assertEqual(d["added"], [])
        self.assertEqual(d["removed"], [])
        self.assertEqual(d["changed"], [])
        self.assertEqual(d["summary"]["counts"]["unchanged"], 1)

    def test_name_fallback_when_no_cid(self):
        """没有 cid 时退回规范化名称：全角/空格/括号差异不应拆成两条。"""
        q1 = _quote("v1", [_row(1, "成品保护（门）", "项", 1, 200, 200, None)])
        q2 = _quote("v2", [_row(1, "成品保护(门)", "项", 2, 200, 400, None)])
        d = V.diff(q1, q2)
        self.assertEqual(len(d["changed"]), 1)
        self.assertEqual(d["summary"]["changed_delta"], 200)

    def test_unit_change_not_netted(self):
        """单位从 m 变 ㎡：金额不可直接相减，必须单列。"""
        q1 = _quote("v1", [_row(1, "踢脚线", "m", 10, 30, 300, "skirting")])
        q2 = _quote("v2", [_row(1, "踢脚线", "㎡", 10, 30, 300, "skirting")])
        d = V.diff(q1, q2)
        self.assertEqual(d["summary"]["counts"]["unit_mismatch"], 1)
        self.assertEqual(d["summary"]["changed_delta"], 0)
        self.assertEqual(d["summary"]["unexplained"], 0)

    def test_duplicate_concept_pairs_by_order(self):
        """同一概念两行：按行序配对，不能揉成一行。"""
        q1 = _quote("v1", [_row(1, "防水", "㎡", 10, 80, 800, "waterproof"),
                           _row(2, "防水", "㎡", 20, 80, 1600, "waterproof")])
        q2 = _quote("v2", [_row(1, "防水", "㎡", 10, 90, 900, "waterproof"),
                           _row(2, "防水", "㎡", 20, 90, 1800, "waterproof")])
        d = V.diff(q1, q2)
        self.assertEqual(len(d["changed"]), 2)
        self.assertEqual(d["summary"]["changed_delta"], 300)
        self.assertEqual(d["added"], [])

    def test_identical_quotes_zero_delta(self):
        rows = [_row(1, "拆墙", "㎡", 10, 50, 500, "demo_wall")]
        d = V.diff(_quote("v1", list(rows)), _quote("v2", list(rows)))
        self.assertEqual(d["summary"]["delta"], 0)
        self.assertEqual(d["summary"]["change_rate"], 0)
        self.assertEqual(d["summary"]["counts"]["unchanged"], 1)

    def test_rates(self):
        q1 = _quote("v1", [_row(1, "拆墙", "㎡", 10, 50, 500, "demo_wall")])
        q2 = _quote("v2", [_row(1, "拆墙", "㎡", 10, 60, 600, "demo_wall")])
        s = V.diff(q1, q2)["summary"]
        self.assertAlmostEqual(s["net_rate"], 0.2, places=4)     # 净涨 20%
        self.assertAlmostEqual(s["change_rate"], 0.2, places=4)  # 变动幅度 20%

    def test_html_renders_css_percent(self):
        """CSS 里的 `width:100%` 曾把 %-模板打断（ValueError），锁住它。"""
        q1 = _quote("v1", [_row(1, "拆墙", "㎡", 10, 50, 500, "demo_wall")])
        q2 = _quote("v2", [_row(1, "拆墙", "㎡", 12, 50, 600, "demo_wall")])
        html = V.to_html(V.diff(q1, q2), banner="演示用")
        self.assertIn("width:100%", html)
        self.assertIn("+100.00", html)
        self.assertIn("演示用", html)

    def test_html_escapes_names(self):
        q1 = _quote("v1", [])
        q2 = _quote("v2", [_row(1, "<script>x</script>", "项", 1, 1, 1, None)])
        html = V.to_html(V.diff(q1, q2))
        self.assertNotIn("<script>x</script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_markdown_has_scope_and_numbers(self):
        q1 = _quote("v1", [_row(1, "拆墙", "㎡", 10, 50, 500, "demo_wall")])
        q2 = _quote("v2", [_row(1, "拆墙", "㎡", 12, 50, 600, "demo_wall")])
        md = V.to_markdown(V.diff(q1, q2))
        self.assertIn("两版报价对比", md)
        self.assertIn("不做跨公司比价", md)
        self.assertIn("100.00", md)


if __name__ == "__main__":
    unittest.main()
