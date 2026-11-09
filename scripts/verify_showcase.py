#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""评委复验页一致性判据：把「展示面」也接进 CI。

## 为什么需要这一层

前四轮把「代码里的数字」「提交包里的数字」「别人 clone 下来的结果」
「云依赖挂了会怎样」「跨环境是否确定」全部接进了自检。
但 2026-11-09 重读工作流时发现：**评委第一眼看到的那一页没人管**。

线上 `https://cwt66.github.io/zhuangmingbai/verify/` 与仓库外
`国奖提交包/02_作品介绍PPT/装明白_评委复验页.html` 字节完全一致，
内容却停在 2026-11-05：`243 测试 / 15 步 / 写死 commit c56b706`。
而当时的真实状态是 `298 测试 / 18 步 / 2aa018f`。
README、台账、提交包、CI 全都绿，只有评委手里那一份悄悄过期了 4 天。

所以这里做的事很朴素：让**当前版展示页**住进仓库 `deliverables/`，
把它的每句可量化数字与 `data/claims_v0.json` 绑定，
改了台账没改这一页（或反之），自检第 19 步就红。

## 三条刻意的设计

1. **不写死 commit hash**。hash 每次提交都变，写进展示页必然过期。
   判据禁止任何 7 位 hex 出现在反引号里；口径统一指向 `git log -1`。
2. **当前值从台账读，不硬编码**。测试数、步数、台账条数、证据档位
   全部取自 `claims_v0.json`，改口径时只改一处。
3. **华为关联和诚实边界是必检项**。新增的第 10 页、四层硬件边界、
   「鲲鹏实例未实测」「③ 档不计已验证」被删掉都必须 FAIL——
   防止有人为了让页面好看而把边界删了。

用法：
    python3 scripts/verify_showcase.py                  # 人读报告 + out/showcase_consistency.md
    python3 scripts/verify_showcase.py --self-test --json   # 双向断言 / CI / 台账探针
只用标准库；不联网、不渲染浏览器。
"""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHOWCASE = ROOT / "deliverables" / "装明白_评委复验页.html"
CLAIMS = ROOT / "data" / "claims_v0.json"
OUT = ROOT / "out" / "showcase_consistency.md"
# 仓库外的提交包副本：本机存在才逐字节比对，CI 上没有就如实跳过
PACKAGE_COPY = ROOT.parent / "国奖提交包" / "02_作品介绍PPT" / "装明白_评委复验页.html"

def stale_rules(v):
    """旧值：出现即违规。刻意收窄，避免误伤「15 条路由」「11/15」这类合法文本。

    「旧步数」必须**动态**取小于当前步数的值：第一版把 18 也写进正则，
    而当时 18 正是当前值，干净页被自己判红。判据不能和当前口径打架。
    """
    old_steps = "|".join(str(i) for i in range(12, int(v["steps"])))
    return (
        ("旧测试数 243", r">243<|243\s*tests?\s*OK|243\s*个?单元测试"),
        ("旧测试数 268/284", r"(?<!\d)(?:268|284)\s*(?:tests?\b|个?单元测试|个测试)"),
        ("旧步数", r"(?:%s)\s*步\s*自检|tests\s*OK\s*·\s*(?:%s)\s*步" %
         (old_steps, old_steps)),
        ("旧生成时点", r"生成时点\s*2026-11-0[1-8]"),
        ("写死 commit hash", r"`[0-9a-f]{7}`"),
    )

# 华为关联 / 诚实边界：少一条就 FAIL
REQUIRED_TEXT = (
    "华为关联 · 可复算证据",
    "跨环境确定性",
    "Ecs.7000",
    "失效矩阵",
    "Agent Skills 规范",
    "鲲鹏",
    "未实测",
    "不写「已适配鲲鹏」",
    "③ 档 3 条",
    "不写死 commit hash",
)


def load_claims():
    return json.loads(CLAIMS.read_text(encoding="utf-8"))


def current_values(claims):
    """当前值只从台账取，判据自己不硬编码数字。"""
    by = {c["id"]: c for c in claims["claims"]}
    tiers = {}
    for c in claims["claims"]:
        tiers[c["tier"]] = tiers.get(c["tier"], 0) + 1
    return {"tests": by["C01"]["value"], "steps": by["C08"]["value"],
            "claims": str(len(claims["claims"])),
            "ran": str(tiers.get("ran", 0)),
            "read": str(tiers.get("read", 0)),
            "declared": str(tiers.get("declared", 0))}


def required_literals(v):
    """从当前值构造「展示页必须出现」的字面量。"""
    return (
        ("当前测试数", "%s tests OK" % v["tests"], 1),
        ("当前步数", "%s 步自检" % v["steps"], 1),
        ("台账条数", "%s 条主张" % v["claims"], 1),
        ("证据档位", "① %s / ② %s / ③ %s" % (v["ran"], v["read"], v["declared"]), 1),
        ("华为云 Skills 规范", "18/18", 1),
        ("失效矩阵实验", "9 个实验", 1),
        ("跨环境确定性", "12 变体 / 144 次比对", 1),
    )


def check_pages(text):
    """页码一致性：section 数 == 页脚分母，且最大编号等于 section 数。"""
    n = len(re.findall(r"<section class=\"slide", text))
    foot = [(int(a), int(b)) for a, b in
            re.findall(r">(\d{2})\s*/\s*(\d{2})</span>", text)]
    if not foot:
        return n, ["找不到页脚编号"]
    problems = []
    if n != max(b for _, b in foot):
        problems.append("页脚分母 %d 与实际 section 数 %d 不一致" %
                        (max(b for _, b in foot), n))
    if n != max(a for a, _ in foot):
        problems.append("最大页脚编号 %d 与实际 section 数 %d 不一致" %
                        (max(a for a, _ in foot), n))
    return n, problems


def scan(text, claims, mirror_text=None):
    """纯函数：给一份 HTML 文本与台账，判 PASS / FAIL。不碰磁盘、不联网。"""
    v = current_values(claims)
    violations, notes = [], []
    checks = 0

    # 1) 当前值覆盖
    for name, literal, minimum in required_literals(v):
        n = text.count(literal)
        checks += 1
        if n < minimum:
            violations.append("【%s】展示页缺少「%s」（出现 %d 次，要求 ≥%d）" %
                              (name, literal, n, minimum))
        else:
            notes.append("【%s】「%s」出现 %d 次" % (name, literal, n))

    # 2) 旧值残留
    for name, pat in stale_rules(v):
        hits = re.findall(pat, text)
        checks += 1
        if hits:
            violations.append("【%s】仍有 %d 处旧值：%s" %
                              (name, len(hits), hits[:3]))
        else:
            notes.append("【%s】无残留" % name)

    # 3) 华为关联与诚实边界
    for literal in REQUIRED_TEXT:
        checks += 1
        if literal not in text:
            violations.append("华为关联或诚实边界被删：「%s」" % literal)
    notes.append("华为关联 / 边界关键字 %d 项全部在位" % len(REQUIRED_TEXT))

    # 4) 页码一致性
    n, problems = check_pages(text)
    checks += 1
    violations.extend(problems)
    notes.append("展示页 %d 页，页脚编号一致" % n)

    # 5) 与仓库外提交包副本逐字节一致（存在才查）
    if mirror_text is not None:
        checks += 1
        a = hashlib.sha256(text.encode("utf-8")).hexdigest()
        b = hashlib.sha256(mirror_text.encode("utf-8")).hexdigest()
        if a != b:
            violations.append("仓库内展示页与提交包副本不一致（%s… ≠ %s…）" %
                              (a[:12], b[:12]))
        else:
            notes.append("提交包副本逐字节一致（sha256 %s…）" % a[:12])
    else:
        notes.append("提交包副本不在本机/CI，跳过镜像比对（本地 ② 档工具负责）")

    return {"status": "FAIL" if violations else "PASS",
            "checks": checks, "violations": violations, "notes": notes,
            "pages": n, "values": v}


def minimal_html(v):
    """自检用的最小展示页：满足全部规则，但不依赖真实交付文件。"""
    lits = " ".join(lit for _, lit, _ in required_literals(v))
    return ("<html><body>" + lits + " " + " ".join(REQUIRED_TEXT) +
            " <section class=\"slide\">p1</section><section class=\"slide\">p2</section>" +
            "<span>01 / 02</span><span>02 / 02</span></body></html>")


def self_test(claims=None):
    """干净必须 PASS；6 种「展示页悄悄变旧 / 变好看」的做法必须 FAIL。"""
    claims = claims or load_claims()
    v = current_values(claims)
    good = minimal_html(v)
    cases = []

    def case(name, text, expect):
        got = scan(text, claims)["status"]
        cases.append({"name": name, "expected": expect, "got": got,
                      "ok": got == expect})

    case("干净展示页必须 PASS", good, "PASS")
    case("抹掉当前测试数 → FAIL",
         good.replace("%s tests OK" % v["tests"], "X"), "FAIL")
    case("塞回旧值 243 tests OK → FAIL", good + " 243 tests OK", "FAIL")
    case("删掉「鲲鹏…未实测」边界 → FAIL",
         good.replace("未实测", "").replace("不写「已适配鲲鹏」", ""), "FAIL")
    case("删掉华为关联页标题 → FAIL",
         good.replace("华为关联 · 可复算证据", ""), "FAIL")
    case("写死 commit hash → FAIL", good + " 在线 main `c56b706`", "FAIL")
    case("页码与 section 数不符 → FAIL",
         good.replace("02 / 02", "02 / 03"), "FAIL")
    return {"status": "PASS" if all(c["ok"] for c in cases) else "FAIL",
            "cases": cases}


def render(rep, selftest=None):
    lines = ["# 评委复验页一致性 · 可执行版", "",
             "> `deliverables/装明白_评委复验页.html` 的每句可量化数字，",
             "> 都与 `data/claims_v0.json` 绑定；改了台账没改这一页，自检第 19 步就红。", "",
             "**%s** ｜ %d 页 ｜ %d 项检查 ｜ 双向断言 %s" %
             (rep["status"], rep["pages"], rep["checks"],
              selftest["status"] if selftest else "未跑"), "",
             "## 当前口径（从台账读，不硬编码）", "",
             "| 项 | 值 |", "|---|---|"]
    for k, val in rep["values"].items():
        lines.append("| %s | `%s` |" % (k, val))
    lines += ["", "## 检查明细", ""] + ["- %s" % n for n in rep["notes"]]
    lines += ["", "## 违规", ""]
    lines += (["- %s" % x for x in rep["violations"]] if rep["violations"]
              else ["- 无"])
    if selftest:
        lines += ["", "## 双向断言", "",
                  "| 故障注入 | 期望 | 实际 | 通过 |", "|---|---|---|---|"]
        for c in selftest["cases"]:
            lines.append("| %s | %s | %s | %s" %
                         (c["name"], c["expected"], c["got"], "✅" if c["ok"] else "❌"),
                         )
        lines += ["", "**自检：%s**" % selftest["status"], ""]
    lines += ["", "## 边界", "",
              "- 判据检查的是**仓库内当前版**，不检查线上 Pages："
              "部署源不在本仓库（Pages API 404），无法随代码覆盖更新。",
              "- `装明白_评委复验页_10页_历史.pdf` 是 11-07 的旧采样点，按纪律不回填。",
              "- 与仓库外提交包副本的逐字节比对只在本机存在该文件时执行，CI 上如实跳过。",
              ""]
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(description="评委复验页一致性判据")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--no-report", action="store_true")
    args = ap.parse_args()

    st = self_test() if args.self_test else None
    if not SHOWCASE.is_file():
        print("找不到展示页：%s" % SHOWCASE)
        return 2
    claims = load_claims()
    text = SHOWCASE.read_text(encoding="utf-8")
    mirror = (PACKAGE_COPY.read_text(encoding="utf-8")
              if PACKAGE_COPY.is_file() else None)
    rep = scan(text, claims, mirror)
    if st:
        rep["self_test"] = st["status"]
    if not args.no_report:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(render(rep, st), encoding="utf-8")
    if args.json:
        print(json.dumps({"status": rep["status"], "pages": rep["pages"],
                          "checks": rep["checks"],
                          "violations": rep["violations"][:6],
                          "self_test": st["status"] if st else "not_run"},
                         ensure_ascii=False))
    else:
        print(render(rep, st))
    bad = rep["status"] != "PASS" or (st and st["status"] != "PASS")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
