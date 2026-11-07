#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""主张台账：让参赛材料里每一句可量化的主张，都能被一条命令复算。

## 为什么需要它

项目已经有交付门、负向知识库、红队、规则活性——但那些判据管的都是
**引擎输出对不对**。还有一类东西从来没人管：**我们对外说的话对不对**。

一份材料里写着「243 个测试」「119 概念 / 388 别名」「15 步自检」，
代码一改，这些数字就悄悄过期。评委不会去数，我们自己也不会。
华为云社区《一个会自我审计的系统：可信度是怎样被公开建造的》
给的可操作清单第 1 条就是：**给每条断言配上产生它的命令；
写不出来命令的断言，降级成「推测」。** 第 6 条：**证据要分档，
别让「我看过」和「我跑过」长得一样。**

这个脚本就是那两条的落地：

- 台账 `data/claims_v0.json` 里的 `value` 是**公开宣称的值**，不是判据；
  判据只认 `probe` 当场从源头重算出来的 `observed`。
- `occurrences` 用 `{v}` 模板反查文档：改了文档没改台账、改了代码没改文档，
  两个方向都会红。
- 证据分三档：① `ran` 当场跑命令 / ② `read` 读命令生成的产物 /
  ③ `declared` 写不出命令的边界声明。③ 不许混进「已验证」，单列欠账。
- `data/claim_history_v0.json` 是被撤回结论、误报、假发现、预测 MISS 的档案，
  指纹锁在台账里，删一条就红。

## 双向断言

`--self-test` 同时验：干净台账必须 PASS（校验器会说「是」），
六种故障注入必须 FAIL（校验器会说「不」）。
一个永远通过的校验器和一个永远失败的校验器一样没用。

只用 Python 标准库，无凭据、不联网、不启动服务。
"""

import argparse
import ast
import copy
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
LEDGER_PATH = DATA / "claims_v0.json"
HISTORY_PATH = DATA / "claim_history_v0.json"
REPORT_PATH = ROOT / "out" / "claim_ledger.md"

# 5 份真实单据（其余 CSV 是 OCR 对照 / 套餐参照 / 术语表，不算评测样本）
REAL_QUOTES = (
    "sh_zhuoguan_halfpack.csv",
    "huaxibeian_newhouse.csv",
    "szxinxin_clinic_pdf.csv",
    "feitai_budget.csv",
    "guijiao_124_basic.csv",
)

TIER_LABEL = {
    "ran": "① 跑出过结果",
    "read": "② 读过原文",
    "declared": "③ 只按元数据判",
}


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def run(cmd, timeout=240):
    """在仓库根跑一条命令，返回 (exit_code, stdout+stderr)。"""
    try:
        r = subprocess.run(
            cmd, cwd=str(ROOT), shell=isinstance(cmd, str),
            capture_output=True, text=True, timeout=timeout,
        )
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return -1, "命令执行失败：%s" % exc


def _py(expr):
    return [sys.executable, "-c", expr]


# ---------------------------------------------------------------- 探针
# 每个探针返回 (observed: str|None, evidence: str)。
# None 表示「这次没算出值」——对 ② 档是「待复算」，对 ① 档是 FAIL。


def probe_tests_count():
    code, out = run([sys.executable, "-m", "unittest", "discover", "-s", "tests"])
    m = re.search(r"Ran (\d+) tests", out)
    ok = re.search(r"^OK", out, re.M)
    if code != 0 or not m or not ok:
        return None, "unittest 未通过（exit=%s）" % code
    return m.group(1), "Ran %s tests / OK" % m.group(1)


def probe_ontology_concepts():
    return str(len(read_json(DATA / "ontology_v0.json"))), "len(data/ontology_v0.json)"


def probe_ontology_aliases():
    onto = read_json(DATA / "ontology_v0.json")
    return str(sum(len(x.get("a", [])) for x in onto)), "sum(len(concept['a']))"


def probe_rules_count():
    return str(len(read_json(DATA / "rules_v0.json")["rules"])), "len(rules_v0.json['rules'])"


def probe_na_count():
    return str(len(read_json(DATA / "not_applicable_v0.json")["items"])), "len(not_applicable_v0.json['items'])"


def probe_real_quote_files():
    have = [n for n in REAL_QUOTES if (DATA / "real" / n).is_file()]
    return str(len(have)), "存在 %d/%d 份白名单真实单据" % (len(have), len(REAL_QUOTES))


def probe_real_quote_rows():
    import csv
    total = 0
    for name in REAL_QUOTES:
        p = DATA / "real" / name
        if not p.is_file():
            continue
        with open(p, encoding="utf-8-sig", newline="") as f:
            total += max(0, sum(1 for _ in csv.reader(f)) - 1)
    return str(total), "5 份白名单 CSV 的数据行合计（含汇总行）"


def probe_check_all_steps():
    text = (ROOT / "scripts" / "check_all.sh").read_text(encoding="utf-8")
    pairs = re.findall(r'^echo "== (\d+)/(\d+)', text, re.M)
    if not pairs:
        return None, "check_all.sh 里找不到步骤编号"
    denom = pairs[-1][1]
    steps = len(pairs)
    return denom, "%d 条 echo 步骤，末步编号 %s/%s" % (steps, pairs[-1][0], denom)


def probe_gate_checks():
    text = (ROOT / "src" / "report_gate.py").read_text(encoding="utf-8")
    ids = re.findall(r'^\s*\("(D\d+)",', text, re.M)
    return str(len(ids)), "report_gate.CHECKS = %s" % ",".join(ids)


def probe_pipeline_stdlib_only():
    """零第三方包：允许标准库 + 同仓 src/ 内的模块，其余一律算第三方。"""
    tree = ast.parse((ROOT / "src" / "pipeline.py").read_text(encoding="utf-8"))
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    internal = {p.stem for p in (ROOT / "src").glob("*.py")}
    bad = sorted(r for r in roots
                 if r not in sys.stdlib_module_names and r not in internal)
    if bad:
        return "含第三方: %s" % ",".join(bad), "非标准库/非同仓导入：%s" % ",".join(bad)
    return "零第三方依赖", "%d 个导入根，全部是标准库或同仓 src/ 模块" % len(roots)


def probe_redteam_blocked():
    code, out = run([sys.executable, "eval/redteam.py"])
    m = re.search(r"拦住 \*\*(\d+/\d+)\*\*", out)
    if code != 0 or not m:
        return None, "redteam.py 未跑出拦住比例"
    return m.group(1), "红队输出：拦住 **%s**" % m.group(1)


def probe_detection_floor():
    code, out = run([sys.executable, "eval/detection_floor.py"])
    m = re.search(r"最小可靠捕获：\+([\d.]+) 元", out)
    if code != 0 or not m:
        return None, "detection_floor.py 未跑出下限"
    return m.group(1), "最小可靠捕获 +%s 元" % m.group(1)


def probe_skill_spec():
    code, out = run([sys.executable, "scripts/verify_skill_spec.py", "--json"])
    try:
        d = json.loads(out.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None, "verify_skill_spec.py 未输出可解析 JSON"
    return str(d.get("status", "FAIL")), "规范符合性 %d/%d 项通过" % (
        d.get("passed", 0), d.get("total", 0))


def probe_platform_machine():
    """当前进程所在机器的架构（CI 用；不代表开发机）。"""
    return platform.machine(), "platform.machine()"


def probe_dev_arch_record():
    """读开发机架构记录 —— ② 档。

    不能把这条做成 ①：GitHub Actions 跑在 x86_64 上，
    `platform.machine()` 会返回 x86_64，而主张说的是**开发机** arm64。
    用自己的架构去「复算」别人的架构，是拿错的证据盖对的章。
    """
    p = DATA / "dev_environment_v0.json"
    if not p.is_file():
        return None, "开发机架构记录不存在"
    d = read_json(p)
    return d.get("machine"), "data/dev_environment_v0.json（采集命令：%s）" % d.get(
        "captured_by", "?")


def probe_ablation_l2():
    p = ROOT / "out" / "ablation.md"
    if not p.is_file():
        return None, "产物不存在：先跑 python3 eval/ablation.py"
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.startswith("| L2 |") and "| feitai |" in line:
            cols = [c.strip() for c in line.strip().strip("|").split("|")]
            # 级/组件/单据/对齐率/auto预测/TP/FP/FN/精确率/...
            try:
                return "%.3f" % float(cols[8]), "L2 feitai 精确率列（out/ablation.md）"
            except (ValueError, IndexError):
                return None, "out/ablation.md 的 L2 行解析失败"
    return None, "out/ablation.md 里找不到 L2 feitai 行"


def probe_liveness_killed():
    p = ROOT / "out" / "rule_liveness.md"
    if not p.is_file():
        return None, "产物不存在：先跑 python3 eval/rule_liveness.py"
    m = re.search(r"\|\s*被杀（改了就有反应）\s*\|\s*\*\*(\d+)\*\*\s*\|",
                  p.read_text(encoding="utf-8"))
    if not m:
        return None, "out/rule_liveness.md 里找不到被杀数"
    return m.group(1), "规则活性报告：被杀 %s" % m.group(1)


PROBES = {
    "tests_count": probe_tests_count,
    "ontology_concepts": probe_ontology_concepts,
    "ontology_aliases": probe_ontology_aliases,
    "rules_count": probe_rules_count,
    "na_count": probe_na_count,
    "real_quote_files": probe_real_quote_files,
    "real_quote_rows": probe_real_quote_rows,
    "check_all_steps": probe_check_all_steps,
    "gate_checks": probe_gate_checks,
    "pipeline_stdlib_only": probe_pipeline_stdlib_only,
    "redteam_blocked": probe_redteam_blocked,
    "detection_floor": probe_detection_floor,
    "skill_spec": probe_skill_spec,
    "platform_machine": probe_platform_machine,
    "dev_arch_record": probe_dev_arch_record,
    "ablation_l2": probe_ablation_l2,
    "liveness_killed": probe_liveness_killed,
}


# ---------------------------------------------------------------- 校验


def history_digest(history):
    blob = json.dumps(history.get("entries", []), ensure_ascii=False,
                      sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _check_occurrences(claim, root):
    """返回 (ok, details)。{v} 替换成宣称值，用字面包含判断。"""
    details = []
    ok = True
    value = str(claim.get("value", ""))
    for occ in claim.get("occurrences", []):
        path = root / occ["file"]
        pattern = occ["pattern"].replace("{v}", value)
        if not path.is_file():
            ok = False
            details.append({"file": occ["file"], "found": False, "why": "文件不存在"})
            continue
        found = pattern in path.read_text(encoding="utf-8")
        if not found:
            ok = False
        details.append({"file": occ["file"], "pattern": pattern, "found": found})
    if not claim.get("occurrences"):
        ok = False
        details.append({"file": "-", "found": False, "why": "没有登记出现位置"})
    return ok, details


def verify(claims_doc, history_doc, root=ROOT, probe_cache=None):
    """校验台账。返回结构化报告；status=PASS/FAIL。"""
    probe_cache = {} if probe_cache is None else probe_cache
    violations = []
    rows = []
    debts = []

    # 0. 撤回档案完整性：指纹 + 预测 MISS + 每条都要写「为什么留着」
    meta = claims_doc.get("meta", {})
    expected = meta.get("history_digest")
    actual = history_digest(history_doc)
    if not expected or expected == "PENDING":
        violations.append("台账未登记撤回档案指纹（meta.history_digest）")
    elif expected != actual:
        violations.append("撤回档案被改动（指纹不符）——被撤回的结论不许删；"
                          "如确需增补，必须同步更新指纹并写明原因")
    entries = history_doc.get("entries", [])
    if not entries:
        violations.append("撤回档案为空")
    for e in entries:
        for k in ("kind", "was", "now", "why", "kept_because"):
            if not e.get(k):
                violations.append("撤回档案 %s 缺字段 %s" % (e.get("id", "?"), k))
    misses = [p for p in history_doc.get("predictions", []) if p.get("miss") is True]
    if not misses:
        violations.append("预测 MISS 永久保留：档案里一条 MISS 都没有")
    for p in history_doc.get("predictions", []):
        if not p.get("kept_forever"):
            violations.append("预测 %s 未标记 kept_forever" % p.get("id", "?"))

    # 1. 逐条主张
    seen = set()
    for c in claims_doc.get("claims", []):
        cid = c.get("id", "?")
        if cid in seen:
            violations.append("主张 ID 重复：%s" % cid)
        seen.add(cid)
        tier = c.get("tier")
        probe = c.get("probe")
        command = c.get("command")
        declared = str(c.get("value", ""))
        observed, evidence, status = None, "", ""

        if tier not in TIER_LABEL:
            status = "invalid"
            violations.append("%s 的证据档位非法：%s" % (cid, tier))
        elif tier == "declared":
            if probe or command:
                status = "invalid"
                violations.append("%s 标成 ③ 却挂了 probe/command——"
                                  "要么给命令并升级档位，要么老实降级" % cid)
            else:
                status = "declared"
                evidence = "写不出命令的边界声明，不计已验证"
            debts.append({"id": cid, "text": c.get("text", ""), "value": declared})
        else:
            if not probe or probe not in PROBES:
                status = "invalid"
                violations.append("%s 的 ①/② 主张没有可用探针：%s" % (cid, probe))
            elif not command:
                status = "invalid"
                violations.append("%s 没有登记产生它的命令" % cid)
            else:
                key = (probe, cid)
                if key not in probe_cache:
                    try:
                        probe_cache[key] = PROBES[probe]()
                    except Exception as exc:  # 探针自己炸了也要如实报
                        probe_cache[key] = (None, "探针异常：%s" % exc)
                observed, evidence = probe_cache[key]
                if observed is None:
                    if tier == "read":
                        status = "pending"
                        debts.append({"id": cid, "text": c.get("text", ""),
                                      "value": declared, "why": evidence})
                    else:
                        status = "mismatch"
                        violations.append("%s 探针没算出值：%s" % (cid, evidence))
                elif observed != declared:
                    status = "mismatch"
                    violations.append("%s 宣称 %s，实测 %s —— 数字漂了" %
                                      (cid, declared, observed))
                else:
                    status = "verified" if tier == "ran" else "read_verified"

        occ_ok, occ_details = _check_occurrences(c, root)
        if not occ_ok:
            violations.append("%s 的文档出现位置对不上（改了文档没改台账，或反之）：%s" %
                              (cid, [d for d in occ_details if not d["found"]]))
            if status in ("verified", "read_verified", "declared"):
                status = "doc_drift"

        rows.append({
            "id": cid, "text": c.get("text", ""), "tier": tier,
            "tier_label": TIER_LABEL.get(tier, "?"),
            "declared": declared, "observed": observed,
            "status": status, "evidence": evidence,
            "occurrences": occ_details,
        })

    if not seen:
        violations.append("台账里一条主张都没有")

    counts = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    tier_counts = {}
    for r in rows:
        tier_counts[r["tier"]] = tier_counts.get(r["tier"], 0) + 1

    return {
        "status": "FAIL" if violations else "PASS",
        "violations": violations,
        "claims": rows,
        "counts": counts,
        "tier_counts": tier_counts,
        "debts": debts,
        "history_entries": len(entries),
        "misses": len(misses),
        "history_digest": actual,
    }


# ---------------------------------------------------------------- 双向断言


def self_test(claims_doc, history_doc, root=ROOT, probe_cache=None):
    """干净台账必须 PASS；六种故障必须 FAIL。防止「永远通过/永远失败」的校验器。

    probe_cache 可由单测注入，避免单测里再次启动 unittest 造成递归。
    """
    cache = {} if probe_cache is None else probe_cache
    cases = []

    def case(name, doc, hist, expect):
        rep = verify(doc, hist, root=root, probe_cache=cache)
        got = rep["status"]
        cases.append({"name": name, "expected": expect, "got": got,
                      "ok": got == expect,
                      "first": (rep["violations"] or ["-"])[0]})

    case("干净台账必须 PASS（校验器会说「是」）", claims_doc, history_doc, "PASS")

    d = copy.deepcopy(claims_doc)
    d["claims"][0]["value"] = "999999"
    case("把 C01 的宣称数字改错 → FAIL", d, history_doc, "FAIL")

    d = copy.deepcopy(claims_doc)
    d["claims"][1]["occurrences"].append(
        {"file": "README.md", "pattern": "这段字永远不会出现在文档里 XYZ"})
    case("模拟文档漂移（出现位置找不到）→ FAIL", d, history_doc, "FAIL")

    d = copy.deepcopy(claims_doc)
    d["claims"][0].pop("probe")
    case("① 档主张摘掉探针 → FAIL", d, history_doc, "FAIL")

    d = copy.deepcopy(claims_doc)
    for c in d["claims"]:
        if c["tier"] == "declared":
            c["tier"] = "ran"
            break
    case("把 ③ 档偷偷升成 ① 档（不给命令）→ FAIL", d, history_doc, "FAIL")

    h = copy.deepcopy(history_doc)
    h["entries"].pop(0)
    case("删掉一条被撤回的结论 → FAIL", claims_doc, h, "FAIL")

    h = copy.deepcopy(history_doc)
    for p in h["predictions"]:
        p["miss"] = False
    case("抹掉预测 MISS → FAIL", claims_doc, h, "FAIL")

    return {"status": "PASS" if all(c["ok"] for c in cases) else "FAIL", "cases": cases}


# ---------------------------------------------------------------- 输出


def render_markdown(rep, selftest=None):
    lines = [
        "# 主张台账复算报告",
        "",
        "> 每条公开宣称的数字，都要有一条产生它的命令。",
        "> ③ 档只作声明，不计已验证；撤回档案不删，预测 MISS 永久保留。",
        "",
        "**总判定：%s** ｜ 主张 %d 条 ｜ ① %d / ② %d / ③ %d ｜ "
        "撤回档案 %d 条 ｜ 预测 MISS %d 条" % (
            rep["status"], len(rep["claims"]),
            rep["tier_counts"].get("ran", 0), rep["tier_counts"].get("read", 0),
            rep["tier_counts"].get("declared", 0),
            rep["history_entries"], rep["misses"]),
        "",
        "| ID | 主张 | 档位 | 宣称 | 实测 | 判定 |",
        "|---|---|---|---|---|---|",
    ]
    for r in rep["claims"]:
        lines.append("| %s | %s | %s | `%s` | `%s` | %s |" % (
            r["id"], r["text"], r["tier_label"], r["declared"],
            r["observed"] if r["observed"] is not None else "—", r["status"]))
    lines += ["", "## 欠账清单（③ 档 / 待复算，不许写成「已验证」）", ""]
    for d in rep["debts"]:
        lines.append("- **%s** %s（宣称 `%s`）%s" %
                     (d["id"], d["text"], d["value"], d.get("why", "")))
    if not rep["debts"]:
        lines.append("- 无")
    lines += ["", "## 违规", ""]
    for v in rep["violations"]:
        lines.append("- %s" % v)
    if not rep["violations"]:
        lines.append("- 无")
    if selftest:
        lines += ["", "## 双向断言（校验器自己也要被查）", "",
                  "| 故障注入 | 期望 | 实际 | 通过 |", "|---|---|---|---|"]
        for c in selftest["cases"]:
            lines.append("| %s | %s | %s | %s |" %
                         (c["name"], c["expected"], c["got"], "✅" if c["ok"] else "❌"))
        lines.append("")
        lines.append("**自检：%s**" % selftest["status"])
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(description="主张台账复算 / 证据分档 / 撤回档案")
    ap.add_argument("--json", action="store_true", help="只输出一行 JSON")
    ap.add_argument("--self-test", action="store_true", help="双向断言与故障注入")
    ap.add_argument("--probe", help="只跑一个探针并打印结果（调试用）")
    ap.add_argument("--update-digest", action="store_true",
                    help="重算撤回档案指纹并写回台账（增补档案时用，会留痕）")
    ap.add_argument("--no-report", action="store_true", help="不写 out/claim_ledger.md")
    args = ap.parse_args()

    claims_doc = read_json(LEDGER_PATH)
    history_doc = read_json(HISTORY_PATH)

    if args.probe:
        if args.probe not in PROBES:
            print("未知探针：%s" % args.probe)
            return 2
        value, why = PROBES[args.probe]()
        print("%s -> %s (%s)" % (args.probe, value, why))
        return 0

    if args.update_digest:
        claims_doc["meta"]["history_digest"] = history_digest(history_doc)
        with open(LEDGER_PATH, "w", encoding="utf-8") as f:
            json.dump(claims_doc, f, ensure_ascii=False, indent=2)
            f.write("\n")
        print("已更新 meta.history_digest = %s" % claims_doc["meta"]["history_digest"])
        return 0

    # 共用一份探针缓存：--self-test 与正式校验不重复跑那条最贵的 unittest
    cache = {}
    selftest = self_test(claims_doc, history_doc,
                         probe_cache=cache) if args.self_test else None
    rep = verify(claims_doc, history_doc, probe_cache=cache)

    if not args.no_report:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(render_markdown(rep, selftest), encoding="utf-8")

    if args.json:
        out = {"status": rep["status"], "claims": len(rep["claims"]),
               "counts": rep["counts"], "violations": rep["violations"],
               "debts": len(rep["debts"]), "history_entries": rep["history_entries"]}
        if selftest:
            out["self_test"] = selftest["status"]
        print(json.dumps(out, ensure_ascii=False))
    else:
        print(render_markdown(rep, selftest))

    bad = rep["status"] != "PASS" or (selftest and selftest["status"] != "PASS")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
