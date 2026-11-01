# -*- coding: utf-8 -*-
"""黄金标注评测：把「报告里手写的 8/8、8/10」变成可复算的脚本。

口径（必须与材料一致，不许事后换尺子）
------------------------------------
- 只对**有完整黄金标注**的两份单据计算：feitai（老房，39 项）与 guijiao124（新房，28 项）。
- `missing` = 单据里确实没有、模板要求有的工序；`present` = 确实有；
  `not_applicable` = 模板口径不适用（如「贴砖已含找平」），**不计入 FP**，单列。
- 同时报两档：
  - `auto`：只算 tier=auto 的确定性漏项（进核减金额的那一档）；
  - `all`：auto + review（进待确认清单的那一档）。
  两档都报，是因为「精确率」在两种口径下不同，混着说就是选择性展示。
- 置信区间用 Wilson 95%，小样本（n=4/8）下比正态近似可靠。

用法：
    python3 eval/golden_eval.py
    python3 eval/golden_eval.py --json out/golden_eval.json
"""
import argparse
import csv
import json
import math
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "src"))
from pipeline import Ontology, audit, build_line_index, load_quote, DATA  # noqa: E402

DOCS = {
    "feitai": {"csv": "feitai_budget.csv", "template": "old_house", "scope": "full",
               "label": "上海翡翠台 · 老房全案（131 行）"},
    "guijiao124": {"csv": "guijiao_124_basic.csv", "template": "new_house", "scope": "base",
                   "label": "124㎡ · 基础报价单（65 行）"},
}


def wilson(k, n, z=1.96):
    if n == 0:
        return None
    p = k / n
    den = 1 + z * z / n
    mid = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(max(0.0, p * (1 - p) / n + z * z / (4 * n * n))) / den
    return [round(max(0, mid - half), 3), round(min(1, mid + half), 3)]


def load_golden(doc):
    path = os.path.join(BASE, "eval", "golden_labels_%s.csv" % doc)
    return list(csv.DictReader(open(path, encoding="utf-8-sig")))


def evaluate(doc, pred, tier_mode="auto"):
    gold = load_golden(doc)
    gm = {g["cid"] for g in gold if g["golden_state"] == "missing"}
    gp = {g["cid"] for g in gold if g["golden_state"] == "present"}
    gna = {g["cid"] for g in gold if g["golden_state"] == "not_applicable"}
    tp = pred & gm
    fp = pred & gp
    na_hit = pred & gna
    fn = gm - pred
    denom = len(tp) + len(fp)
    precision = round(len(tp) / denom, 3) if denom else None
    recall = round(len(tp) / len(gm), 3) if gm else None
    return {
        "doc": doc, "tier_mode": tier_mode,
        "golden_missing": len(gm), "golden_present": len(gp),
        "golden_not_applicable": len(gna),
        "predicted": len(pred), "tp": len(tp), "fp": len(fp), "fn": len(fn),
        "na_conflict": len(na_hit),
        "precision": precision, "recall": recall,
        "recall_ci": wilson(len(tp), len(gm)),
        "precision_ci": wilson(len(tp), denom),
        "fp_ids": sorted(fp), "fn_ids": sorted(fn), "na_ids": sorted(na_hit),
    }


def run_doc(doc):
    meta = DOCS[doc]
    onto = Ontology(os.path.join(DATA, "ontology_v0.json"))
    templates = json.load(open(os.path.join(DATA, "templates_v0.json"), encoding="utf-8"))
    rules = json.load(open(os.path.join(DATA, "rules_v0.json"), encoding="utf-8"))
    coverage = json.load(open(os.path.join(DATA, "coverage_v0.json"), encoding="utf-8"))
    quote = build_line_index(load_quote(os.path.join(DATA, "real", meta["csv"]), meta["label"]), onto)
    res = audit([quote], onto, templates, rules, coverage,
                template_key=meta["template"], scope=meta["scope"])[quote["label"]]
    missing = res["missing"]
    out = {"doc": doc, "label": meta["label"], "rows": len(quote["rows"]),
           "total": quote["total"], "aligned": sum(1 for r in quote["rows"] if r.get("cid")),
           "duplicates": len(res["duplicates"]), "risks": len(res["risks"])}
    for mode in ("auto", "all"):
        pred = {m["cid"] for m in missing
                if m.get("cid") and (mode == "all" or m.get("tier") == "auto")}
        out[mode] = evaluate(doc, pred, mode)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=os.path.join(BASE, "out", "golden_eval.json"))
    args = ap.parse_args()
    results = [run_doc(d) for d in DOCS]
    # 微平均（两份合并）——小样本下比宏平均更诚实：每份权重按标注项数
    micro = {}
    for mode in ("auto", "all"):
        tp = sum(r[mode]["tp"] for r in results)
        fp = sum(r[mode]["fp"] for r in results)
        fn = sum(r[mode]["fn"] for r in results)
        micro[mode] = {
            "tp": tp, "fp": fp, "fn": fn,
            "precision": round(tp / (tp + fp), 3) if tp + fp else None,
            "recall": round(tp / (tp + fn), 3) if tp + fn else None,
            "recall_ci": wilson(tp, tp + fn),
            "precision_ci": wilson(tp, tp + fp),
        }

    L = ["# 黄金标注评测（自动生成，可复算）\n",
         "> 命令：`python3 eval/golden_eval.py` ｜ 标注：单一标注者 A1（第二标注人待补）\n",
         "## 一、按单据\n",
         "| 单据 | 行数 | 对齐 | 黄金 missing | auto 预测 | TP | FP | FN | auto 精确率 | auto 召回率 |",
         "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for r in results:
        a = r["auto"]
        L.append("| %s | %d | %d/%d | %d | %d | %d | %d | %d | %s | %s |" % (
            r["label"], r["rows"], r["aligned"], r["rows"], a["golden_missing"],
            a["predicted"], a["tp"], a["fp"], a["fn"],
            a["precision"] if a["precision"] is not None else "—",
            a["recall"] if a["recall"] is not None else "—"))
    L.append("\n> auto = 只算 tier=auto 的确定性漏项（进核减金额的那一档）。\n")
    L.append("## 二、两档并报（不许只挑好看的那档）\n")
    L.append("| 单据 | 档位 | 预测 | TP | FP | FN | not_applicable 冲突 | 精确率 [95%CI] | 召回率 [95%CI] |")
    L.append("|---|---|---:|---:|---:|---:|---:|---|---|")
    for r in results:
        for mode in ("auto", "all"):
            x = r[mode]
            L.append("| %s | %s | %d | %d | %d | %d | %d | %s %s | %s %s |" % (
                r["doc"], mode, x["predicted"], x["tp"], x["fp"], x["fn"], x["na_conflict"],
                x["precision"], x["precision_ci"], x["recall"], x["recall_ci"]))
    L.append("\n## 三、微平均（两份合并）\n")
    L.append("| 档位 | TP | FP | FN | 精确率 [95%CI] | 召回率 [95%CI] |")
    L.append("|---|---:|---:|---:|---|---|")
    for mode in ("auto", "all"):
        m = micro[mode]
        L.append("| %s | %d | %d | %d | %s %s | %s %s |" % (
            mode, m["tp"], m["fp"], m["fn"], m["precision"], m["precision_ci"],
            m["recall"], m["recall_ci"]))
    L.append("\n## 四、错误明细（可回原文核对）\n")
    for r in results:
        a = r["auto"]
        L.append("- **%s** FP：%s" % (r["doc"], "、".join(a["fp_ids"]) or "无"))
        L.append("- **%s** FN：%s" % (r["doc"], "、".join(a["fn_ids"]) or "无"))
        if a["na_ids"]:
            L.append("- **%s** not_applicable 冲突：%s（不计 FP，单列）" % (
                r["doc"], "、".join(a["na_ids"])))
    L.append("\n## 五、诚实的边界\n")
    L.append("- n=2 份完整标注，只作**存在性证明**，不能外推为总体召回率。")
    L.append("- 单一标注者 A1；第二标注人到位后，应重算一致率（目标 Cohen's κ ≥ 0.8）。")
    L.append("- `not_applicable` 是模板口径与真实施工口径的分歧，不是模型错误；单列比藏起来诚实。")
    text = "\n".join(L)
    os.makedirs(os.path.dirname(args.json) or ".", exist_ok=True)
    json.dump({"documents": results, "micro": micro}, open(args.json, "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    md = os.path.join(BASE, "out", "golden_eval.md")
    open(md, "w", encoding="utf-8").write(text)
    print(text)
    print("\n[OK] %s | %s" % (md, args.json))


if __name__ == "__main__":
    main()
