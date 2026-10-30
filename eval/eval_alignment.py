# -*- coding: utf-8 -*-
"""本体对齐覆盖率评测：用真实项目名检验本体接得住多少

用法：python3 eval/eval_alignment.py
输出：
  out/alignment_report.md   覆盖率 + 未匹配清单（带最近邻建议）
  out/unmatched_terms.csv   未匹配词（用于扩充本体同义词）

口径：
- 命中 exact/alias：算「已覆盖」
- 模糊命中（ratio≥0.62 且 <0.9）：算「疑似覆盖」，需人工确认
- 未命中：算「缺口」，进 unmatched_terms.csv
"""
import csv
import difflib
import json
import os
import sys
from collections import Counter

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "src"))
from pipeline import Ontology, DATA  # noqa: E402


def main():
    onto = Ontology(os.path.join(DATA, "ontology_v0.json"))
    path = os.path.join(DATA, "real", "terms_real.csv")
    rows = list(csv.DictReader(open(path, encoding="utf-8-sig")))
    covered, fuzzy, missing = [], [], []
    for r in rows:
        name = r["项目名称"].strip()
        cid, conf, how = onto.align(name)
        item = {"name": name, "source": r.get("来源", ""), "type": r.get("类型", ""),
                "cid": cid, "conf": conf, "how": how}
        if how in ("exact", "alias"):
            covered.append(item)
        elif how == "fuzzy":
            fuzzy.append(item)
        else:
            # 最近邻建议
            best = sorted(((difflib.SequenceMatcher(None, name, a).ratio(), cid2)
                           for a, cid2 in onto.alias.items()), reverse=True)[:3]
            item["nearest"] = [{"concept": onto.by_id[c]["n"], "ratio": round(s, 2)} for s, c in best if s > 0.4]
            missing.append(item)
    total = len(rows)
    cov = len(covered) / total * 100 if total else 0
    fz = len(fuzzy) / total * 100 if total else 0
    ms = len(missing) / total * 100 if total else 0
    L = ["# 本体对齐覆盖率报告\n",
         f"> 样本：{total} 个真实项目名（来源：什么值得买晒单 2 篇 + 监理/工长文章 1 批）\n",
         f"- 精确/别名命中（已覆盖）：**{len(covered)} 个（{cov:.1f}%）**",
         f"- 模糊命中（疑似覆盖，需人工确认）：{len(fuzzy)} 个（{fz:.1f}%）",
         f"- 未命中（本体缺口）：{len(missing)} 个（{ms:.1f}%）\n",
         "\n## 一、模糊命中（需确认是否同一概念）\n",
         "| 真实名称 | 命中概念 | 相似度 | 来源 |", "|---|---|---:|---|"]
    for x in sorted(fuzzy, key=lambda y: y["conf"]):
        L.append(f"| {x['name']} | {onto.by_id[x['cid']]['n']} | {x['conf']:.2f} | {x['source']} |")
    L.append("\n## 二、本体缺口（按类型分组）\n")
    by_type = Counter(x["type"] for x in missing)
    L.append("| 类型 | 数量 |")
    L.append("|---|---:|")
    for t, n in by_type.most_common():
        L.append(f"| {t or '未标注'} | {n} |")
    L.append("\n| 真实名称 | 最近邻概念（相似度） | 来源 |")
    L.append("|---|---|---|")
    for x in missing:
        near = "、".join(f"{n['concept']}({n['ratio']})" for n in x.get("nearest", [])) or "—"
        L.append(f"| {x['name']} | {near} | {x['source']} |")
    L.append("\n## 三、结论与动作\n")
    L.append(f"- 当前覆盖率 **{cov:.1f}%**；要支撑 W3 的「对齐准确率 ≥90%」，需把缺口与模糊项补进本体同义词。")
    L.append("- 扩充原则：只加真实出现过的叫法（来源可追溯），不做同义词的无限外推。")
    L.append("- 每次扩充后重跑本脚本，覆盖率作为本体版本的回归指标。")
    out = os.path.join(BASE, "out")
    os.makedirs(out, exist_ok=True)
    open(os.path.join(out, "alignment_report.md"), "w", encoding="utf-8").write("\n".join(L))
    with open(os.path.join(out, "unmatched_terms.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["项目名称", "类型", "来源", "最近邻1", "相似度1", "最近邻2", "相似度2"])
        for x in missing:
            n = x.get("nearest", [])
            w.writerow([x["name"], x["type"], x["source"],
                        n[0]["concept"] if len(n) > 0 else "", n[0]["ratio"] if len(n) > 0 else "",
                        n[1]["concept"] if len(n) > 1 else "", n[1]["ratio"] if len(n) > 1 else ""])
    print("\n".join(L))
    print(f"\n[OK] out/alignment_report.md | out/unmatched_terms.csv（覆盖率 {cov:.1f}%）")


if __name__ == "__main__":
    main()
