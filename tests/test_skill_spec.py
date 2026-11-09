# -*- coding: utf-8 -*-
"""锁住「项目 Skill 符合华为云官方 Agent Skills 规范」这句话本身。

规范符合性最容易变成一句没人核过的宣传语：写一份看起来像官方模板的
SKILL.md，然后宣称「对齐了」。这个测试要求：
1. 20 项验收逐条有结果（结构 + 文档 + 功能层）；
2. 干净包必须全绿，六种故障必须全红（双向断言）；
3. 偏离要被声明，不许靠伪造 CLI/IAM 文档混过去。
"""
import copy
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import verify_skill_spec as VS  # noqa: E402


class SkillSpecTests(unittest.TestCase):
    def setUp(self):
        self.skill = VS.load_skill()

    def test_clean_skill_passes_all_checks(self):
        rep = VS.evaluate(self.skill)
        self.assertEqual(rep["status"], "PASS",
                         [c for c in rep["checks"] if not c["ok"]])
        self.assertEqual(rep["total"], 20)
        self.assertEqual(rep["passed"], 20)

    def test_required_reference_docs_exist(self):
        paths = {f["path"] for f in self.skill["files"]}
        for ref in VS.REQUIRED_REFERENCES:
            self.assertIn("references/" + ref, paths)

    def test_frontmatter_has_no_version_and_name_matches_dir(self):
        fm = self.skill["frontmatter"]
        self.assertNotIn("version", fm)
        self.assertEqual(fm["name"], self.skill["dir"])
        self.assertTrue(all(k in fm for k in ("name", "description", "tags")))

    def test_deviations_are_declared_not_faked(self):
        self.assertIn("偏离声明", self.skill["text"])
        self.assertIn("skill-quality-cli", self.skill["text"])
        self.assertIn("不调用任何云 API", self.skill["text"])

    def _mutated(self, fn):
        m = copy.deepcopy(self.skill)
        fn(m)
        return m

    def test_fault_injections_all_fail(self):
        mutations = [
            lambda m: m["frontmatter"].update({"version": "1.0.0"}),
            lambda m: m["frontmatter"].update({"name": "wrong-name"}),
            lambda m: m.update({"files": [f for f in m["files"]
                                          if not f["path"].endswith("iam-policies.md")]}),
            lambda m: m.update({"all_text": m["all_text"] +
                                "\nAK = ABCDEFGHIJKLMNOP123456\n"}),
            lambda m: m.update({"text": m["text"].replace("偏离声明", "（删掉了）")}),
            lambda m: m.update({"all_text": m["all_text"] +
                                "\npython3 scripts/not_exist_tool.py --json\n"}),
            lambda m: m.update({"all_text": m["all_text"] +
                                "\n详见 docs/不存在的文档.md\n"}),
            lambda m: m.update({"lines": VS.MAX_LINES + 1}),
        ]
        for fn in mutations:
            with self.subTest(fn=fn):
                self.assertEqual(VS.evaluate(self._mutated(fn))["status"], "FAIL")

    def test_self_test_is_bidirectional(self):
        st = VS.self_test(self.skill)
        self.assertEqual(st["status"], "PASS")
        self.assertTrue(all(c["ok"] for c in st["cases"]))
        self.assertEqual(st["cases"][0]["expected"], "PASS")
        for c in st["cases"][1:]:
            self.assertEqual(c["expected"], "FAIL")

    def test_json_contract_for_claim_ledger_probe(self):
        """台账的 C13 探针要能解析 --json 的最后一行。"""
        import json
        import subprocess
        r = subprocess.run([sys.executable, "scripts/verify_skill_spec.py", "--json"],
                           cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        payload = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual(payload["status"], "PASS")


if __name__ == "__main__":
    unittest.main()
