# -*- coding: utf-8 -*-
"""生成「同一批报价对照」+ 每家的问询单。

用法：
    python3 scripts/make_batch_inquiry.py \
        --csv "A 公司=data/samples/company_a.csv" \
        --csv "B 公司=data/samples/company_b.csv" \
        --csv "C 公司=data/samples/company_c.csv" \
        --out out

产出（out/ 下）：
    batch_compare.md / .html     三家对照（谁缺什么、谁重复、谁口径不同）
    inquiry_<label>.md           每家一份可直接发出的问询单

边界：只对照同一批、同一模板下的报价；缺项数不是评分，不排名次。
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
import batch_inquiry  # noqa: E402
import inquiry  # noqa: E402
import pipeline as P  # noqa: E402

DATA = os.path.join(ROOT, "data")


def _load():
    onto = P.Ontology(os.path.join(DATA, "ontology_v0.json"))
    with open(os.path.join(DATA, "templates_v0.json"), encoding="utf-8") as f:
        templates = json.load(f)
    with open(os.path.join(DATA, "rules_v0.json"), encoding="utf-8") as f:
        rules = json.load(f)
    with open(os.path.join(DATA, "coverage_v0.json"), encoding="utf-8") as f:
        coverage = json.load(f)
    return onto, templates, rules, coverage


def _parse(spec):
    """`标签=路径` 优先；只给路径时退回文件名。"""
    if "=" in spec:
        label, path = spec.split("=", 1)
    else:
        path = spec
        label = os.path.splitext(os.path.basename(spec))[0]
    return label.strip(), path


def main():
    ap = argparse.ArgumentParser(description="同一批报价对照 + 每家问询单")
    ap.add_argument("--csv", action="append", required=True,
                    help="可重复；写成「标签=路径」时用该标签")
    ap.add_argument("--template", default="old_house", choices=["old_house", "new_house"])
    ap.add_argument("--scope", default="full", choices=["full", "base"])
    ap.add_argument("--out", default=os.path.join(ROOT, "out"))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if len(args.csv) < 2:
        ap.error("至少两家才能对照：--csv 传两次以上")

    onto, templates, rules, coverage = _load()
    specs = [_parse(s) for s in args.csv]
    quotes = [P.build_line_index(P.load_quote(p, lb), onto) for lb, p in specs]
    res = P.audit(quotes, onto, templates, rules, coverage,
                  template_key=args.template, scope=args.scope)

    c = batch_inquiry.compare(quotes, res)
    os.makedirs(args.out, exist_ok=True)
    md_path = os.path.join(args.out, "batch_compare.md")
    html_path = os.path.join(args.out, "batch_compare.html")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(batch_inquiry.to_markdown(c))
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(batch_inquiry.to_html(c))

    written = []
    for q in quotes:
        inq = inquiry.build(q, res[q["label"]], cross_unit=res.get("_cross_unit"))
        safe = "".join(ch for ch in q["label"] if ch not in '/\\:*?"<>|').strip() or "quote"
        p = os.path.join(args.out, "inquiry_%s.md" % safe)
        with open(p, "w", encoding="utf-8") as f:
            f.write(inquiry.to_markdown(inq))
        written.append((q["label"], inq["meta"]["n_items"], p))

    if args.json:
        print(json.dumps({"sections": [s["title"] for s in c["sections"]],
                          "summary": c["summary"],
                          "inquiries": [{"label": l, "items": n} for l, n, _ in written]},
                         ensure_ascii=False, indent=2))
        return 0

    print("同一批对照：%s" % " / ".join(c["meta"]["labels"]))
    for s in c["sections"]:
        print("  %-22s %d 行" % (s["title"], len(s["rows"])))
    print("  每家问询条数：" + "，".join("%s %d 条" % (l, n) for l, n, _ in written))
    print("  产出：%s" % md_path)
    print("        %s" % html_path)
    for _, _, p in written:
        print("        %s" % p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
