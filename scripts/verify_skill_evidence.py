# -*- coding: utf-8 -*-
"""校验「码道调用 Skill」的运行证据，是否与引擎实跑一致。

## 这个脚本解决什么问题

把一段智能体会话贴进 `stage6_skill.txt`，能证明「用过」，但证明不了
「它说的是真的」。智能体完全可能把 618.40 写成 6,184.00，或者漏掉一条
漏项却仍然输出一张好看的表格——而读材料的人不会去跑代码。

所以这里不比「有没有证据」，比的是**证据里的每个数字能不能被复算**：
重新跑一遍引擎，把会话里写的行数、明细合计、可核减金额、交付判定、
判定指纹逐个对上去。对不上就是编的，明说。

## 为什么允许「证据缺失」时返回 0

证据文件在提交包里（不随开源仓库发布），干净 clone 里没有。
所以默认模式下「没采到」只报告状态、不判失败——否则 CI 会因为这个
永远红。但一旦文件存在，校验就是**硬**的：数字不对直接非 0。

阈值只有一条原则：**金额差 1 分钱就算不一致**。含糊的容差等于没校验。

用法：

    python3 scripts/verify_skill_evidence.py
    python3 scripts/verify_skill_evidence.py --evidence ../国奖提交包/05_CodeArts过程资产/raw/stage6_skill.txt
    python3 scripts/verify_skill_evidence.py --require     # 缺证据即失败（提交前用）

退出码：0 通过或待采集；1 证据与实跑不一致；2 要求证据但缺失。
"""
import argparse
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import pipeline as P  # noqa: E402
import report_gate  # noqa: E402
import audit_one  # noqa: E402

# 证据文件可能出现的位置（按优先级）
CANDIDATES = (
    os.path.join(ROOT, "..", "国奖提交包", "05_CodeArts过程资产", "raw", "stage6_skill.txt"),
    os.path.join(ROOT, "docs", "codearts", "stage6_skill.txt"),
)

# 证据里必须能找到的六个字段（正则容得下加粗、千分位、反引号）
FIELDS = {
    "rows": ("工序行数", r"\|\s*工序行数\s*\|\s*\**\s*([\d,]+)\s*\|", "int"),
    "total": ("明细合计", r"\|\s*明细合计\s*\|\s*\**\s*([\d,]+(?:\.\d+)?)\s*元", "money"),
    "missing_cost": ("可核减漏项", r"\|\s*可核减漏项\s*\|\s*\**\s*([\d,]+(?:\.\d+)?)\s*元", "money"),
    "duplicate_cut": ("可核减重复", r"\|\s*可核减重复\s*\|\s*\**\s*([\d,]+(?:\.\d+)?)\s*元", "money"),
    "verdict": ("交付判定", r"\|\s*交付判定\s*\|\s*\**\s*([a-zA-Z_]+)\s*\**\s*\|", "str"),
    "digest": ("判定指纹", r"\|\s*判定指纹\s*\|\s*\**\s*`?([0-9a-f]{8,64})`?", "str"),
}

# 采集时的 commit。只作信息展示：证据可能采于稍早的 commit，
# 之后若只改文档，数字仍应一致；若判定链路过改，下面的字段比对会自己报错。
COMMIT_RE = re.compile(r"\bHEAD[：:\s]+([0-9a-f]{7,40})\b", re.I)


def _local_head():
    try:
        r = subprocess.run(["git", "-C", ROOT, "rev-parse", "--short", "HEAD"],
                           capture_output=True, text=True, timeout=5)
        return (r.stdout or "").strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


# 不能说出口的话：无口径的确定性断言。带「不是/非/下限」等否定前缀的放过。
FORBIDDEN = (
    ("召回率 100%", ("不是", "非", "下限", "不等于", "并非")),
    ("召回率100%", ("不是", "非", "下限", "不等于", "并非")),
    ("准确率 100%", ("不是", "非", "并非")),
    ("准确率 83.3%", ("口径", "auto", "CI", "边界", "只算")),
)


def _money(s):
    return float(str(s).replace(",", ""))


def expected(csv, label, template="auto", scope="full"):
    """按引擎实跑一遍，返回应出现在证据里的六个字段（与 audit_one 同源）。"""
    onto = P.Ontology(os.path.join(audit_one.DATA, "ontology_v0.json"))
    tpl = audit_one._load("templates_v0.json")
    rules = audit_one._load("rules_v0.json")
    cov = audit_one._load("coverage_v0.json")
    q = P.build_line_index(P.load_quote(csv, label), onto)
    tk = template
    if tk == "auto":
        tk = "old_house" if len(q["rows"]) >= 80 else "new_house"
    res = P.audit([q], onto, tpl, rules, cov, template_key=tk, scope=scope)[label]

    def replay():
        return P.audit([q], onto, tpl, rules, cov, template_key=tk, scope=scope)[label]

    gate = report_gate.check(q, res, replay=replay)
    return {
        "rows": len(q["rows"]),
        "total": round(float(q["total"]), 2),
        "missing_cost": round(float(res["missing_cost"]), 2),
        "duplicate_cut": round(float(res["duplicate_cut"]), 2),
        "verdict": gate["verdict"],
        "digest": gate["digest"],
    }


def _json_blocks(text):
    """从文本里扫出所有能解析的 JSON 对象（按大括号配对，不靠正则）。

    用途：有的客户端会把命令输出折叠，用户只能粘 `--json` 的结果。
    格式可以退让，数字不能——所以这里认两种形态。
    """
    out = []
    i = 0
    while i < len(text):
        if text[i] != "{":
            i += 1
            continue
        depth = 0
        for j in range(i, len(text)):
            if text[j] == "{":
                depth += 1
            elif text[j] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[i:j + 1])
                        if isinstance(obj, dict):
                            out.append(obj)
                    except ValueError:
                        pass
                    i = j
                    break
        i += 1
    return out


def extract_json(text):
    """JSON 形态：只要有一个对象同时带 digest 与 rows，就认它。"""
    for obj in _json_blocks(text):
        if "digest" in obj and "rows" in obj:
            return {k: obj.get(k) for k in FIELDS}
    return None


def extract(text):
    """从证据文本里抠出六个字段；抠不到的记为 None。

    先按 Markdown 表格解析（正常路径），字段缺一半以上时退回 JSON
    （客户端折叠输出、用户只能粘 `--json` 的兜底）。
    """
    got_md = _extract_md(text)
    if sum(1 for v in got_md.values() if v is not None) >= 3:
        return got_md
    return extract_json(text) or got_md


def _extract_md(text):
    got = {}
    for key, (_name, pat, kind) in FIELDS.items():
        m = re.search(pat, text)
        if not m:
            got[key] = None
            continue
        raw = m.group(1)
        if kind == "int":
            got[key] = int(raw.replace(",", ""))
        elif kind == "money":
            got[key] = _money(raw)
        else:
            got[key] = raw
    return got


def find_forbidden(text):
    """找出无口径的确定性断言；带否定前缀的不算。"""
    hits = []
    for phrase, negs in FORBIDDEN:
        start = 0
        while True:
            i = text.find(phrase, start)
            if i < 0:
                break
            window = text[max(0, i - 28):i]
            if not any(n in window for n in negs):
                hits.append(phrase)
            start = i + len(phrase)
    return hits


def check(text, exp):
    """六个字段逐项比对。金额按 1 分钱判定，不做模糊容差。"""
    got = extract(text)
    rows = []
    for key, (name, _p, _k) in FIELDS.items():
        claim, truth = got.get(key), exp.get(key)
        if claim is None:
            rows.append((key, name, "—", truth, "missing", "证据里找不到这个字段"))
            continue
        if isinstance(truth, float):
            ok = abs(claim - truth) < 0.005
            shown = format(claim, ",.2f")
        else:
            ok = claim == truth
            shown = str(claim)
        rows.append((key, name, shown, truth,
                     "ok" if ok else "mismatch",
                     "" if ok else "与实跑不一致"))
    bad = [r for r in rows if r[4] != "ok"]
    return rows, bad


def resolve(cli_path):
    if cli_path:
        return cli_path if os.path.isfile(cli_path) else None
    env = os.environ.get("ZMB_SKILL_EVIDENCE")
    if env and os.path.isfile(env):
        return env
    for p in CANDIDATES:
        if os.path.isfile(p):
            return p
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(description="校验码道 Skill 运行证据与引擎实跑是否一致")
    ap.add_argument("--evidence", default="", help="证据文件路径；不给则自动找")
    ap.add_argument("--csv", default=os.path.join(ROOT, "data", "real", "feitai_budget.csv"))
    ap.add_argument("--label", default="翡翠台")
    ap.add_argument("--template", default="auto", choices=["auto", "old_house", "new_house"])
    ap.add_argument("--scope", default="full", choices=["full", "base"])
    ap.add_argument("--require", action="store_true", help="证据缺失即失败（提交前用）")
    args = ap.parse_args(argv)

    path = resolve(args.evidence)
    print("# 码道 Skill 运行证据校验")
    print()

    if not path:
        if args.evidence:
            print("指定的证据文件不存在：`%s`" % args.evidence)
            print()
            print("要采证请按 `05_CodeArts过程资产/prompts/stage6_skill_prompt.md` 跑一次码道。")
            return 2
        print("| 项 | 值 |")
        print("|---|---|")
        print("| 状态 | **待采集**（R7 仍为 🟡） |")
        print("| 期望路径 | `05_CodeArts过程资产/raw/stage6_skill.txt` |")
        print("| 提示词 | `05_CodeArts过程资产/prompts/stage6_skill_prompt.md` |")
        print()
        print("证据文件不存在，未做任何校验。**这不代表通过，只是还没采。**")
        return 2 if args.require else 0

    with open(path, encoding="utf-8", errors="replace") as f:
        text = f.read()

    exp = expected(args.csv, args.label, args.template, args.scope)
    rows, bad = check(text, exp)
    forbidden = find_forbidden(text)

    # 来源标记只是提醒，不判失败：不同客户端输出的头部格式不一样。
    has_mark = bool(re.search(r"openpangu|build ·|codearts|码道", text, re.I))

    print("| 项 | 值 |")
    print("|---|---|")
    shown_path = os.path.relpath(path, ROOT) if path.startswith(ROOT + os.sep) else path
    print("| 证据文件 | `%s` |" % shown_path)
    print("| 体积 | %d 字符 |" % len(text))
    print("| 来源标记 | %s |" % ("有" if has_mark else "**未见**（建议补会话头部）"))
    m = COMMIT_RE.search(text)
    if m:
        got_commit, local = m.group(1), _local_head()
        same = bool(local) and (local.startswith(got_commit[:7]) or got_commit.startswith(local[:7]))
        print("| 采集时 commit | `%s`（本地 HEAD `%s`，%s） |" % (
            got_commit, local or "—", "一致" if same else "不同（若期间只改文档则正常）"))
    print()
    print("## 逐字段比对（基准：引擎实跑 %s / %s）" % (args.label, args.template))
    print()
    print("| 字段 | 证据里写的 | 实跑得到 | 判定 |")
    print("|---|---:|---:|---|")
    for _key, name, claim, truth, status, note in rows:
        mark = {"ok": "✅", "mismatch": "❌ **不一致**", "missing": "⚠ 缺字段"}[status]
        truth_s = format(truth, ",.2f") if isinstance(truth, float) else str(truth)
        print("| %s | %s | %s | %s%s |" % (
            name, claim, truth_s, mark, ("　" + note) if note else ""))
    print()
    if forbidden:
        print("## 纪律检查：**不通过**")
        print()
        for w in forbidden:
            print("- 出现无口径断言「%s」——要么补口径（auto 档 / n=2 / 95%%CI），要么删掉" % w)
        print()
    else:
        print("## 纪律检查：通过（未出现无口径的确定性断言）")
        print()

    ok = (not bad) and (not forbidden)
    if ok:
        print("**结论：证据与实跑一致，可以引用。**")
    else:
        print("**结论：不可引用。** 上面标出 %d 个字段不一致、%d 处纪律问题。"
              % (len(bad), len(forbidden)))
        print()
        if bad:
            print("字段不一致不是「格式问题」，是**证据里的数与被审对象对不上**——")
            print("要么会话被改过，要么智能体自己编了数。两种都不能进材料。")
        else:
            print("数字都对，但**表述越界**了。数字真、话说过头，一样不能进材料——")
            print("评委抓住一句无口径的「100%」，前面所有诚实都会被重新怀疑。")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
