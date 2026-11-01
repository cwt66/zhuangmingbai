# -*- coding: utf-8 -*-
"""从本项目已入库的真实单据里，构建「同工序参照价库」。

解决一个具体的产品缺口：单份单据上传时，漏项判定能说出「缺什么」，却说不出
「补上要花多少钱」——因为原来的参照金额只能取自同一批的其他报价。
有了这个库，单份上传也能给出量级估算。

**必须说清楚的三条边界（写进报告，不让用户误解）**：
1. 这是「本项目 4 份家装公开单据的同工序金额中位数」，**不是市场行情价**；
2. 工装单据（深圳新源诊所）不参与，避免工装/家装单价混池；
3. 只做量级参照，不做「贵不贵」判断——本项目红线。

用法：
    python3 scripts/build_reference_prices.py
产物：data/reference_prices.json
"""
import json
import os
import statistics
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
import pipeline as P  # noqa: E402

DATA = os.path.join(ROOT, "data")
OUT = os.path.join(DATA, "reference_prices.json")

# 只用家装单据建库（工装单价体系不同，混池会失真）
SOURCES = [
    ("篱笆·卓观（老房半包）", "data/real/sh_zhuoguan_halfpack.csv"),
    ("花溪北岸（新房）", "data/real/huaxibeian_newhouse.csv"),
    ("翡翠台（老房全案）", "data/real/feitai_budget.csv"),
    ("124㎡（基础报价）", "data/real/guijiao_124_basic.csv"),
]


def main():
    onto = P.Ontology(os.path.join(DATA, "ontology_v0.json"))
    pool = {}      # cid -> [amount, ...]
    by_doc = {}    # cid -> set(labels)
    for label, rel in SOURCES:
        path = os.path.join(ROOT, rel)
        q = P.build_line_index(P.load_quote(path, label), onto)
        for r in q["rows"]:
            cid = r.get("cid")
            if not cid or r["amount"] <= 0:
                continue
            pool.setdefault(cid, []).append(r["amount"])
            by_doc.setdefault(cid, set()).add(label)

    items = {}
    for cid, vals in pool.items():
        items[cid] = {
            "concept": onto.by_id[cid]["n"] if cid in onto.by_id else cid,
            "amount_median": round(statistics.median(vals), 2),
            "n_docs": len(by_doc[cid]),
            "n_rows": len(vals),
            "amount_range": [round(min(vals), 2), round(max(vals), 2)],
            "from": sorted(by_doc[cid]),
        }

    doc = {
        "meta": {
            "built_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "sources": [s[0] for s in SOURCES],
            "scope": "家装（工装不参与，避免单价体系混池）",
            "semantics": "amount_median = 同一概念在各单据中「该行金额」的中位数；"
                         "不是单价、不是市场行情价，只用于漏项补项的量级参照",
            "caveat": "n_docs < 2 的条目只有单一来源，噪声大，报告里必须显示 n",
            "principle": "不判断贵不贵，只估算「补上这项大概要多少」",
        },
        "items": items,
    }
    json.dump(doc, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    multi = sum(1 for v in items.values() if v["n_docs"] >= 2)
    print("写出 %s" % OUT)
    print("覆盖概念 %d 个（其中 ≥2 份单据互证 %d 个）" % (len(items), multi))
    for cid in ("water_test", "pressure_test", "mgmt_fee", "level_wall", "mesh"):
        it = items.get(cid)
        print("  %-12s %s" % (cid, ("中位 %s 元 / n=%d 份" % (it["amount_median"], it["n_docs"]))
                              if it else "（无样本 → 该漏项仍不给金额，如实标注）"))


if __name__ == "__main__":
    main()
