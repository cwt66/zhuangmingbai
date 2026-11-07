# -*- coding: utf-8 -*-
"""锁住「干净 clone 复验」这套保障不会悄悄消失。

完整复验要 1 分钟，由 CI 的独立 job 跑；这里只锁静态不变量：
job 还在、check_all 不会递归调它、.gitignore 的白名单还在、护栏会拒绝递归。
"""
import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC = importlib.util.spec_from_file_location(
    "_verify_clean_clone", os.path.join(ROOT, "scripts", "verify_clean_clone.py"))
VC = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VC)


class CleanCloneGuardTests(unittest.TestCase):
    def test_self_test_passes(self):
        st = VC.self_test()
        self.assertEqual(st["status"], "PASS",
                         [c for c in st["cases"] if not c["ok"]])
        self.assertTrue(all(c["ok"] for c in st["cases"]))
        self.assertGreaterEqual(len(st["cases"]), 5)

    def test_ci_has_second_job(self):
        ci = Path(ROOT, ".github", "workflows", "ci.yml").read_text(encoding="utf-8")
        self.assertIn("clean-clone:", ci)
        self.assertIn("verify_clean_clone.py", ci)
        self.assertIn("clean-clone-evidence", ci)

    def test_check_all_does_not_call_it(self):
        sh = Path(ROOT, "scripts", "check_all.sh").read_text(encoding="utf-8")
        self.assertNotIn("verify_clean_clone", sh)

    def test_guard_refuses_recursion(self):
        env = dict(os.environ)
        env[VC.GUARD] = "1"
        r = VC.subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", "verify_clean_clone.py"), "--json"],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 2)
        self.assertEqual(json.loads(r.stdout.strip())["status"], "FAIL")


if __name__ == "__main__":
    unittest.main()
