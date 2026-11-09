#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""校验项目级 Skill 是否符合华为云官方 Agent Skills 规范。

依据：`huaweicloud/huaweicloud-skills`（MIT）各 Skill 的
`references/acceptance-criteria.md` 里反复出现的验收项：

- AC-01 SKILL.md 有 YAML frontmatter：name / description / tags，且**没有 version**
- AC-02 frontmatter 的 name == 目录名
- AC-03 description 含功能摘要 + 触发词
- AC-05 必需章节齐全
- AC-06 `references/iam-policies.md` 存在（最小权限）
- AC-07 `references/cli-installation-guide.md` 存在；质量上报要么实现、要么声明偏离
- AC-08 全目录无硬编码凭据
- AC-09 无跨 Skill 直接调用
- AC-16 SKILL.md ≤ 500 行；文件数 ≤ 30；总体积 ≤ 40 MB
- AC-17 扩展名在白名单内

本 Skill 是**本地确定性审计 Skill，不调用 hcloud CLI、不碰任何云 API**。
所以「CLI 安装」「IAM 策略」「质量上报」三项按官方口径写成**偏离声明**，
而不是假装做了。校验器检查「偏离被写清楚」，不检查「偏离不存在」——
这比硬凑一份假的策略文档诚实。

只读、零依赖、不联网。
"""

import argparse
import copy
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = ROOT / ".codeartsdoer" / "skills" / "zmb-audit"

REQUIRED_REFERENCES = (
    "acceptance-criteria.md",
    "verification-method.md",
    "iam-policies.md",
    "dataflow-diagram.md",
    "cli-installation-guide.md",
)
ALLOWED_EXT = {".md", ".py", ".json", ".sh", ".yaml", ".yml", ".txt"}
MAX_LINES = 500
MAX_FILES = 30
MAX_BYTES = 40 * 1024 * 1024
# 官方要求的六个章节；本 Skill 用中文标题 + 英文别名同时标出
REQUIRED_SECTIONS = (
    ("Overview", "这个技能解决什么"),
    ("Prerequisites", "前置条件"),
    ("Workflow", "怎么做"),
    ("Core Commands", "怎么做"),
    ("Parameter Confirmation", "参数与确认"),
    ("Reference Documents", "Reference Documents"),
)


def load_skill(skill_dir=SKILL_DIR):
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        raise FileNotFoundError("找不到 %s" % skill_md)
    text = skill_md.read_text(encoding="utf-8")
    fm = {}
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.S)
    if m:
        for line in m.group(1).splitlines():
            kv = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
            if kv:
                fm[kv.group(1)] = kv.group(2).strip()
    files = []
    total = 0
    for p in sorted(skill_dir.rglob("*")):
        if p.is_file():
            rel = p.relative_to(skill_dir).as_posix()
            size = p.stat().st_size
            files.append({"path": rel, "ext": p.suffix.lower(), "size": size})
            total += size
    return {
        "dir": skill_dir.name,
        "text": text,
        "lines": len(text.splitlines()),
        "frontmatter": fm,
        "files": files,
        "total_bytes": total,
        "all_text": "\n".join(
            p.read_text(encoding="utf-8", errors="ignore")
            for p in skill_dir.rglob("*") if p.is_file() and p.suffix.lower() in
            {".md", ".py", ".json", ".sh", ".yaml", ".yml", ".txt"}
        ),
    }


def _has(skill, needle):
    return needle in skill["text"]


def run_checks(skill):
    """返回 [(id, 名称, 是否通过, 说明)]。顺序与官方 AC 编号对应。"""
    out = []

    def add(cid, name, ok, detail=""):
        out.append({"id": cid, "name": name, "ok": bool(ok), "detail": detail})

    fm = skill["frontmatter"]
    add("AC-01a", "SKILL.md 有 YAML frontmatter",
        bool(fm), "解析到字段：%s" % ",".join(sorted(fm)))
    # 2026-11-09 对照华为云帮助中心最新字段规范：license / compatibility
    # 已从「可选」变成「建议填」；不新增检查项，合并进 AC-01b，避免项数虚高。
    add("AC-01b", "含 name / description / tags / license / compatibility，且无 version",
        all(k in fm and fm[k] for k in ("name", "description", "tags",
                                        "license", "compatibility"))
        and "version" not in fm,
        "version 必须交给包管理器；license/compatibility 按官方 2026 字段规范补")
    tags = re.findall(r"[\w-]+", fm.get("tags", "").strip("[] "))
    add("AC-01c", "tags ≤ 5 个", 0 < len(tags) <= 5, "tags=%d" % len(tags))
    add("AC-02", "frontmatter.name == 目录名",
        fm.get("name") == skill["dir"],
        "%s vs %s" % (fm.get("name"), skill["dir"]))
    desc = fm.get("description", "")
    add("AC-03", "description 含功能摘要 + 触发词（≥40 字）",
        len(desc) >= 40 and any(w in desc for w in ("当用户", "报价单", "使用")),
        "长度 %d" % len(desc))
    add("AC-05", "官方要求章节齐全",
        all(_has(skill, zh) or _has(skill, en) for en, zh in REQUIRED_SECTIONS),
        "缺：%s" % ",".join(en for en, zh in REQUIRED_SECTIONS
                         if not (_has(skill, zh) or _has(skill, en))) or "无")
    for ref in REQUIRED_REFERENCES:
        add("AC-06/07", "references/%s 存在" % ref,
            any(f["path"] == "references/" + ref for f in skill["files"]))
    # 硬编码凭据：AK/SK 形态，排除「不要写 AK/SK」这类说明性文字
    creds = re.findall(r"(?i)\b(?:AK|SK|access[_-]?key|secret[_-]?key)\b\s*[:=]\s*['\"]?([A-Za-z0-9]{16,})",
                       skill["all_text"])
    add("AC-08", "全目录无硬编码凭据", not creds,
        "命中 %d 处" % len(creds))
    cross = re.findall(r"(?i)(?:use|load|call|invoke)\s+(?:the\s+)?skill\s+[`'\"]?([\w-]+)",
                       skill["all_text"])
    add("AC-09", "无跨 Skill 直接调用", not cross,
        "命中 %s" % (cross[:3] or "无"))
    add("AC-16a", "SKILL.md ≤ %d 行" % MAX_LINES,
        skill["lines"] <= MAX_LINES, "%d 行" % skill["lines"])
    add("AC-16b", "文件数 ≤ %d、体积 ≤ 40MB" % MAX_FILES,
        len(skill["files"]) <= MAX_FILES and skill["total_bytes"] <= MAX_BYTES,
        "%d 个文件 / %d 字节" % (len(skill["files"]), skill["total_bytes"]))
    bad_ext = sorted({f["ext"] for f in skill["files"] if f["ext"] not in ALLOWED_EXT})
    add("AC-17", "扩展名全在白名单内", not bad_ext,
        "越界：%s" % (bad_ext or "无"))
    add("DEV-01", "非 CLI Skill 的偏离被明确声明",
        _has(skill, "偏离声明") and _has(skill, "skill-quality-cli"),
        "没有 hcloud 命令 / 没有云 API / 没有质量上报，都要写出来而不是假装做了")
    add("DEV-02", "验收项在 acceptance-criteria.md 里逐条可核对",
        "AC-01" in skill["all_text"] and "AC-17" in skill["all_text"],
        "规范符合性本身也要能被第三方复算")

    # ---- 功能层（2026-11-09 新增，对照官方四层验收的「功能」层）----
    # 官方 huaweicloud-skills 的验收分结构 / 注册 / 功能 / 结果四层；
    # 我们此前只覆盖了结构 + 文档。「文档里写着一条命令、仓库里没有这个文件」
    # 是 Skill 最常见的腐烂方式——它看起来像规范，跑起来是空头支票。
    def _referenced(skill):
        cmds, docs = set(), set()
        for m in re.finditer(
                r"(?:python3|bash)\s+((?:scripts|eval|src)/[\w\-./]+\.(?:py|sh))",
                skill["all_text"]):
            cmds.add(m.group(1))
        for m in re.finditer(
                r"\b((?:docs|data|eval)/[\w\-./]+\.(?:md|json|csv))",
                skill["all_text"]):
            p = m.group(1)
            if "*" not in p and "<" not in p and "{" not in p:
                docs.add(p)
        return sorted(cmds), sorted(docs)

    cmds, docs = _referenced(skill)
    missing_cmds = [c for c in cmds if not (ROOT / c).is_file()]
    add("AC-18", "SKILL/references 引用的每条命令目标都存在（%d 条）" % len(cmds),
        bool(cmds) and not missing_cmds,
        "缺：%s" % ",".join(missing_cmds) or "全部存在：%s" % ",".join(cmds[:4]) + "…")
    missing_docs = [d for d in docs if not (ROOT / d).is_file()]
    add("AC-19", "引用的仓库内文档 / 数据都存在（%d 条）" % len(docs),
        bool(docs) and not missing_docs,
        "缺：%s" % ",".join(missing_docs) or "全部存在")
    return out


def evaluate(skill):
    checks = run_checks(skill)
    passed = sum(1 for c in checks if c["ok"])
    return {"status": "PASS" if passed == len(checks) else "FAIL",
            "passed": passed, "total": len(checks), "checks": checks}


def self_test(skill):
    """双向断言：干净 Skill 必须 PASS；六种故障必须 FAIL。"""
    cases = []

    def case(name, mutated, expect):
        got = evaluate(mutated)["status"]
        cases.append({"name": name, "expected": expect, "got": got,
                      "ok": got == expect})

    case("干净 Skill 必须 PASS", skill, "PASS")

    m = copy.deepcopy(skill)
    m["frontmatter"]["version"] = "1.0.0"
    case("frontmatter 加 version → FAIL", m, "FAIL")

    m = copy.deepcopy(skill)
    m["frontmatter"].pop("compatibility", None)
    case("删掉 compatibility → FAIL", m, "FAIL")

    m = copy.deepcopy(skill)
    m["frontmatter"]["name"] = "not-the-dir"
    case("name 与目录名不一致 → FAIL", m, "FAIL")

    m = copy.deepcopy(skill)
    m["files"] = [f for f in m["files"] if not f["path"].endswith("iam-policies.md")]
    case("删掉最小权限文档 → FAIL", m, "FAIL")

    m = copy.deepcopy(skill)
    m["all_text"] += "\nAK = ABCDEFGHIJKLMNOP123456\n"
    case("塞入硬编码凭据 → FAIL", m, "FAIL")

    m = copy.deepcopy(skill)
    m["text"] = m["text"].replace("偏离声明", "（删掉了）")
    case("抹掉偏离声明 → FAIL", m, "FAIL")

    m = copy.deepcopy(skill)
    m["lines"] = MAX_LINES + 1
    case("SKILL.md 超 500 行 → FAIL", m, "FAIL")

    # 功能层（2026-11-09 新增）
    m = copy.deepcopy(skill)
    m["all_text"] += "\npython3 scripts/not_exist_tool.py --json\n"
    case("引用不存在的命令 → FAIL", m, "FAIL")

    m = copy.deepcopy(skill)
    m["all_text"] += "\n详见 docs/不存在的文档.md\n"
    case("引用不存在的文档 → FAIL", m, "FAIL")

    return {"status": "PASS" if all(c["ok"] for c in cases) else "FAIL",
            "cases": cases}


def main():
    ap = argparse.ArgumentParser(description="华为云官方 Agent Skills 规范符合性校验")
    ap.add_argument("--json", action="store_true", help="只输出一行 JSON")
    ap.add_argument("--self-test", action="store_true", help="双向断言与故障注入")
    args = ap.parse_args()

    try:
        skill = load_skill()
    except FileNotFoundError as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, ensure_ascii=False))
        return 1

    rep = evaluate(skill)
    st = self_test(skill) if args.self_test else None

    if args.json:
        out = {"status": rep["status"], "passed": rep["passed"], "total": rep["total"],
               "failed": [c["id"] for c in rep["checks"] if not c["ok"]]}
        if st:
            out["self_test"] = st["status"]
        print(json.dumps(out, ensure_ascii=False))
        return 0 if out["status"] == "PASS" and (not st or st["status"] == "PASS") else 1

    print("# 项目 Skill · 华为云官方 Agent Skills 规范符合性\n")
    print("**%s** ｜ %d/%d 项通过 ｜ 目录 `%s`" %
          (rep["status"], rep["passed"], rep["total"], SKILL_DIR.relative_to(ROOT)))
    print("\n| AC | 检查 | 结果 | 说明 |\n|---|---|---|---|")
    for c in rep["checks"]:
        print("| %s | %s | %s | %s |" %
              (c["id"], c["name"], "✅" if c["ok"] else "❌", c["detail"]))
    if st:
        print("\n## 双向断言\n\n| 故障注入 | 期望 | 实际 | 通过 |\n|---|---|---|---|")
        for c in st["cases"]:
            print("| %s | %s | %s | %s |" %
                  (c["name"], c["expected"], c["got"], "✅" if c["ok"] else "❌"))
        print("\n**自检：%s**" % st["status"])
    bad = rep["status"] != "PASS" or (st and st["status"] != "PASS")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
