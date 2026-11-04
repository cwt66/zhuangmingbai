# -*- coding: utf-8 -*-
"""盲标工具与一致性的锁。

守三件事：
1. 生成的盲标页面**不能内嵌 A1 的答案**（否则 κ 失去意义）；
2. 页面必须**自包含**（离线可用，不引外部资源）；
3. κ 的算法与拒绝逻辑正确（自比 =1、非法取值必须报错、重名必须拦）。
"""
import contextlib
import csv
import importlib.util
import io
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestAnnotatorGenerator(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load(os.path.join(ROOT, "eval", "make_annotator.py"), "make_annotator")
        cls.tmp = tempfile.mkdtemp(prefix="annotator_")
        cls.path, cls.n_concepts, cls.n_rows = cls.mod.build("feitai", cls.tmp)
        with open(cls.path, encoding="utf-8") as f:
            cls.html = f.read()

    def test_concept_and_row_counts(self):
        self.assertEqual(self.n_concepts, 39)
        self.assertEqual(self.n_rows, 131)

    def test_no_answer_leak(self):
        """页面里不能出现 golden_labels 相关字样，也不能出现「cid + 状态」的成对数据。"""
        self.assertNotIn("golden_labels", self.html)
        # golden_state 作为导出列名是允许的；出现次数应恰好 1（表头那一处）
        self.assertEqual(self.html.count("golden_state"), 1)
        # 逐个 cid 检查：状态词不应紧跟在 cid 附近（防止有人把答案拼进 JSON）
        with open(os.path.join(ROOT, "eval", "golden_labels_feitai.csv"), encoding="utf-8-sig") as f:
            gold = list(csv.DictReader(f))
        for g in gold:
            idx = self.html.find('"%s"' % g["cid"])
            self.assertGreater(idx, 0, g["cid"])
            window = self.html[idx:idx + 220]
            self.assertNotIn('"missing"', window, "%s 附近疑似泄漏答案" % g["cid"])

    def test_self_contained(self):
        for bad in ("http://", "https://", "<script src=", "<link rel=\"stylesheet\""):
            self.assertNotIn(bad, self.html, "盲标页不能依赖外部资源：%s" % bad)

    def test_has_three_states_and_export(self):
        for token in ("'present'", "'missing'", "'not_applicable'", "exportCSV", "Blob"):
            self.assertIn(token, self.html)

    def test_concept_universe_matches_golden(self):
        """页面的概念集合必须与 A1 标注的 cid 集合完全一致，否则 κ 无法对齐。"""
        import json
        import re
        m = re.search(r"const CONCEPTS = (\[.*?\]);\n", self.html, re.S)
        concepts = json.loads(m.group(1).replace("<\\/", "</"))
        with open(os.path.join(ROOT, "eval", "golden_labels_feitai.csv"), encoding="utf-8-sig") as f:
            gold = list(csv.DictReader(f))
        self.assertEqual({c["cid"] for c in concepts}, {g["cid"] for g in gold})


class TestKappa(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load(os.path.join(ROOT, "eval", "cohen_kappa.py"), "cohen_kappa")
        cls.gold_path = os.path.join(ROOT, "eval", "golden_labels_feitai.csv")

    def _write(self, rows, name):
        d = tempfile.mkdtemp(prefix="kappa_")
        p = os.path.join(d, name)
        with open(p, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=["cid", "quote_doc", "golden_state", "annotator", "note"])
            w.writeheader()
            w.writerows(rows)
        return p

    def _gold(self):
        with open(self.gold_path, encoding="utf-8-sig") as f:
            return list(csv.DictReader(f))

    def test_self_comparison_is_perfect(self):
        a = self.mod.load(self.gold_path)
        st = self.mod.kappa(a, a)
        self.assertEqual(st["po"], 1.0)
        self.assertEqual(st["kappa"], 1.0)
        self.assertEqual(st["disagree"], [])

    def test_flip_four_items(self):
        gold = self._gold()
        rows = []
        for i, g in enumerate(gold):
            st = g["golden_state"]
            if i < 4:
                st = "missing" if st == "present" else "present"
            rows.append({"cid": g["cid"], "quote_doc": "feitai", "golden_state": st,
                         "annotator": "B", "note": ""})
        b = self.mod.load(self._write(
            [{k: r[k] for k in ("cid", "quote_doc", "golden_state", "annotator", "note")} for r in rows],
            "feitai.B.labels.csv"))
        st = self.mod.kappa(self.mod.load(self.gold_path), b)
        self.assertAlmostEqual(st["po"], 35 / 39, places=3)
        self.assertEqual(len(st["disagree"]), 4)
        self.assertLess(st["kappa"], 1.0)

    def test_invalid_state_rejected(self):
        gold = self._gold()
        # A1 的标注文件列名是 first_annotator，转成工具的统一 schema
        gold = [{"cid": g["cid"], "quote_doc": g["quote_doc"],
                 "golden_state": g["golden_state"],
                 "annotator": g.get("first_annotator", "A1"),
                 "note": g.get("note", "")} for g in gold]
        gold[0]["golden_state"] = "maybe"
        p = self._write(gold, "feitai.bad.labels.csv")
        with self.assertRaises(SystemExit):
            self.mod.load(p)

    def test_duplicate_doc_guard(self):
        """同一份单据两个回传文件必须报错，不能悄悄算两次。"""
        d = tempfile.mkdtemp(prefix="kappa_dup_")
        gold = self._gold()
        for name in ("feitai.A1.labels.csv", "feitai.A2.labels.csv"):
            with open(os.path.join(d, name), "w", newline="", encoding="utf-8-sig") as f:
                w = csv.DictWriter(f, fieldnames=["cid", "quote_doc", "golden_state", "annotator", "note"])
                w.writeheader()
                w.writerows([{"cid": g["cid"], "quote_doc": "feitai",
                              "golden_state": g["golden_state"], "annotator": "x", "note": ""}
                             for g in gold])
        r = subprocess.run([sys.executable, os.path.join(ROOT, "eval", "cohen_kappa.py"),
                            "--dir", d, "--base-dir", os.path.join(ROOT, "eval")],
                           capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("多个回传文件", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
