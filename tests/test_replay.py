# -*- coding: utf-8 -*-
"""审计回放页的锁：数据必须真、页面必须能自洽解开。

这一页是「评委不跑代码也能点」的兜底，坏掉的代价很高：
JSON 里多一个 `</script>`、少一个字段，页面就是白屏，而且没人会发现——
因为它不在主链路上。所以这里锁四件事：

1. 5 份真实单据、603 行，一个不少；
2. 每份单据的结论数与行项数对得上，行号都能在原表里找到；
3. 嵌进 HTML 的 JSON 能被 JS 那侧原样解析（不转义坏）；
4. 页面里明确写「预计算回放」，不冒充实时引擎。
"""
import importlib.util
import json
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))


def _load_builder():
    path = os.path.join(ROOT, "scripts", "build_replay.py")
    spec = importlib.util.spec_from_file_location("build_replay", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestReplay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load_builder()
        cls.data = cls.mod.build()
        cls.html = cls.mod.render(cls.data)

    def test_five_docs_603_rows(self):
        docs = self.data["docs"]
        self.assertEqual(len(docs), 5)
        # 603 行工序明细；汇总/取费行单列（R13 对账指向它们）
        self.assertEqual(sum(d["detail_rows"] for d in docs), 603)
        self.assertEqual(sum(d["summary_rows"] for d in docs), 11)
        for d in docs:
            self.assertTrue(d["lines"], d["file"])
            self.assertEqual(d["rows"], len(d["lines"]))
            self.assertEqual(d["rows"], d["detail_rows"] + d["summary_rows"])

    def test_every_line_number_exists(self):
        for d in self.data["docs"]:
            nos = {x["no"] for x in d["lines"]}
            for bucket in ("duplicates", "risks"):
                for f in d[bucket]:
                    for ln in f["lines"]:
                        self.assertIn(ln, nos, "%s 第 %s 行不存在" % (d["file"], ln))

    def test_missing_has_basis(self):
        for d in self.data["docs"]:
            for m in d["missing"]:
                self.assertTrue(m["basis"], (d["file"], m["title"]))
                self.assertIn(m["tier"], ("auto", "review"))

    def test_embedded_json_parses(self):
        m = re.search(r'<script id="DATA" type="application/json">(.*?)</script>',
                      self.html, re.S)
        self.assertIsNotNone(m, "JSON 数据块缺失")
        parsed = json.loads(m.group(1))
        self.assertEqual(len(parsed["docs"]), 5)
        self.assertNotIn("</script>", m.group(1))

    def test_says_precomputed(self):
        self.assertIn("预计算回放", self.html)
        self.assertIn("不是实时引擎", self.html)
        self.assertIn("build_replay.py", self.html)


if __name__ == "__main__":
    unittest.main()
