# -*- coding: utf-8 -*-
"""生成「报价问询单」：把审计结论变成可以直接发给报价方的逐条问询。

用法：
    python3 scripts/make_inquiry.py --csv data/real/feitai_budget.csv \
        --label 翡翠台 --template old_house --out out
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
import inquiry  # noqa: E402
import pipeline as P  # noqa: E402

DATA = os.path.join(ROOT, "data")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--label", default=None)
    ap.add_argument("--template", default="old_house", choices=["old_house", "new_house"])
    ap.add_argument("--scope", default="full", choices=["full", "base"])
    ap.add_argument("--out", default=os.path.join(ROOT, "out"))
    args = ap.parse_args()

    onto = P.Ontology(os.path.join(DATA, "ontology_v0.json"))
    with open(os.path.join(DATA, "templates_v0.json"), encoding="utf-8") as f:
        templates = json.load(f)
    with open(os.path.join(DATA, "rules_v0.json"), encoding="utf-8") as f:
        rules = json.load(f)
    with open(os.path.join(DATA, "coverage_v0.json"), encoding="utf-8") as f:
        coverage = json.load(f)

    label = args.label or os.path.splitext(os.path.basename(args.csv))[0]
    q = P.build_line_index(P.load_quote(args.csv, label), onto)
    res = P.audit([q], onto, templates, rules, coverage,
                  template_key=args.template, scope=args.scope)[q["label"]]

    inq = inquiry.build(q, res)
    os.makedirs(args.out, exist_ok=True)
    stem = "inquiry_" + os.path.splitext(os.path.basename(args.csv))[0]
    md_path = os.path.join(args.out, stem + ".md")
    html_path = os.path.join(args.out, stem + ".html")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(inquiry.to_markdown(inq))
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(inquiry.to_html(inq))

    print("问询单：%s（可直接发给报价方）" % args.label)
    print("  单据编码 %s ｜ 分隔符 %r" % (q.get("encoding"), q.get("delimiter")))
    print("  共 %d 条问询：" % inq["meta"]["n_items"])
    for s in inq["sections"]:
        print("    %-28s %d 条" % (s["title"], len(s["items"])))
    print("  产出：%s" % md_path)
    print("        %s" % html_path)


if __name__ == "__main__":
    main()
