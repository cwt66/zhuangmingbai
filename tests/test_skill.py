# -*- coding: utf-8 -*-
"""码道 Skill 的锁：SKILL.md 不能是死文档。

## 为什么给一份 Markdown 写测试

Skill 是给智能体看的说明书。说明书最大的风险不是写错，是**过期**——
命令改了、脚本挪了、参数换了，而 SKILL.md 还写着旧的，
于是智能体照着一条不存在的命令去执行，然后"合理地"编一个结果。

所以这里做两件事：
1. 把 SKILL.md 里出现的每一条命令，逐个验证**目标文件真实存在**；
2. 真的跑一次入口脚本，确认它输出的字段和文档里写的一致。

这是「可执行规格」和「一篇文档」的区别。
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL = os.path.join(ROOT, ".codeartsdoer", "skills", "zmb-audit", "SKILL.md")
AGENTS = os.path.join(ROOT, ".codeartsdoer", "AGENTS.md")
REAL = os.path.join(ROOT, "data", "real")


class TestSkillFile(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(SKILL, encoding="utf-8") as f:
            cls.text = f.read()

    def test_frontmatter(self):
        """码道要求 name/description，且 name 只能是小写字母数字连字符。"""
        self.assertTrue(self.text.startswith("---"), "缺 frontmatter")
        head = self.text.split("---", 2)[1]
        self.assertIn("name:", head)
        self.assertIn("description:", head)
        name = re.search(r"^name:\s*(\S+)", head, re.M).group(1)
        self.assertRegex(name, r"^[a-z0-9]+(-[a-z0-9]+)*$", "name 不符合码道命名规则")
        desc = re.search(r"^description:\s*(.+)$", head, re.M).group(1)
        self.assertGreater(len(desc), 60, "description 太短，触发不了")

    def test_description_has_trigger_words(self):
        """触发词得覆盖真实说法，否则技能不会被唤起。"""
        head = self.text.split("---", 2)[1]
        for w in ("装修", "报价", "漏项", "核对"):
            self.assertIn(w, head, "触发词缺：%s" % w)

    def test_every_referenced_command_exists(self):
        """文档里每个 scripts/xxx.py、eval/xxx.py 都必须真实存在。"""
        refs = set(re.findall(r"(?:scripts|eval)/[A-Za-z0-9_]+\.py", self.text))
        self.assertGreaterEqual(len(refs), 4, "引用太少，可疑")
        missing = [r for r in refs if not os.path.isfile(os.path.join(ROOT, r))]
        self.assertEqual(missing, [], "SKILL.md 引用了不存在的脚本：%s" % missing)

    def test_every_referenced_data_file_exists(self):
        refs = set(re.findall(r"data/[A-Za-z0-9_]+\.json", self.text))
        self.assertGreaterEqual(len(refs), 3)
        missing = [r for r in refs if not os.path.isfile(os.path.join(ROOT, r))]
        self.assertEqual(missing, [], "SKILL.md 引用了不存在的数据：%s" % missing)

    def test_declares_boundary(self):
        """必须写明不判「贵不贵」——这是全项目的边界，不能只写在 README 里。"""
        self.assertIn("不判断「贵不贵」", self.text)


class TestAgentsFile(unittest.TestCase):
    def test_exists_and_has_conventions(self):
        with open(AGENTS, encoding="utf-8") as f:
            t = f.read()
        for kw in ("判定不经过大模型", "零第三方依赖", "宁可少报", "指纹"):
            self.assertIn(kw, t, "AGENTS.md 缺约定：%s" % kw)
        self.assertIn("zmb-audit", t, "AGENTS.md 没提已有 skill")


class TestEntryActuallyRuns(unittest.TestCase):
    """真的跑一次，别只检查文件在不在。"""

    def test_audit_one_runs_and_prints_fields(self):
        r = subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", "audit_one.py"),
             os.path.join(REAL, "feitai_budget.csv"), "--label", "翡翠台"],
            capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(r.returncode, 0, r.stderr[-400:])
        out = r.stdout
        for kw in ("交付判定", "判定指纹", "可核减漏项", "可核减重复"):
            self.assertIn(kw, out, "输出缺字段：%s" % kw)

    def test_audit_one_json_is_parseable(self):
        r = subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", "audit_one.py"),
             os.path.join(REAL, "guijiao_124_basic.csv"), "--label", "124㎡",
             "--scope", "base", "--json"],
            capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(r.returncode, 0, r.stderr[-400:])
        d = json.loads(r.stdout)
        self.assertEqual(d["label"], "124㎡")
        self.assertEqual(d["scope"], "base")
        self.assertIn(d["verdict"], ("pass", "needs_fix", "block"))
        self.assertEqual(len(d["digest"]), 16)

    def test_gate_blocks_non_quote(self):
        """不是报价单要返回 2（守门），并说明理由——不能 500。"""
        d = tempfile.mkdtemp()
        p = os.path.join(d, "合同.csv")
        with open(p, "w", encoding="utf-8-sig") as f:
            f.write("合同编号,甲方,乙方,工期\nHT-001,张三,某公司,90天\n")
        r = subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", "audit_one.py"), p],
            capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(r.returncode, 2, "守门没拦住，或返回码不对")
        self.assertTrue(r.stderr.strip(), "拒绝时没有任何说明")


if __name__ == "__main__":
    unittest.main()
