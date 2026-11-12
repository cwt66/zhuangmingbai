# -*- coding: utf-8 -*-
"""对齐置信度抽查：每个命中档位抽 N 条，生成人工判定表。

## 为什么先做这件事，而不是先改算法

实测 5 份真实单据 603 行：
    exact 34 / alias 92 / fuzzy 202 / contains 165 / unmatched 110
- 未命中 110 条里，大量是「碳晶板墙板」「发光字」「壁龛制作」这种**本体里没有的概念**——
  换更好的相似度算法也命不中，那是本体缺口，不是检索问题。
- 真正没人验证过的是 fuzzy（202）与 contains（165）：它们进了哪一档、准不准，没有数字。

所以先补「每档的实测准确率」，再决定阈值。没有这一步，任何「提升召回」的说法都是空话。

## 抽样规则（防挑选）

按 (单据, 行号) 排序后等距抽样：`step = max(1, n // k)`，从第 1 条起每隔 step 取一条。
固定输入 + 固定规则 = 可复算；不按「看起来像错的」挑。

用法：
    python3 eval/alignment_audit.py --per-bucket 15 --out out/alignment_audit_sheet.csv
"""
import argparse
import csv
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "src"))
import pipeline as P  # noqa: E402

DATA = os.path.join(BASE, "data")
DOCS = ["sh_zhuoguan_halfpack.csv", "huaxibeian_newhouse.csv", "szxinxin_clinic_pdf.csv",
        "feitai_budget.csv", "guijiao_124_basic.csv"]
BUCKETS = {"fuzzy": ("fuzzy",), "contains": ("contains",), "alias": ("alias",)}


def _bucket(how):
    for name, hows in BUCKETS.items():
        if how in hows:
            return name
    return how


def collect(onto):
    rows = []
    for fn in DOCS:
        q = P.load_quote(os.path.join(DATA, "real", fn), fn)
        for r in q["rows"]:
            name = (r.get("name") or "").strip()
            if not name:
                continue
            cid, conf, how = onto.align(name)
            rows.append({"doc": fn, "no": r.get("no"), "name": name,
                         "bucket": _bucket(how), "concept": onto.by_id[cid]["n"] if cid else "",
                         "cid": cid or "", "conf": conf})
    return rows


def sample(rows, per_bucket):
    out = []
    for bucket in ("alias", "fuzzy", "contains"):
        pool = [r for r in rows if r["bucket"] == bucket]
        if not pool:
            continue
        step = max(1, len(pool) // per_bucket)
        for i in range(0, len(pool), step):
            if len([x for x in out if x["bucket"] == bucket]) >= per_bucket:
                break
            out.append(dict(pool[i], sample_index=i))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-bucket", type=int, default=15)
    ap.add_argument("--out", default=os.path.join(BASE, "out", "alignment_audit_sheet.csv"))
    a = ap.parse_args()

    onto = P.Ontology(os.path.join(DATA, "ontology_v0.json"))
    rows = collect(onto)
    picked = sample(rows, a.per_bucket)
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["doc", "no", "name", "bucket", "concept",
                                          "cid", "conf", "sample_index", "label"])
        w.writeheader()
        for r in picked:
            r["label"] = ""      # 人工填 correct / wrong / unsure
            w.writerow(r)
    print("抽样 %d 条（每档 %d）→ %s" % (len(picked), a.per_bucket, a.out))
    for r in picked:
        print("  [%s] %-28s → %-14s conf=%.2f" % (r["bucket"], r["name"], r["concept"], r["conf"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
