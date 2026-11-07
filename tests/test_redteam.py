# -*- coding: utf-8 -*-
"""红队结果的锁：8 招里有 7 招必须被拦住，剩下 1 招要**明确承认**它拦不住。

## 为什么失败也要写进测试

A3（㎡ 写成 m）我们没有修，因为 `m` 是合法单位（延米），强行判会误伤。
把「拦不住」写成一条**断言拦不住**的测试，有两个作用：

1. 防止有人好心办坏事——为了让 A3 变绿而加一条粗暴规则，会打出成片误报；
2. 它是一条诚实的记录：**这个边界我们知道，且知道为什么先不补**。

一条「已知拦不住」的测试，比一条删除掉的失败用例诚实得多。
"""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "eval"))
import pipeline as P  # noqa: E402
import redteam as RT  # noqa: E402

DATA = os.path.join(ROOT, "data")


class _Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env = RT._env()


class TestBlocked(_Base):
    """7 招必须被拦住。任何一条变红，说明有人把防线拆了。"""

    def test_A1_synonym_duplicate(self):
        """换个近义名再列一次 → 要归到同一概念，并判重复。"""
        verdict, detail = RT.a1_synonym_duplicate(self.env)
        self.assertEqual(verdict, "拦住", detail)
        self.assertIn("重复结论 1 条", detail)

    def test_A2_alias_noise(self):
        """空格 + 全角括号 + 「（同款）」→ 仍要判重复。"""
        verdict, detail = RT.a2_alias_with_space(self.env)
        self.assertEqual(verdict, "拦住", detail)

    def test_A4_qty_without_price(self):
        """有工程量、单价 0 → R9 必须报出来。"""
        verdict, detail = RT.a4_qty_without_price(self.env)
        self.assertEqual(verdict, "拦住", detail)
        self.assertIn("R9_qty_unit", detail)

    def test_A5_hidden_in_summary(self):
        """漏项伪装成表尾「××处理费」→ 不许误判成漏项。"""
        verdict, _ = RT.a5_hidden_in_summary(self.env)
        self.assertEqual(verdict, "拦住")

    def test_A6_missing_in_note(self):
        """漏项只写在备注 → 第二路证据词要救回来。"""
        verdict, _ = RT.a6_missing_in_note(self.env)
        self.assertEqual(verdict, "拦住")

    def test_A7_split_discount(self):
        """优惠拆负数+正数两行 → 对账仍要闭合。"""
        verdict, detail = RT.a7_split_discount(self.env)
        self.assertEqual(verdict, "拦住", detail)

    def test_A8_zero_qty_nonzero_amount(self):
        """数量 0 合价 800 → 必须报。"""
        verdict, detail = RT.a8_zero_qty_nonzero_amount(self.env)
        self.assertEqual(verdict, "拦住", detail)


class TestKnownGap(_Base):
    """A3 是**已知拦不住**的。这里断言它拦不住，而不是假装它被拦住了。"""

    def test_A3_unit_switch_is_known_gap(self):
        """`㎡` 写成 `m` 单份单据里不报——因为 m 是合法单位（延米）。

        要判它需要「工序 → 标准单位」的知识表，成本高且容易误伤
        （有些工序确实可以按延米报）。所以先记为边界，写在 negative KB 的 NA13。
        """
        verdict, detail = RT.a3_unit_switch(self.env)
        self.assertEqual(verdict, "得手", detail)
        self.assertIn("无", detail)


class TestConfusable(unittest.TestCase):
    """易混词表本身的锁。"""

    def test_confusable_table_loaded(self):
        self.assertGreaterEqual(len(P.CONFUSABLE_PAIRS), 10, "易混词表没加载到")

    def test_tile_floor_not_misread_as_drain(self):
        """『地砖安装』不许被编辑距离判成『地漏安装』——这是红队 A1 的原始 bug。"""
        onto = P.Ontology(os.path.join(DATA, "ontology_v0.json"))
        cid, conf, how = onto.align("地砖安装")
        self.assertEqual(cid, "tile_floor", "被误认为：%s（%s）" % (cid, how))

    def test_opposite_pairs_still_distinguished(self):
        """易混词不能把真正不同的工序也归一了。"""
        onto = P.Ontology(os.path.join(DATA, "ontology_v0.json"))
        self.assertEqual(onto.align("地漏安装")[0], "floor_drain")
        self.assertEqual(onto.align("墙面涂料")[0], "topcoat")
        self.assertEqual(onto.align("顶面涂料")[0], "topcoat")

    def test_zhaoping_vs_zhaopo(self):
        """找平与找坡只差一字，必须分开。"""
        onto = P.Ontology(os.path.join(DATA, "ontology_v0.json"))
        a = onto.align("地面找平")[0]
        b = onto.align("地面找坡")[0]
        self.assertNotEqual(a, b, "找平与找坡被判成同一概念")

    def test_variant_residual_strips_trade_verbs(self):
        """剥掉施工动词后，同一工序的不同说法残差相等。"""
        onto = P.Ontology(os.path.join(DATA, "ontology_v0.json"))
        r1 = P._variant_residual(onto, {"name": "地砖铺贴", "cid": "tile_floor"})
        r2 = P._variant_residual(onto, {"name": "地砖安装", "cid": "tile_floor"})
        self.assertEqual(r1, r2)

    def test_variant_residual_keeps_location(self):
        """部位词必须留下——否则顶面和墙面会被判成重复。"""
        onto = P.Ontology(os.path.join(DATA, "ontology_v0.json"))
        r1 = P._variant_residual(onto, {"name": "墙面涂料", "cid": "topcoat"})
        r2 = P._variant_residual(onto, {"name": "顶面涂料", "cid": "topcoat"})
        self.assertNotEqual(r1, r2)

    def test_null_paren_is_stripped(self):
        """「（同款）」不区分工序，要剥掉；「（主卧）」要留下。"""
        onto = P.Ontology(os.path.join(DATA, "ontology_v0.json"))
        a = P._variant_residual(onto, {"name": "地砖铺贴（同款）", "cid": "tile_floor"})
        b = P._variant_residual(onto, {"name": "地砖铺贴", "cid": "tile_floor"})
        self.assertEqual(a, b)
        c = P._variant_residual(onto, {"name": "柜（主卧）", "cid": "cabinet"})
        d = P._variant_residual(onto, {"name": "柜（次卧）", "cid": "cabinet"})
        self.assertNotEqual(c, d)


class TestNoRegression(unittest.TestCase):
    """修红队漏洞不许动摇真实单据的判定。"""

    def test_corpus_exclusions_unchanged(self):
        """全库变体排除仍是 72 组——说明只是把「换说法绕过」堵了，没伤到真实变体。"""
        env = RT._env()
        docs = [("sh_zhuoguan_halfpack.csv", "篱笆", "old_house"),
                ("huaxibeian_newhouse.csv", "花溪", "new_house"),
                ("szxinxin_clinic_pdf.csv", "诊所", "new_house"),
                ("feitai_budget.csv", "翡翠台", "old_house"),
                ("guijiao_124_basic.csv", "124㎡", "new_house")]
        total = 0
        dups = 0
        for f, l, t in docs:
            q = P.build_line_index(P.load_quote(os.path.join(DATA, "real", f), l), env[0])
            r = P.audit([q], env[0], env[1], env[2], env[3], template_key=t)[l]
            total += r["dup_excluded"]
            dups += len(r["duplicates"])
        self.assertEqual(total, 72)
        self.assertEqual(dups, 4)

    def test_gift_rows_still_silent(self):
        """加了「有量无价」之后，赠品仍不许被报（否则误伤促销写法）。"""
        d = tempfile.mkdtemp()
        p = os.path.join(d, "q.csv")
        with open(p, "w", encoding="utf-8-sig") as f:
            f.write("项目名称,单位,数量,单价,合价\n"
                    "地砖铺贴,m2,40,120,4800\n"
                    "（赠送）开关面板,个,20,0,0\n")
        env = RT._env()
        q = P.build_line_index(P.load_quote(p, "赠"), env[0])
        r = P.audit([q], env[0], env[1], env[2], env[3], template_key="old_house")["赠"]
        bad = [x for x in r["risks"] if x["rule"] == "R9_qty_unit" and "开关面板" in (x.get("concept") or "")]
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
