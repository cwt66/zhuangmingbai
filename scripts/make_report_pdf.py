# -*- coding: utf-8 -*-
"""命令行生成可分享 PDF 报告（离线可用，不启服务）。

用法：
    python3 scripts/make_report_pdf.py --csv data/real/feitai_budget.csv \\
        --label 翡翠台 --template old_house --scope full --out out/翡翠台_审计报告.pdf
"""
import argparse
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "src"))
import pipeline as P  # noqa: E402
import report_pdf  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--label", default="报价单")
    ap.add_argument("--template", default="old_house", choices=("old_house", "new_house"))
    ap.add_argument("--scope", default="full", choices=("full", "base"))
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    if not report_pdf.available():
        raise SystemExit("需要 reportlab：python3 -m pip install reportlab")
    onto = P.Ontology(os.path.join(P.DATA, "ontology_v0.json"))
    with open(os.path.join(P.DATA, "templates_v0.json"), encoding="utf-8") as f:
        tpl = json.load(f)
    with open(os.path.join(P.DATA, "rules_v0.json"), encoding="utf-8") as f:
        rules = json.load(f)
    with open(os.path.join(P.DATA, "coverage_v0.json"), encoding="utf-8") as f:
        cov = json.load(f)
    q = P.build_line_index(P.load_quote(args.csv, args.label), onto)
    res = P.audit([q], onto, tpl, rules, cov,
                  template_key=args.template, scope=args.scope)
    out = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    report_pdf.export_pdf(out, [q], res, source=args.label)
    print("生成 %s ｜ %d B ｜ 漏项 %d / 重复 %d / 增项预测 %d 项"
          % (out, os.path.getsize(out), len(res[args.label]["missing"]),
             len(res[args.label]["duplicates"]),
             res[args.label]["addon_forecast"]["summary"]["total"]))


if __name__ == "__main__":
    main()
