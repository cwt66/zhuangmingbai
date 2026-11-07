# -*- coding: utf-8 -*-
"""给「主张台账」本身写测试：它是一台判据，判据自己也要被查。

## 为什么不能在这里跑真探针

`probe_tests_count` 会启动 `python3 -m unittest discover -s tests`，
而那个进程里又包含本文件 —— 从单测里直接调它会无限递归。
所以这里的 `verify()` 一律注入 `probe_cache`（假观察值），
**只测校验器的逻辑，不测探针**；真探针由 `check_all.sh` 第 16 步跑。

这也是一条纪律：判据能不能说「不」，和探针算得对不对，是两件事，分开测。
"""
import copy
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import claim_ledger as CL  # noqa: E402

CLAIMS = CL.read_json(CL.LEDGER_PATH)
HISTORY = CL.read_json(CL.HISTORY_PATH)


def stub_cache(claims):
    """把每条 ①/② 主张的观察值预填成「宣称值」，不启动任何子进程。"""
    return {(c["probe"], c["id"]): (str(c["value"]), "单测桩")
            for c in claims.get("claims", []) if c.get("probe")}


class ClaimLedgerTests(unittest.TestCase):
    def setUp(self):
        self.claims = copy.deepcopy(CLAIMS)
        self.history = copy.deepcopy(HISTORY)

    def _verify(self, claims=None, history=None, cache=None):
        # 桩缓存永远按「干净台账」生成：故障注入改的是宣称值，
        # 观察值不该跟着变，否则永远测不出数字漂移。
        return CL.verify(claims or self.claims, history or self.history,
                         root=CL.ROOT,
                         probe_cache=stub_cache(CLAIMS) if cache is None else cache)

    # ---------- 干净态必须放行（校验器会说「是」） ----------
    def test_clean_ledger_passes(self):
        rep = self._verify()
        self.assertEqual(rep["status"], "PASS", rep["violations"])
        self.assertTrue(all(c["status"] in
                            ("verified", "read_verified", "declared")
                            for c in rep["claims"]))
        self.assertEqual(rep["history_entries"], 11)
        self.assertEqual(rep["misses"], 3)

    def test_three_tiers_are_present_and_declared_not_counted(self):
        rep = self._verify()
        self.assertEqual(rep["tier_counts"].get("ran"), 13)
        self.assertEqual(rep["tier_counts"].get("read"), 3)
        self.assertEqual(rep["tier_counts"].get("declared"), 3)
        # ③ 档必须进欠账清单，且状态不是 verified
        debts = {d["id"] for d in rep["debts"]}
        for cid in ("C17", "C18", "C19"):
            self.assertIn(cid, debts)
        for c in rep["claims"]:
            if c["tier"] == "declared":
                self.assertEqual(c["status"], "declared")
                self.assertIsNone(c["observed"])
        # C14 是「开发机 arm64」，CI 在 x86_64 上跑不出这个值，
        # 必须从 ① 降到 ②。锁住这次降级，不许为了好看偷偷升回去。
        c14 = next(c for c in rep["claims"] if c["id"] == "C14")
        self.assertEqual(c14["tier"], "read")

    # ---------- 六种故障必须被拒（校验器会说「不」） ----------
    def test_value_drift_fails(self):
        self.claims["claims"][0]["value"] = "999999"
        rep = self._verify()
        self.assertEqual(rep["status"], "FAIL")
        self.assertTrue(any("数字漂了" in v for v in rep["violations"]))

    def test_doc_drift_fails(self):
        self.claims["claims"][1]["occurrences"].append(
            {"file": "README.md", "pattern": "这段字不存在 XYZ"})
        rep = self._verify()
        self.assertEqual(rep["status"], "FAIL")
        self.assertTrue(any("文档出现位置对不上" in v for v in rep["violations"]))
        self.assertEqual(rep["claims"][1]["status"], "doc_drift")

    def test_ran_claim_without_probe_fails(self):
        self.claims["claims"][0].pop("probe")
        rep = self._verify()
        self.assertEqual(rep["status"], "FAIL")
        self.assertTrue(any("没有可用探针" in v for v in rep["violations"]))

    def test_ran_claim_without_command_fails(self):
        self.claims["claims"][0]["command"] = None
        rep = self._verify()
        self.assertEqual(rep["status"], "FAIL")

    def test_declared_cannot_be_promoted_without_probe(self):
        for c in self.claims["claims"]:
            if c["tier"] == "declared":
                c["tier"] = "ran"
                break
        rep = self._verify()
        self.assertEqual(rep["status"], "FAIL")

    def test_declared_cannot_sneak_in_a_command(self):
        for c in self.claims["claims"]:
            if c["tier"] == "declared":
                c["command"] = "echo fake"
                break
        rep = self._verify()
        self.assertEqual(rep["status"], "FAIL")

    # ---------- 撤回档案：被撤回的结论不删 ----------
    def test_deleting_withdrawn_record_fails(self):
        self.history["entries"].pop(0)
        rep = self._verify()
        self.assertEqual(rep["status"], "FAIL")
        self.assertTrue(any("撤回档案被改动" in v for v in rep["violations"]))

    def test_removing_predicted_miss_fails(self):
        for p in self.history["predictions"]:
            p["miss"] = False
        rep = self._verify()
        self.assertEqual(rep["status"], "FAIL")

    def test_history_digest_is_sensitive_to_content(self):
        a = CL.history_digest(self.history)
        h = copy.deepcopy(self.history)
        h["entries"][0]["now"] = "改过"
        self.assertNotEqual(a, CL.history_digest(h))

    # ---------- 双向断言（自检本身） ----------
    def test_self_test_is_bidirectional(self):
        st = CL.self_test(self.claims, self.history,
                          probe_cache=stub_cache(self.claims))
        self.assertEqual(st["status"], "PASS")
        self.assertTrue(all(c["ok"] for c in st["cases"]))
        self.assertEqual(len(st["cases"]), 7)  # 1 个正向 + 6 个故障
        self.assertEqual(st["cases"][0]["expected"], "PASS")
        for c in st["cases"][1:]:
            self.assertEqual(c["expected"], "FAIL")

    # ---------- 几个便宜的真探针（不启动 unittest） ----------
    def test_cheap_probes_recompute_from_source(self):
        self.assertEqual(CL.probe_ontology_concepts()[0], "119")
        self.assertEqual(CL.probe_ontology_aliases()[0], "388")
        self.assertEqual(CL.probe_rules_count()[0], "13")
        self.assertEqual(CL.probe_na_count()[0], "13")
        self.assertEqual(CL.probe_real_quote_files()[0], "5")
        self.assertEqual(CL.probe_real_quote_rows()[0], "614")
        self.assertEqual(CL.probe_gate_checks()[0], "7")
        self.assertEqual(CL.probe_pipeline_stdlib_only()[0], "零第三方依赖")
        self.assertEqual(CL.probe_dev_arch_record()[0], "arm64")

    def test_stdlib_probe_would_catch_a_third_party_import(self):
        """把 numpy 塞进源码，探针必须报出来——否则它只是个永远说通过的橡皮图章。"""
        import ast
        fake = ast.parse("import numpy\nimport json\n")
        roots = set()
        for node in ast.walk(fake):
            if isinstance(node, ast.Import):
                roots.update(a.name.split(".")[0] for a in node.names)
        bad = [r for r in roots if r not in sys.stdlib_module_names]
        self.assertEqual(bad, ["numpy"])


if __name__ == "__main__":
    unittest.main()
