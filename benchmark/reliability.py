# -*- coding: utf-8 -*-
"""可靠性双指标：弃答（abstention）与 pass^k。

借鉴 2026 年的两条方法：
- FinTrace（github.com/FredXing1/FinTrace, Apache-2.0）：不可能任务的正确答案
  是「不知道」；把「承认无法判断」当成可评分行为，而不是失败。
- Tessera / Claw-Eval：headline 用严格 pass^k（k 次全过才算过），
  单次成功只是 capability，不是 reliability。

本脚本不调用模型、不联网，全部用现有确定性守门与引擎。
用法：python3 benchmark/reliability.py
"""
import datetime
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import doc_gate  # noqa: E402
import pipeline  # noqa: E402

REFUSE_TASKS = [
    {"id": "contract_not_quote", "path": "benchmark/fixtures/contract_like.txt",
     "expect": "refuse", "reason": "合同文本没有逐项报价行，解析前必须拒绝"},
    {"id": "irrelevant_text", "path": "benchmark/fixtures/irrelevant.txt",
     "expect": "refuse", "reason": "购物清单不是报价单"},
    {"id": "header_only", "path": "benchmark/fixtures/header_only.csv",
     "expect": "refuse", "reason": "只有表头没有行项，不能出报告"},
]
ACCEPT_TASKS = [
    {"id": "valid_company_a", "path": "data/samples/company_a.csv", "expect": "accept"},
    {"id": "valid_company_b", "path": "data/samples/company_b.csv", "expect": "accept"},
    {"id": "valid_feitai", "path": "data/real/feitai_budget.csv", "expect": "accept"},
]
PASS_K_DOCS = ["data/samples/company_a.csv", "data/real/feitai_budget.csv"]
K = 3


def verdict(path):
    """返回 (是否拒绝, 理由)。"""
    p = ROOT / path
    if not p.is_file():
        return True, "文件不存在"
    ok, info = doc_gate.gate_file(str(p), p.name)
    if not ok:
        return True, info.get("message") or info.get("label") or "守门拒绝"
    try:
        sys.path.insert(0, str(ROOT / "src"))
        q = pipeline.load_quote(str(p), p.stem)
        if not q.get("rows"):
            return True, "没有解析到任何行项"
    except Exception as e:
        return True, "解析失败: " + str(e)[:120]
    return False, "可接受"


def load_rules():
    onto = pipeline.Ontology(os.path.join(pipeline.DATA, "ontology_v0.json"))
    with open(os.path.join(pipeline.DATA, "templates_v0.json"), encoding="utf-8") as f:
        templates = json.load(f)
    with open(os.path.join(pipeline.DATA, "rules_v0.json"), encoding="utf-8") as f:
        rules = json.load(f)
    cover = os.path.join(pipeline.DATA, "coverage_v0.json")
    coverage = json.load(open(cover, encoding="utf-8")) if os.path.exists(cover) else None
    return onto, templates, rules, coverage


def fingerprint_of(path):
    onto, templates, rules, coverage = load_rules()
    p = ROOT / path
    q = [pipeline.build_line_index(pipeline.load_quote(str(p), p.stem), onto)]
    res = pipeline.audit(q, onto, templates, rules, coverage,
                         template_key="old_house", scope="full")
    return pipeline._report_fingerprint(res)


def main():
    results = {"run_at": datetime.datetime.now().isoformat(timespec="seconds"),
               "python": sys.version.split()[0], "abstention": [], "pass_k": []}

    for task in REFUSE_TASKS + ACCEPT_TASKS:
        refused, why = verdict(task["path"])
        ok = refused if task["expect"] == "refuse" else not refused
        results["abstention"].append({**task, "refused": refused, "reason": why, "correct": ok})

    for path in PASS_K_DOCS:
        fps = []
        for _ in range(K):
            fps.append(fingerprint_of(path))
        results["pass_k"].append({
            "path": path, "k": K, "fingerprints": fps,
            "passed": len(set(fps)) == 1,
        })

    ref = results["abstention"]
    n_refuse = sum(1 for x in ref if x["expect"] == "refuse")
    n_accept = sum(1 for x in ref if x["expect"] == "accept")
    correct_refuse = sum(1 for x in ref if x["expect"] == "refuse" and x["correct"])
    false_refuse = sum(1 for x in ref if x["expect"] == "accept" and not x["correct"])
    results["summary"] = {
        "correct_abstention": f"{correct_refuse}/{n_refuse}",
        "false_refusal": f"{false_refuse}/{n_accept}",
        "abstention_pass": correct_refuse == n_refuse and false_refuse == 0,
        "pass_k": f"{sum(1 for x in results['pass_k'] if x['passed'])}/{len(PASS_K_DOCS)}",
    }

    out = ROOT / "benchmark" / "results"
    out.mkdir(parents=True, exist_ok=True)
    f = out / ("reliability_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S") + ".json")
    f.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(results["summary"], ensure_ascii=False, indent=2))
    print("✓", f.relative_to(ROOT))
    return 0 if results["summary"]["abstention_pass"] and results["summary"]["pass_k"].startswith(f"{len(PASS_K_DOCS)}/") else 1


if __name__ == "__main__":
    raise SystemExit(main())
