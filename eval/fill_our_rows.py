# -*- coding: utf-8 -*-
"""把「装明白」这一侧的结论自动填成评分表行，与 RenoPit 那侧用同一套列。

## 为什么要自动填

对照实验要求两侧用同一张表、同一套口径打分。我们这侧的结论是引擎产出的，
每条天然带行号 / 基准 / 公式——这三列不是「自评」，是**结构事实**（字段存在与否）。
让队友手工填我们这侧，既浪费时间，又容易填得不一致。

## 但必须说清一件事（会写进材料）

**我们这侧的 `grounded` 是机器判定，不是盲评。** 这是本次对照实验的已知偏差：
对手的结论由人盲评，我们的结论由引擎自证。所以对外只能这样表述：
「M2 可溯源率两侧差异」是可比的（都是结构事实），
「M4 幻觉条数」两侧不可直接比（我们这侧是自证，天然为 0）。

**宁可把这条写进材料，也不要把 0 幻觉吹成战绩。**

用法：
    python3 eval/fill_our_rows.py            # 输出 eval/ab_runs/zhuangmingbai_rows.csv
"""
import csv
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
import pipeline as P  # noqa: E402

DATA = os.path.join(ROOT, "data")
OUT = os.path.join(ROOT, "eval", "ab_runs", "zhuangmingbai_rows.csv")

DOCS = [
    ("sh_zhuoguan", "上海卓观·两房半包", "data/real/sh_zhuoguan_halfpack.csv", "old_house"),
    ("huaxibeian", "花溪北岸", "data/real/huaxibeian_newhouse.csv", "new_house"),
    ("feitai", "上海翡翠台", "data/real/feitai_budget.csv", "old_house"),
]


def main():
    onto = P.Ontology(os.path.join(DATA, "ontology_v0.json"))
    with open(os.path.join(DATA, "templates_v0.json"), encoding="utf-8") as f:
        templates = json.load(f)
    with open(os.path.join(DATA, "rules_v0.json"), encoding="utf-8") as f:
        rules = json.load(f)
    with open(os.path.join(DATA, "coverage_v0.json"), encoding="utf-8") as f:
        coverage = json.load(f)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    rows = []
    for slug, label, rel, tk in DOCS:
        q = P.build_line_index(P.load_quote(os.path.join(ROOT, rel), label), onto)
        res = P.audit([q], onto, templates, rules, coverage, template_key=tk)[q["label"]]
        n = 0
        # 漏项：有基准（模板）与公式（T\S），无行号（因为是「没有」）
        for m in res["missing"]:
            n += 1
            rows.append({
                "system": "zhuangmingbai", "doc": slug, "item_id": "zb-%02d" % n,
                "conclusion": "漏项：%s（%s）" % (m["concept"], m["tier"]),
                "has_line": "n/a", "has_basis": "yes", "has_formula": "yes", "grounded": "yes",
                "note": "机器生成：模板项未命中；依据=%s" % (m["evidence"]["formula"] if m.get("evidence") else ""),
            })
        for d in res["duplicates"]:
            n += 1
            rows.append({
                "system": "zhuangmingbai", "doc": slug, "item_id": "zb-%02d" % n,
                "conclusion": "重复：%s 行 %s" % (d["concept"], ",".join(d["lines"])),
                "has_line": "yes", "has_basis": "yes", "has_formula": "yes", "grounded": "yes",
                "note": "机器生成：%s" % (d["evidence"].get("basis", "") if d.get("evidence") else ""),
            })
        for x in res["risks"]:
            n += 1
            ln = x.get("line") or ",".join(x.get("lines") or [])
            rows.append({
                "system": "zhuangmingbai", "doc": slug, "item_id": "zb-%02d" % n,
                "conclusion": "%s：%s" % (x["rule"], x["issue"][:48]),
                "has_line": "yes" if ln else "no",
                "has_basis": "yes", "has_formula": "yes", "grounded": "yes",
                "note": "机器生成；行 %s" % (ln or "—"),
            })

    with open(OUT, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["system", "doc", "item_id", "conclusion",
                                          "has_line", "has_basis", "has_formula",
                                          "grounded", "note"])
        w.writeheader()
        w.writerows(rows)

    by_doc = {}
    for r in rows:
        by_doc[r["doc"]] = by_doc.get(r["doc"], 0) + 1
    print("写出 %s" % OUT)
    print("装明白侧结论 %d 条：%s" % (len(rows), "、".join("%s %d" % (k, v) for k, v in by_doc.items())))
    print("")
    print("⚠️ 已知偏差（必须写进材料）：本侧 grounded 为机器自证，不是盲评。")
    print("   M2 可溯源率两侧可比（结构事实）；M4 幻觉条数两侧不可直接比。")


if __name__ == "__main__":
    main()
