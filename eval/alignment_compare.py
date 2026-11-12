# -*- coding: utf-8 -*-
"""对齐链路 A/B：现有 `Ontology.align()` vs 加混合检索候选。

口径（必须写死，不许看完结果再换）：
- **覆盖** = exact / alias，可直接采信
- **模糊** = 现有链路 fuzzy / contains，需人工确认
- **候选** = 本轮新增的 hybrid，**单独一列**，不算进覆盖
- **未命中** = 三路都没有

比较只在 `data/real/terms_real.csv`（60 个真实出现过的工序叫法）上做，
外加 5 份真实单据的全部行项（看实际单据里的表现）。

关键约束：**已命中的一律不许改**。脚本会统计「改了目标的条数」，必须是 0，
否则说明这条通道在动别人的结论，不能用。
"""
import argparse
import csv
import json
import os
import sys
from collections import Counter

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "src"))
import align_hybrid  # noqa: E402
import pipeline as P  # noqa: E402

DATA = os.path.join(BASE, "data")
REAL_DOCS = ["sh_zhuoguan_halfpack.csv", "huaxibeian_newhouse.csv",
             "szxinxin_clinic_pdf.csv", "feitai_budget.csv", "guijiao_124_basic.csv"]


def _bucket(how):
    if how in ("exact", "alias"):
        return "covered"
    if how in ("fuzzy", "contains"):
        return "fuzzy"
    if how.startswith("hybrid"):
        return "candidate"
    return "missing"


def run_terms(onto):
    rows = list(csv.DictReader(open(os.path.join(DATA, "real", "terms_real.csv"),
                                    encoding="utf-8-sig")))
    out = []
    for r in rows:
        name = (r.get("项目名称") or "").strip()
        if not name:
            continue
        b_cid, b_conf, b_how = onto.align(name)
        h_cid, h_conf, h_how = align_hybrid.align_with_fallback(onto, name)
        out.append({
            "name": name, "source": r.get("来源", ""),
            "base": {"cid": b_cid, "conf": b_conf, "how": b_how,
                     "bucket": _bucket(b_how),
                     "concept": onto.by_id[b_cid]["n"] if b_cid else None},
            "hybrid": {"cid": h_cid, "conf": h_conf, "how": h_how,
                       "bucket": _bucket(h_how),
                       "concept": onto.by_id[h_cid]["n"] if h_cid else None},
        })
    return out


def run_docs(onto):
    out = []
    for fn in REAL_DOCS:
        path = os.path.join(DATA, "real", fn)
        if not os.path.isfile(path):
            continue
        q = P.load_quote(path, fn)
        for r in q["rows"]:
            name = (r.get("name") or "").strip()
            if not name:
                continue
            b_cid, b_conf, b_how = onto.align(name)
            h_cid, h_conf, h_how = align_hybrid.align_with_fallback(onto, name)
            out.append({"doc": fn, "no": r.get("no"), "name": name,
                        "base": {"bucket": _bucket(b_how), "concept": onto.by_id[b_cid]["n"] if b_cid else None},
                        "hybrid": {"bucket": _bucket(h_how), "concept": onto.by_id[h_cid]["n"] if h_cid else None}})
    return out


def summarize(rows, key="base"):
    c = Counter(r[key]["bucket"] for r in rows)
    n = len(rows) or 1
    return {k: {"n": c.get(k, 0), "pct": round(c.get(k, 0) / n * 100, 1)}
            for k in ("covered", "fuzzy", "candidate", "missing")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out", default=os.path.join(BASE, "out", "alignment_compare.json"))
    a = ap.parse_args()

    onto = P.Ontology(os.path.join(DATA, "ontology_v0.json"))
    terms = run_terms(onto)
    docs = run_docs(onto)

    # 安全阀：已命中被改目标的条数
    changed = [t for t in terms
               if t["base"]["bucket"] in ("covered", "fuzzy")
               and t["hybrid"]["cid"] != t["base"]["cid"]]

    newly = [t for t in terms if t["base"]["bucket"] == "missing"
             and t["hybrid"]["bucket"] == "candidate"]

    report = {
        "terms": {"n": len(terms), "base": summarize(terms, "base"),
                  "hybrid": summarize(terms, "hybrid")},
        "docs": {"n": len(docs), "base": summarize(docs, "base"),
                 "hybrid": summarize(docs, "hybrid")},
        "changed_targets": len(changed),
        "new_candidates": [{"name": t["name"], "concept": t["hybrid"]["concept"],
                            "conf": t["hybrid"]["conf"], "source": t["source"]}
                           for t in newly],
        "still_missing": [{"name": t["name"], "source": t["source"]}
                          for t in terms if t["hybrid"]["bucket"] == "missing"],
    }
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    if a.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    print("== terms_real.csv（%d 条真实叫法）==" % report["terms"]["n"])
    for tag in ("base", "hybrid"):
        s = report["terms"][tag]
        print("  %-7s 覆盖 %3d (%4.1f%%) ｜ 模糊 %2d ｜ 候选 %2d ｜ 未命中 %2d" % (
            tag, s["covered"]["n"], s["covered"]["pct"], s["fuzzy"]["n"],
            s["candidate"]["n"], s["missing"]["n"]))
    print("== 5 份真实单据行项（%d 条）==" % report["docs"]["n"])
    for tag in ("base", "hybrid"):
        s = report["docs"][tag]
        print("  %-7s 覆盖 %3d (%4.1f%%) ｜ 模糊 %2d ｜ 候选 %2d ｜ 未命中 %2d" % (
            tag, s["covered"]["n"], s["covered"]["pct"], s["fuzzy"]["n"],
            s["candidate"]["n"], s["missing"]["n"]))
    print("== 安全阀：已命中被改目标 %d 条（必须为 0）==" % report["changed_targets"])
    if newly:
        print("== 新增候选（需人工确认）==")
        for x in newly:
            print("  %-14s → %-14s conf=%.2f  [%s]" % (
                x["name"], x["concept"], x["conf"], x["source"]))
    print("产出：%s" % a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
