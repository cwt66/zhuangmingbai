# -*- coding: utf-8 -*-
"""锁住「证据校验器」本身：它必须能识破编造的数。

## 为什么给校验器写测试

这个脚本的作用是判断「码道会话里的数字是不是真的」。如果它自己有 bug——
比如正则匹配不到某个字段就跳过、或者金额比错位——那它就变成了
一个**专门给编造数字盖章的工具**，比没有更糟。

所以测试的重点不是「好样本能过」，是**坏样本必须被拦**：
改一个金额、删一个字段、加一句无口径断言，三种都要被点名。
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import verify_skill_evidence as VE  # noqa: E402

CSV = os.path.join(ROOT, "data", "real", "feitai_budget.csv")
LABEL = "翡翠台"


def render(exp, drop=None, extra=""):
    """按 audit_one 的表格格式造一份「看起来像会话输出」的证据。"""
    lines = [
        "现在用 zmb-audit 技能审计这份报价单。",
        "",
        "> build · openpangu-2.0-pro",
        "",
        "• 读取 .codeartsdoer/skills/zmb-audit/SKILL.md",
        "✓ 执行 python3 scripts/audit_one.py data/real/feitai_budget.csv --label 翡翠台",
        "",
        "# 审计结果：翡翠台",
        "",
        "| 项 | 值 |",
        "|---|---|",
        "| 模板 / 口径 | old_house / full |",
    ]
    rows = [
        ("rows", "| 工序行数 | %d |" % exp["rows"]),
        ("total", "| 明细合计 | %s 元 |" % format(exp["total"], ",.2f")),
        ("missing_cost", "| 可核减漏项 | **%s 元** |" % format(exp["missing_cost"], ",.2f")),
        ("duplicate_cut", "| 可核减重复 | **%s 元** |" % format(exp["duplicate_cut"], ",.2f")),
        ("verdict", "| 交付判定 | **%s** |" % exp["verdict"]),
        ("digest", "| 判定指纹 | `%s` |" % exp["digest"]),
    ]
    for key, line in rows:
        if key == drop:
            continue
        lines.append(line)
    if extra:
        lines += ["", extra]
    return "\n".join(lines) + "\n"


class TestEvidenceVerifier(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.exp = VE.expected(CSV, LABEL)

    def _tmp(self, text, name):
        p = os.path.join("/tmp", name)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        return p

    # ---------- 好样本 ----------

    def test_matching_evidence_passes(self):
        text = render(self.exp)
        rows, bad = VE.check(text, self.exp)
        self.assertEqual(bad, [], "一致样本被判不一致：%s" % bad)
        self.assertEqual(VE.find_forbidden(text), [])

    def test_main_exit_zero_on_match(self):
        p = self._tmp(render(self.exp), "ve_good.txt")
        self.assertEqual(VE.main(["--evidence", p]), 0)

    # ---------- 坏样本：数字被改 ----------

    def test_tampered_amount_is_caught(self):
        """把 618.40 改成 6,184.00 —— 会话编了一个更好看的数。"""
        text = render(self.exp).replace(
            format(self.exp["missing_cost"], ",.2f"), "6,184.00")
        _rows, bad = VE.check(text, self.exp)
        self.assertEqual([b[0] for b in bad], ["missing_cost"])
        self.assertEqual(bad[0][4], "mismatch")

    def test_one_cent_off_is_caught(self):
        """差 1 分钱也算不一致——含糊的容差等于没校验。"""
        exp = dict(self.exp)
        exp["duplicate_cut"] = round(exp["duplicate_cut"] + 0.01, 2)
        text = render(self.exp)
        _rows, bad = VE.check(text, exp)
        self.assertEqual([b[0] for b in bad], ["duplicate_cut"])

    def test_tampered_digest_is_caught(self):
        """指纹是最容易被"顺手改一位"的地方。"""
        fake = "0" * 16
        text = render(self.exp).replace(self.exp["digest"], fake)
        _rows, bad = VE.check(text, self.exp)
        self.assertEqual([b[0] for b in bad], ["digest"])

    def test_main_exit_one_on_mismatch(self):
        text = render(self.exp).replace(
            format(self.exp["missing_cost"], ",.2f"), "6,184.00")
        p = self._tmp(text, "ve_bad.txt")
        self.assertEqual(VE.main(["--evidence", p]), 1)

    def test_json_evidence_is_accepted(self):
        """客户端把输出折叠了、只能粘 `--json` 时，数字照样要对得上。

        格式可以退让，数字不能——所以 JSON 形态必须走**同一套**比对，
        而不是"识别到 JSON 就放过"。
        """
        import json
        blob = json.dumps({
            "label": LABEL, "template": "old_house", "scope": "full",
            "rows": self.exp["rows"], "total": self.exp["total"],
            "missing_cost": self.exp["missing_cost"],
            "duplicate_cut": self.exp["duplicate_cut"],
            "verdict": self.exp["verdict"], "digest": self.exp["digest"],
        }, ensure_ascii=False, indent=2)
        text = "HEAD: c9afba8\n\n" + blob + "\n\nEXECUTED: audit_one.py --json\n"
        rows, bad = VE.check(text, self.exp)
        self.assertEqual(bad, [], "JSON 形态未被正确解析：%s" % bad)

    def test_json_evidence_tampered_still_caught(self):
        """JSON 里改一个数，同样要被抓——不能因为换了格式就松。"""
        import json
        blob = json.dumps({
            "rows": self.exp["rows"], "total": self.exp["total"],
            "missing_cost": 6184.00,
            "duplicate_cut": self.exp["duplicate_cut"],
            "verdict": self.exp["verdict"], "digest": self.exp["digest"],
        }, ensure_ascii=False)
        _rows, bad = VE.check(blob, self.exp)
        self.assertEqual([b[0] for b in bad], ["missing_cost"])

    # ---------- 坏样本：字段缺失 ----------

    def test_missing_field_is_flagged(self):
        """删掉指纹行——不能因为「没写」就当作「对」。"""
        text = render(self.exp, drop="digest")
        _rows, bad = VE.check(text, self.exp)
        self.assertEqual([b[0] for b in bad], ["digest"])
        self.assertEqual(bad[0][4], "missing")

    # ---------- 坏样本：表述越界 ----------

    def test_unqualified_claim_is_caught(self):
        text = render(self.exp, extra="本引擎对已知缺陷的召回率 100%。")
        self.assertEqual(VE.find_forbidden(text), ["召回率 100%"])

    def test_negated_claim_is_not_flagged(self):
        """「这不是召回率，是下限校验」——诚实的话不能被误伤。"""
        text = render(self.exp, extra="242/242 不是召回率，是下限校验。")
        self.assertEqual(VE.find_forbidden(text), [])

    def test_qualified_accuracy_is_not_flagged(self):
        """带口径的 83.3% 是允许的（auto 档、n=2）。"""
        text = render(self.exp, extra="auto 档精确率 83.3%，n=2，95%CI 0.552–0.953。")
        self.assertEqual(VE.find_forbidden(text), [])

    def test_main_fails_on_discipline_even_when_numbers_match(self):
        p = self._tmp(render(self.exp, extra="召回率 100%。"), "ve_dis.txt")
        self.assertEqual(VE.main(["--evidence", p]), 1)

    # ---------- 缺证据：不许当成通过 ----------

    def test_explicit_missing_path_is_error(self):
        self.assertEqual(VE.main(["--evidence", "/tmp/ve_no_such_file.txt"]), 2)

    def test_absent_evidence_reports_pending_not_pass(self):
        """默认模式下没证据只是「待采集」，返回 0——但绝不能说成通过。"""
        saved = VE.CANDIDATES
        VE.CANDIDATES = ()
        try:
            self.assertEqual(VE.main([]), 0)
        finally:
            VE.CANDIDATES = saved

    def test_require_mode_fails_without_evidence(self):
        saved = VE.CANDIDATES
        VE.CANDIDATES = ()
        try:
            self.assertEqual(VE.main(["--require"]), 2)
        finally:
            VE.CANDIDATES = saved


if __name__ == "__main__":
    unittest.main()
