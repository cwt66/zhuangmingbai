# -*- coding: utf-8 -*-
"""哈希链账本与审计包的三层校验。

这些测试锁的是 2026-11-09 那次真实失守：
上一版 verify.py 只校验输入哈希，改标准后照样 PASS。
所以这里最重要的一条是 `test_bundle_blocks_standards_tamper`——
它不是"设想中的攻击"，是当时真的跑通过的那一招。
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
import ledger as L  # noqa: E402


class TestChain(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="zmb_ledger_test_")
        self.lp = os.path.join(self.tmp, "ledger.jsonl")
        for i in range(1, 4):
            L.append(self.lp, "audit", {"label": "d%d" % i, "result_digest": "x%d" % i},
                     "2026-11-09T00:00:0%d+08:00" % i)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _entries(self):
        return L.load(self.lp)

    def test_clean_chain_passes(self):
        ok, v = L.verify(self._entries())
        self.assertTrue(ok, v)

    def test_gap_on_first_entry(self):
        e, is_new = L.append(self.lp, "audit", {"label": "d4"}, "t")
        self.assertTrue(is_new)
        self.assertEqual(e["seq"], 4)
        self.assertEqual(e["prev"], self._entries()[2]["hash"])

    def test_tamper_payload_detected(self):
        es = self._entries()
        es[1]["payload"]["result_digest"] = "TAMPERED"
        ok, v = L.verify(es)
        self.assertFalse(ok)
        self.assertTrue(any("被改" in x for x in v))

    def test_delete_middle_detected(self):
        es = self._entries()
        del es[1]
        ok, v = L.verify(es)
        self.assertFalse(ok)
        self.assertTrue(any("断号" in x or "断链" in x for x in v))

    def test_reorder_detected(self):
        es = self._entries()
        es[0], es[1] = es[1], es[0]
        ok, _ = L.verify(es)
        self.assertFalse(ok)

    def test_truncate_only_caught_by_anchor(self):
        es = self._entries()[:-1]
        ok, _ = L.verify(es)
        self.assertTrue(ok, "截尾后链内仍然自洽——这正是需要锚点的原因")
        anchor = {"seq": 3, "head": self._entries()[2]["hash"]}
        ok2, v2 = L.check_anchor(es, anchor)
        self.assertFalse(ok2)
        self.assertTrue(any("截断" in x for x in v2))

    def test_rewrite_all_caught_by_anchor(self):
        es = self._entries()
        prev, new = L.GENESIS, []
        for i, e in enumerate(es, 1):
            e2 = L.make_entry(i, e["ts"], e["kind"],
                              {"label": "rewritten", "result_digest": "y%d" % i}, prev)
            new.append(e2)
            prev = e2["hash"]
        ok, _ = L.verify(new)
        self.assertTrue(ok)
        anchor = {"seq": 3, "head": es[2]["hash"]}
        ok2, v2 = L.check_anchor(new, anchor)
        self.assertFalse(ok2)
        self.assertTrue(any("锚点不符" in x for x in v2))

    def test_missing_anchor_fails(self):
        ok, v = L.check_anchor(self._entries(), None)
        self.assertFalse(ok)
        self.assertTrue(any("缺锚点" in x for x in v))

    def test_stale_anchor_fails(self):
        anchor = {"seq": 2, "head": self._entries()[1]["hash"]}
        ok, v = L.check_anchor(self._entries(), anchor)
        self.assertFalse(ok)
        self.assertTrue(any("落后" in x for x in v))

    def test_tamper_dedupe_key_detected(self):
        """2026-11-09 的真实缺陷回归：dedupe_key 曾游离在哈希之外，
        改身份链照样绿。修复后必须在哈希覆盖内。"""
        es = self._entries()
        # setUp 里 append 没传 dedupe_key → 先造一条带身份的
        L.append(self.lp, "audit", {"label": "with-id", "result_digest": "z"},
                 "t", dedupe_key="ID-1")
        es = self._entries()
        es[-1]["dedupe_key"] = "FORGED-IDENTITY"
        ok, v = L.verify(es)
        self.assertFalse(ok, "改 dedupe_key 必须被链发现")
        self.assertTrue(any("被改" in x for x in v))

    def test_canonical_is_order_independent(self):
        a = L.canonical({"b": 1, "a": [2, {"d": 3, "c": 4}]})
        b = L.canonical({"a": [2, {"c": 4, "d": 3}], "b": 1})
        self.assertEqual(a, b)

    def test_dedupe_ignores_label(self):
        """同一份内容换显示名，不该在链上多一条。"""
        e1, new1 = L.append(self.lp, "audit", {"label": "甲", "result_digest": "z"},
                            "t", dedupe_key="ID1")
        e2, new2 = L.append(self.lp, "audit", {"label": "乙", "result_digest": "z"},
                            "t", dedupe_key="ID1")
        self.assertTrue(new1)
        self.assertFalse(new2)
        self.assertEqual(e1["seq"], e2["seq"])
        self.assertEqual(len(self._entries()), 4)


class TestBundlePipeline(unittest.TestCase):
    """打一个真包，验证「改标准」一定被拦。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="zmb_bundle_test_")
        cls.lp = os.path.join(cls.tmp, "ledger.jsonl")
        cls.ap = os.path.join(cls.tmp, "anchor.json")
        cls.zp = os.path.join(cls.tmp, "b.zmb.zip")
        r = subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", "make_bundle.py"),
             "--csv", os.path.join(ROOT, "data", "real", "feitai_budget.csv"),
             "--label", "测试", "--out", cls.zp,
             "--ledger", cls.lp, "--anchor-file", cls.ap],
            cwd=ROOT, capture_output=True, text=True)
        assert r.returncode == 0, r.stderr

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _extract(self):
        d = tempfile.mkdtemp(prefix="zmb_bundle_x_", dir=self.tmp)
        with zipfile.ZipFile(self.zp) as z:
            z.extractall(d)
        return d

    def _verify(self, d):
        return subprocess.run([sys.executable, "verify.py", "--quiet"],
                              cwd=d, capture_output=True, text=True)

    def test_bundle_contains_ledger_files(self):
        with zipfile.ZipFile(self.zp) as z:
            names = set(z.namelist())
        for n in ("ledger.jsonl", "ledger_entry.json", "anchor_snapshot.json",
                  "result.json", "verify.py"):
            self.assertIn(n, names)

    def test_clean_bundle_passes(self):
        d = self._extract()
        r = self._verify(d)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_bundle_blocks_standards_tamper(self):
        """上一版失守的那一招：删必做项 + 重算 digest + 写回。"""
        d = self._extract()
        p = os.path.join(d, "standards", "templates_v0.json")
        data = json.load(open(p, encoding="utf-8"))
        data["old_house"] = [x for x in data["old_house"]
                             if x not in ("protect_door", "water_test", "material_lift")]
        json.dump(data, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        r = self._verify(d)
        self.assertNotEqual(r.returncode, 0, "改标准必须被拦住")
        self.assertIn("templates_v0.json", r.stdout)

    def test_bundle_blocks_code_tamper(self):
        d = self._extract()
        p = os.path.join(d, "src", "pipeline.py")
        with open(p, "a", encoding="utf-8") as f:
            f.write("\n# tampered\n")
        r = self._verify(d)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("pipeline.py", r.stdout)

    def test_bundle_blocks_missing_standard(self):
        d = self._extract()
        os.remove(os.path.join(d, "standards", "rules_v0.json"))
        r = self._verify(d)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("缺文件", r.stdout)

    def test_l2_passes_clean_bundle(self):
        r = subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", "verify_bundle.py"),
             "--zip", self.zp, "--ledger", self.lp, "--anchor-file", self.ap, "--json"],
            cwd=ROOT, capture_output=True, text=True)
        rep = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual(rep["status"], "PASS", rep["violations"])

    def test_l2_catches_bundle_from_other_chain(self):
        """拿另一个账本打的包，在 L2 上必须被识别为「不在链上」。"""
        other_lp = os.path.join(self.tmp, "other.jsonl")
        other_ap = os.path.join(self.tmp, "other_anchor.json")
        L.append(other_lp, "audit", {"label": "别的", "result_digest": "q"},
                 "2026-11-09T00:00:00+08:00")
        r = subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", "verify_bundle.py"),
             "--zip", self.zp, "--ledger", other_lp, "--anchor-file", other_ap, "--json"],
            cwd=ROOT, capture_output=True, text=True)
        rep = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual(rep["status"], "FAIL")
        self.assertTrue(any("找不到" in x or "payload 与仓库账本" in x
                            for x in rep["violations"]))


if __name__ == "__main__":
    unittest.main()
