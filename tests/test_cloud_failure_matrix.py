# -*- coding: utf-8 -*-
"""给「华为云失效矩阵」本身写测试：这套判据不能退化成橡皮图章。

矩阵文档此前只是文档。现在每一行都对应一次故障注入，
那判据自己就得和 rule_liveness / claim_ledger 一样被查：
干净必须放行、撒谎必须被拒、全挂时指纹必须逐字节不变。
"""
import importlib.util
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC = importlib.util.spec_from_file_location(
    "_cloud_failure_matrix", os.path.join(ROOT, "eval", "cloud_failure_matrix.py"))
CF = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CF)


class CloudFailureMatrixTests(unittest.TestCase):
    def test_all_experiments_pass(self):
        rep = CF.run_all()
        self.assertEqual(rep["status"], "PASS",
                         [r for r in rep["rows"] if not r["ok"]])
        self.assertEqual(len(rep["rows"]), 9)

    def test_self_test_is_bidirectional(self):
        st = CF.self_test()
        self.assertEqual(st["status"], "PASS",
                         [c for c in st["cases"] if not c["ok"]])
        self.assertEqual(len(st["cases"]), 5)
        self.assertEqual(st["cases"][0]["expected"], "PASS")
        for c in st["cases"][1:]:
            self.assertEqual(c["expected"], "FAIL")

    def test_health_check_rejects_lies(self):
        actual = {"db": "sqlite", "cache": "memory"}
        clean = {"db": {"kind": "sqlite"}, "cache": {"kind": "memory"}}
        self.assertTrue(CF.assert_truthful_health(clean, actual))
        lie_db = {"db": {"kind": "postgres"}, "cache": {"kind": "memory"}}
        self.assertFalse(CF.assert_truthful_health(lie_db, actual))
        lie_cache = {"db": {"kind": "sqlite"}, "cache": {"kind": "redis"}}
        self.assertFalse(CF.assert_truthful_health(lie_cache, actual))

    def test_engine_has_no_network_imports(self):
        ok, observed, _ = CF.exp_ecs()
        self.assertTrue(ok, observed)

    def test_perception_failure_is_loud(self):
        for fn in (CF.exp_maas_vision, CF.exp_ocr_table):
            with self.subTest(fn=fn.__name__):
                ok, observed, _ = fn()
                self.assertTrue(ok, observed)

    def test_all_down_digest_is_byte_identical(self):
        ok, observed, _ = CF.exp_all_down_determinism()
        self.assertTrue(ok, observed)


if __name__ == "__main__":
    unittest.main()
