# -*- coding: utf-8 -*-
"""跨环境判定确定性矩阵的门禁测试：判据不许退化成橡皮图章。"""

import importlib.util
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "determinism_matrix", ROOT / "eval" / "determinism_matrix.py")
DM = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DM)


class TestSelfTest(unittest.TestCase):
    def test_self_test_passes(self):
        st = DM.self_test()
        self.assertEqual(st["status"], "PASS", st["cases"])
        self.assertTrue(all(c["ok"] for c in st["cases"]))

    def test_self_test_covers_eight_cases(self):
        st = DM.self_test()
        self.assertEqual(len(st["cases"]), 8)  # 1 正向 + 7 故障
        self.assertEqual(st["cases"][0]["expected"], "PASS")
        self.assertTrue(all(c["expected"] == "FAIL" for c in st["cases"][1:]))


class TestEvaluate(unittest.TestCase):
    def setUp(self):
        base = {"rc": 0, "digest": "52bf7b635204fd96", "verdict": "pass",
                "missing_cost": 618.4, "duplicate_cut": 182.0,
                "rows": 123, "total": 153384.0}
        self.records = {i: dict(base, id=i) for i in DM.EXPECTED_IDS}
        self.archive = {"schema": DM.ARCHIVE_SCHEMA,
                        "reference": {"machine": "arm64", "python": "3.13.12"},
                        "declares": list(DM.REQUIRED_DECLARES), **base}

    def test_clean_passes(self):
        rep = DM.evaluate(self.records, self.archive)
        self.assertEqual(rep["status"], "PASS", rep["violations"])
        self.assertEqual(rep["checks"], len(DM.EXPECTED_IDS) * len(DM.FIELDS) * 2)

    def test_archive_without_kunpeng_boundary_fails(self):
        self.archive["declares"] = ["openEuler 未实测", "昇腾 NPU 未涉及"]
        rep = DM.evaluate(self.records, self.archive)
        self.assertEqual(rep["status"], "FAIL")
        self.assertTrue(any("鲲鹏实例未实测" in x for x in rep["violations"]))

    def test_digest_drift_fails(self):
        self.records["seed_random"]["digest"] = "deadbeefdeadbeef"
        self.assertEqual(DM.evaluate(self.records, self.archive)["status"], "FAIL")

    def test_missing_archive_fails(self):
        self.assertEqual(DM.evaluate(self.records, None)["status"], "FAIL")

    def test_missing_variant_fails(self):
        del self.records["no_site_packages"]
        self.assertEqual(DM.evaluate(self.records, self.archive)["status"], "FAIL")

    def test_archive_must_come_from_arm64(self):
        self.archive["reference"]["machine"] = "x86_64"
        self.assertEqual(DM.evaluate(self.records, self.archive)["status"], "FAIL")


class TestArchiveAndWiring(unittest.TestCase):
    def test_committed_archive_is_arm64_and_declares_boundaries(self):
        a = json.loads((ROOT / "data" / "determinism_arm64_v0.json").read_text(
            encoding="utf-8"))
        self.assertEqual(a["schema"], DM.ARCHIVE_SCHEMA)
        self.assertEqual(a["reference"]["machine"], "arm64")
        self.assertEqual(a["digest"], "52bf7b635204fd96")
        for d in DM.REQUIRED_DECLARES:
            self.assertIn(d, a["declares"])

    def test_variants_cover_the_known_divergence_causes(self):
        ids = set(DM.EXPECTED_IDS)
        for required in ("seed_0", "seed_1", "seed_random", "tz_utc",
                         "tz_shanghai", "locale_c", "locale_zh", "utf8_off",
                         "optimize_2", "fake_cloud_deps", "no_site_packages"):
            self.assertIn(required, ids)
        self.assertEqual(len(DM.EXPECTED_IDS), 12)

    def test_capture_refuses_non_arm64(self):
        with mock.patch.object(DM, "current_env",
                               return_value={"machine": "x86_64",
                                             "platform": "x", "python": "3.12"}):
            self.assertEqual(DM.capture(), 2)

    def test_check_all_has_step_18(self):
        sh = (ROOT / "scripts" / "check_all.sh").read_text(encoding="utf-8")
        self.assertIn("18/18 跨环境判定确定性", sh)
        self.assertIn("determinism_matrix.py", sh)

    def test_ci_has_python_version_matrix_in_empty_venv(self):
        ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("determinism:", ci)
        self.assertIn("python-version:", ci)
        self.assertIn("python -m venv", ci)
        # 刻意不装 requirements，才证明判定链路零第三方包
        self.assertNotIn("pip install -r requirements.txt", ci.split("determinism:")[1])


class TestRealRun(unittest.TestCase):
    def test_real_matrix_passes(self):
        p = subprocess.run([sys.executable, "eval/determinism_matrix.py",
                            "--self-test", "--json", "--no-report"],
                           cwd=str(ROOT), capture_output=True, text=True,
                           env=dict(os.environ))
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        d = json.loads(p.stdout.strip().splitlines()[-1])
        self.assertEqual(d["status"], "PASS")
        self.assertEqual(d["variants"], 12)
        self.assertEqual(d["self_test"], "PASS")


if __name__ == "__main__":
    unittest.main()
