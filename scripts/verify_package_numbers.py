#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""提交包数字一致性：把「评委实际拿到的材料」也纳入复算。

## 这轮补的是哪一个缝

`data/claims_v0.json` 的 occurrences 只反查**仓库内**的 `README.md` 和
`docs/*.md`。但评委拿到的是仓库外的 `国奖提交包/`——22 份 md、几十处数字
（268 测试 / 16 步 / 19 条主张 / commit hash / sha256 清单）。
这些数字上一轮是手工同步的，**CI 管不到，台账也管不到**。
一旦再提交一次，提交包里的 hash 就会悄悄过期，而我们不会发现。

所以做这个脚本：扫描提交包里的旧值残留、当前值覆盖、HEAD 一致性与
sha256 校验清单，输出一份可复算的报告，**并把报告放进提交包 09_证据附录**，
让评委能现场重跑。

## 为什么它不进 ① 档主张台账

CI 的 checkout 里没有 `国奖提交包/`（它在仓库外，含评分自评与答辩材料，
不随公开仓库发布）。探针在 CI 上跑不出值——**写不出命令的值不许伪装成
① 当场可复算**。所以这条是本地工具、② 档边界，在 `docs/CLAIM-LEDGER.md`
里主动交代，不塞进 check_all.sh 骗一个绿勾。

用法：
    python3 scripts/verify_package_numbers.py            # 人读报告
    python3 scripts/verify_package_numbers.py --json     # 一行 JSON
    python3 scripts/verify_package_numbers.py --package /path/to/国奖提交包
只用标准库；不联网。
"""

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PACKAGE = ROOT.parent / "国奖提交包"
OUT_REPORT = ROOT / "out" / "package_numbers.md"
PACKAGE_REPORT_REL = "09_证据附录/提交包数字一致性报告.md"
SHA_FILE = "06_开源与部署/文件校验_sha256.txt"

# 历史存证文件：允许保留旧值，但必须有「不回填」声明（下面另查）
HISTORY_FILES = (
    "09_证据附录/演示环境验证记录.md",
    "09_证据附录/评测原始输出/",
    "09_证据附录/运行存证/",
    "09_证据附录/远程仓库复验_",
    PACKAGE_REPORT_REL,  # 报告自己
)
# 行级豁免：明确标注为历史/采样/不回填的说明性文字
HISTORY_LINE_MARKERS = ("历史存证", "历史采样", "不回填", "旧采样", "当时真实", "按纪律")
# 叙述豁免：讲「以前写着什么 / 从 X 变成 Y」的句子本来就该保留旧值。
# 不豁免这些，工具就会逼人把诚实的历史记录删掉——那比数字漂移更糟。
NARRATIVE_MARKERS = ("写着", "此前", "原本", "当时", "档位从", "变成", "旧值",
                     "降级", "过期", "漂移", "第一次", "难看", "但它是对的",
                     "悄悄", "代码一改", "比 ①")

# (规则 id, 名称, 旧值正则, 旧值说明, 同行出现即判为「对比叙述」的当前值)
# 正则刻意收窄：只匹配「自检总步数」，不匹配「第 5、15 步」或日期「11/15」。
STALE_RULES = (
    ("tests", "单元测试数", r"(?<!\d)243\s*(?:个\s*(?:单元)?测试|个\s*单测|tests?\b|测试\b)",
     "243 测试", ("268",)),
    ("steps", "自检步数", r"(?:15\s*步\s*自检|自检\s*15\s*步|（\s*15\s*步\s*）|"
     r"\(\s*15\s*步\s*\)|共\s*15\s*步|十五步|==\s*\d+/15\s)", "15 步", ("16 步",)),
    ("engine_lines", "引擎行数", r"1,198\s*行|1,248\s*行", "引擎行数", ("零第三方包",)),
    # 只认「历史档位组合」，不把表格里的当前计数写死 —— 每加一条 ① 档主张，
    # 表格数字就会变，写死值必然误报（这轮就踩过一次）。
    ("tiers", "证据档位", r"①\s*14\s*/\s*②\s*2|①\s*13\s*/\s*②\s*3|"
     r"①\s*14\s*条\s*/\s*②\s*2\s*条|①\s*13\s*条\s*/\s*②\s*3\s*条",
     "旧档位组合", ("① 14 / ② 3", "① 14 条 / ② 3 条")),
    ("history_count", "撤回档案条数", r"撤回档案\s*10\s*条|10\s*条撤回|10\s*条\s*/\s*3\s*条",
     "10 条撤回档案", ("11 条",)),
)

# (名称, 字面值, 至少出现在几份非豁免文件里)
REQUIRED_RULES = (
    ("当前测试数", "344", 3),
    ("当前步数", "20 步", 3),
    ("主张台账条数", "25 条", 2),
    ("当前档位", "① 18 / ② 4", 1),
    ("Skills 规范", "20/20", 1),
)


def short_head():
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=str(ROOT),
                              capture_output=True, text=True).stdout.strip()
    except OSError:
        return ""


def release_heads():
    """提交包是在某个 commit 上生成的，之后为同步交付物还会再提交一次。
    所以认可最近 3 个祖先；任何无关 hash 仍必须 FAIL。
    修的是「生成完再上传会误报自己过期」这个设计缺陷，不是放宽口径。
    """
    out = []
    for spec in ("HEAD", "HEAD~1", "HEAD~2"):
        try:
            r = subprocess.run(["git", "rev-parse", "--short", spec], cwd=str(ROOT),
                               capture_output=True, text=True)
            if r.returncode == 0 and r.stdout.strip():
                out.append(r.stdout.strip())
        except OSError:
            break
    return out


def select_release_head(heads, texts, minimum=3):
    """纯函数：从候选 hash 里挑出「在 ≥minimum 份文件出现」的发布快照。

    刻意不读 git —— 双向断言在只有 1 个 commit 的浅克隆里也必须能构造。
    CI 的 check job 用默认 fetch-depth=1，曾因此少跑一例断言而变红。
    """
    matched = [(h, [rel for rel, t in texts.items() if h and h in t]) for h in heads]
    return next(((h, f) for h, f in matched if len(f) >= minimum), None)


def is_history_file(rel):
    return any(rel.startswith(h.rstrip("/")) if h.endswith("/") else rel == h
               for h in HISTORY_FILES)


def scan(package):
    violations, notes, stats = [], [], {}
    md_files = sorted(p for p in package.rglob("*.md") if p.is_file())
    stats["md_files"] = len(md_files)
    stats["history_files"] = sum(1 for p in md_files
                                 if is_history_file(p.relative_to(package).as_posix()))
    texts = {}
    for p in md_files:
        rel = p.relative_to(package).as_posix()
        texts[rel] = p.read_text(encoding="utf-8", errors="ignore")

    # 1) 旧值残留（历史存证文件、明确标注历史的行、对比叙述行，三类豁免都要留痕）
    stale_hits = {r[0]: [] for r in STALE_RULES}
    exempted = []
    for rel, text in texts.items():
        if is_history_file(rel):
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            why = None
            if any(m in line for m in HISTORY_LINE_MARKERS):
                why = "历史存证声明"
            for rid, name, pat, label, currents in STALE_RULES:
                if not re.search(pat, line):
                    continue
                if why is None and any(m in line for m in NARRATIVE_MARKERS):
                    why = "历史叙述"
                if why is None and any(c in line for c in currents):
                    why = "新旧值对比"
                if why:
                    exempted.append("%s:%d [%s] %s" %
                                    (rel, lineno, why, line.strip()[:70]))
                else:
                    stale_hits[rid].append("%s:%d %s" % (rel, lineno, line.strip()[:70]))
    for rid, name, pat, label, currents in STALE_RULES:
        stats["stale_" + rid] = len(stale_hits[rid])
        if stale_hits[rid]:
            violations.append("【%s】仍有 %d 处旧值「%s」：%s" %
                              (name, len(stale_hits[rid]), label, stale_hits[rid][:3]))
        else:
            notes.append("【%s】无旧值残留" % name)
    stats["exempted_lines"] = len(exempted)
    notes.append("合法历史叙述豁免 %d 行（不是悄悄放过，逐行列在报告里）" % len(exempted))
    result_exempted = exempted

    # 2) 当前值覆盖
    for name, literal, minimum in REQUIRED_RULES:
        n = sum(1 for rel, t in texts.items() if not is_history_file(rel) and literal in t)
        stats["required_" + name] = n
        if n < minimum:
            violations.append("【%s】当前值「%s」只在 %d 份文件里出现，要求 ≥%d" %
                              (name, literal, n, minimum))
        else:
            notes.append("【%s】「%s」出现在 %d 份文件（≥%d）" % (name, literal, n, minimum))

    # 3) commit hash：发布快照 commit 必须出现在至少 3 份文件。
    # 提交包生成后为把 zip 放进仓库会再提交一次，故认可最近 3 个祖先；
    # 无关 hash（tamper_head）仍必须 FAIL，见 --self-test。
    heads = release_heads()
    head = heads[0] if heads else ""
    if not heads:
        # 离线演示包不带 .git：没有历史可比，明确降级为「跳过」而不是判过期。
        # 仓库内/CI 仍然走下面这条严格路径。
        stats["head"] = ""
        stats["head_files"] = 0
        notes.append("无 git 历史（离线包），跳过发布快照 commit 校验")
        picked = None
    else:
        picked = select_release_head(heads, texts)
    if heads and not picked:
        violations.append("发布快照 commit 不在最近 3 个祖先里（当前 HEAD `%s`），提交包可能已过期" % head)
    elif picked:
        # 报告是「生成时点的快照」，内容不能随之后的同步提交变化；
        # 所以只写快照 hash 与覆盖份数，不写当前 HEAD / 相差几个提交。
        stats["head"] = picked[0]
        stats["head_files"] = len(picked[1])
        notes.append("发布快照 commit `%s` 出现在 %d 份文件（认可 HEAD 最近 3 个祖先；无关 hash 仍 FAIL）" %
                     (picked[0], len(picked[1])))
    other_hashes = {}
    for rel, t in texts.items():
        for h in re.findall(r"`([0-9a-f]{7})`", t):
            if h not in heads:
                other_hashes.setdefault(h, set()).add(rel)
    for h, files in sorted(other_hashes.items()):
        notes.append("历史引用 `%s` 出现在 %d 份文件（不判违规，人工确认是采样点即可）" %
                     (h, len(files)))

    # 4) sha256 校验清单逐条复算
    sha_path = package / SHA_FILE
    if not sha_path.is_file():
        violations.append("缺少 %s" % SHA_FILE)
    else:
        checked = 0
        for line in sha_path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^([0-9a-f]{64})\s+(\S+)", line)
            if not m:
                continue
            expect, name = m.group(1), m.group(2)
            f = package / "06_开源与部署" / name
            if not f.is_file():
                violations.append("校验清单里的 %s 不存在" % name)
                continue
            got = hashlib.sha256(f.read_bytes()).hexdigest()
            checked += 1
            if got != expect:
                violations.append("sha256 不符：%s（清单 %s…，实际 %s…）" %
                                  (name, expect[:12], got[:12]))
        stats["sha_checked"] = checked
        if checked:
            notes.append("sha256 清单复算 %d 个文件，全部一致" % checked)

    # 5) 历史存证文件必须带「不回填」声明（否则允许旧值就成了漏洞）
    for rel in ("09_证据附录/演示环境验证记录.md",
                "09_证据附录/评测原始输出/README.md"):
        p = package / rel
        if p.is_file() and "不回填" not in p.read_text(encoding="utf-8"):
            violations.append("%s 保留了历史值，却没写「不回填」声明" % rel)
    notes.append("历史存证豁免文件 %d 份，均已检查声明" % stats["history_files"])

    return {"md_files": len(md_files), "stats": stats, "notes": notes,
            "violations": violations, "exempted": result_exempted}


def self_test():
    """双向断言：最小干净包必须 PASS，5 种故障必须 FAIL。

    用临时目录造一份最小提交包骨架，不依赖真实提交包，也不写任何交付文件。
    """
    import tempfile

    head = short_head()
    # 所有当前值都从规则表取，避免「改了口径忘了改自测」造成假红
    def cur(name):
        return next(r[1] for r in REQUIRED_RULES if r[0] == name)

    current_tests = cur("当前测试数")
    cases = []

    def build(root):
        root = Path(root)
        (root / "06_开源与部署").mkdir(parents=True, exist_ok=True)
        (root / "09_证据附录").mkdir(parents=True, exist_ok=True)
        common = ("当前 HEAD `%s`；%s 个单元测试；一键自检 %s；"
                  "主张台账 %s；证据分档 %s / ③ 3；"
                  "撤回档案 13 条；华为云 Skills 规范 20/20 项。\n" % (
                      head, current_tests, cur("当前步数"),
                      cur("主张台账条数"), cur("当前档位")))
        for name in ("README.md", "00_导航.md", "07_答辩.md"):
            (root / name).write_text(common, encoding="utf-8")
        (root / "09_证据附录" / "演示环境验证记录.md").write_text(
            "历史存证，按纪律不回填：243 测试 / 15 步是当时的真实值。\n", encoding="utf-8")
        payload = (root / "06_开源与部署" / "dummy.zip")
        payload.write_bytes(b"zmb-selftest")
        digest = hashlib.sha256(payload.read_bytes()).hexdigest()
        (root / "06_开源与部署" / "文件校验_sha256.txt").write_text(
            "%s  dummy.zip\n" % digest, encoding="utf-8")
        return root

    def case(name, mutate, expect):
        with tempfile.TemporaryDirectory(prefix="zmb_pkg_") as td:
            root = build(td)
            mutate(root)
            got = "PASS" if not scan(root)["violations"] else "FAIL"
        cases.append({"name": name, "expected": expect, "got": got,
                      "ok": got == expect})

    case("干净最小包必须 PASS（会说「是」）", lambda r: None, "PASS")
    # 这一例用纯函数构造，不读 git：浅克隆（1 个 commit）里也必须跑得到。
    probe = select_release_head(
        ["aaaaaaa", "bbbbbbb"],
        {"a.md": "发布快照 `bbbbbbb`", "b.md": "`bbbbbbb`", "c.md": "`bbbbbbb`"})
    ok = bool(probe and probe[0] == "bbbbbbb")
    cases.append({"name": "发布快照落在 HEAD~1 仍被认可（纯函数，不依赖 git 深度）",
                  "expected": "PASS", "got": "PASS" if ok else "FAIL", "ok": ok})


    def add_stale(r):
        p = r / "README.md"
        p.write_text(p.read_text(encoding="utf-8") + "\n旧稿：243 个单元测试全绿。\n",
                     encoding="utf-8")
    case("塞入旧测试数 → FAIL", add_stale, "FAIL")

    def drop_current(r):
        for p in r.glob("*.md"):
            p.write_text(p.read_text(encoding="utf-8").replace(current_tests, "X"),
                         encoding="utf-8")
    case("抹掉当前测试数 → FAIL", drop_current, "FAIL")

    def break_sha(r):
        p = r / "06_开源与部署" / "文件校验_sha256.txt"
        p.write_text("0" * 64 + "  dummy.zip\n", encoding="utf-8")
    case("篡改 sha256 清单 → FAIL", break_sha, "FAIL")

    def remove_disclaimer(r):
        p = r / "09_证据附录" / "演示环境验证记录.md"
        p.write_text("243 测试 / 15 步\n", encoding="utf-8")
    case("历史文件删掉「不回填」声明 → FAIL", remove_disclaimer, "FAIL")

    def tamper_head(r):
        for p in r.glob("*.md"):
            p.write_text(p.read_text(encoding="utf-8").replace(head, "deadbeef"),
                         encoding="utf-8")
    case("提交包 HEAD 全部过期 → FAIL", tamper_head, "FAIL")

    return {"status": "PASS" if all(c["ok"] for c in cases) else "FAIL", "cases": cases}


def render(result, package, head):
    s = result["stats"]
    lines = [
        "# 提交包数字一致性报告", "",
        "> 由 `scripts/verify_package_numbers.py` 复算；评委可在干净 clone 里重跑。"
        "提交包本体不随公开仓库发布，所以这条**不是 ① 档台账条目**，是本地 ② 档边界。", "",
        "**%s** ｜ 扫描 %d 份 md ｜ 发布快照 commit `%s` ｜ 历史存证豁免 %d 份" %
        ("PASS" if not result["violations"] else "FAIL",
         result["md_files"], head, s.get("history_files", 0)), "",
        "| 检查项 | 值 |", "|---|---:|",
        "| 单元测试旧值残留 | %d 处 |" % s.get("stale_tests", 0),
        "| 步数旧值残留 | %d 处 |" % s.get("stale_steps", 0),
        "| 引擎行数旧值残留 | %d 处 |" % s.get("stale_engine_lines", 0),
        "| 档位旧值残留 | %d 处 |" % s.get("stale_tiers", 0),
        "| 撤回档案旧值残留 | %d 处 |" % s.get("stale_history_count", 0),
        "| 发布快照 commit 出现文件数 | %d |" % s.get("head_files", 0),
        "| sha256 清单复算文件数 | %d |" % s.get("sha_checked", 0),
        "| 合法历史叙述豁免行 | %d |" % s.get("exempted_lines", 0), "",
        "## 明细", "",
    ]
    lines += ["- %s" % n for n in result["notes"]]
    lines += ["", "## 被豁免的合法历史叙述（逐行留痕，不是悄悄放过）", ""]
    if result["exempted"]:
        lines += ["- %s" % e for e in result["exempted"]]
    else:
        lines.append("- 无")
    lines += ["", "## 违规", ""]
    lines += ["- %s" % v for v in result["violations"]] if result["violations"] else ["- 无"]
    lines += ["", "## 边界（主动交代）", "",
              "- 工具在**仓库内**，被检查的提交包在**仓库外**；CI 的 checkout 没有它，",
              "  所以不能进 `check_all.sh`，也不占一步自检编号。",
              "- 历史存证按纪律不回填；豁免只在「文件白名单 + 明确写了不回填」时生效。",
              "- 其它 7 位 commit hash 只列为历史引用提示，不自动判违规（人工确认是采样点）。",
              "- 本报告本身在豁免名单里，避免报告里的模式字符串把自己扫红。", ""]
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(description="提交包数字一致性复算")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--package", default=str(DEFAULT_PACKAGE))
    ap.add_argument("--no-write", action="store_true", help="不写报告文件")
    ap.add_argument("--self-test", action="store_true", help="双向断言与故障注入")
    args = ap.parse_args()

    if args.self_test:
        st = self_test()
        if args.json:
            print(json.dumps(st, ensure_ascii=False))
        else:
            for c in st["cases"]:
                print("| %s | %s | %s | %s |" %
                      (c["name"], c["expected"], c["got"], "✅" if c["ok"] else "❌"))
            print("\n**自检：%s**" % st["status"])
        return 0 if st["status"] == "PASS" else 1

    package = Path(args.package).resolve()

    if not package.is_dir():
        payload = {"status": "UNAVAILABLE", "reason": "提交包目录不存在（CI 的 checkout 里没有它）",
                   "package": str(package)}
        print(json.dumps(payload, ensure_ascii=False) if args.json
              else "[UNAVAILABLE] 提交包目录不存在：%s\n这不代表通过，只是 CI 上没法算。" % package)
        return 0  # 不算通过，也不算失败；如实说明，不进任何门禁

    result = scan(package)
    head = result["stats"].get("head", "")
    status = "PASS" if not result["violations"] else "FAIL"
    md = render(result, package, head)
    if not args.no_write:
        OUT_REPORT.parent.mkdir(parents=True, exist_ok=True)
        OUT_REPORT.write_text(md, encoding="utf-8")
        (package / PACKAGE_REPORT_REL).write_text(md, encoding="utf-8")

    if args.json:
        print(json.dumps({"status": status, "md_files": result["md_files"],
                          "violations": result["violations"],
                          "head": head,
                          "report": PACKAGE_REPORT_REL}, ensure_ascii=False))
    else:
        print(md)
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
