# -*- coding: utf-8 -*-
"""核对「对方回的新版」有没有真的解决问询单里的问题。

用法：
    python3 scripts/check_followup.py \
        --before data/real/feitai_budget.csv \
        --after  data/samples/feitai_change_demo.csv \
        --out out

产出：
    out/followup_<after>.md     逐条状态（已落实 / 未落实 / 无法自动核对）
    out/followup_<after>.json   同上，机器可读

边界：只核对「问的事实在新版里有没有变化」，不评价答复是否合理，也不判断价格高低。
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
import followup  # noqa: E402
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


def main():
    ap = argparse.ArgumentParser(description="问询落实情况核对")
    ap.add_argument("--before", required=True, help="业主最初收到的那版报价（CSV）")
    ap.add_argument("--after", required=True, help="对方回的新版报价（CSV）")
    ap.add_argument("--label-before", default=None)
    ap.add_argument("--label-after", default=None)
    ap.add_argument("--template", default="old_house", choices=["old_house", "new_house"])
    ap.add_argument("--scope", default="full", choices=["full", "base"])
    ap.add_argument("--out", default=os.path.join(ROOT, "out"))
    ap.add_argument("--json", action="store_true", help="只打印 JSON")
    args = ap.parse_args()

    onto, templates, rules, coverage = _load()

    def load(path, label):
        lb = label or os.path.splitext(os.path.basename(path))[0]
        q = P.build_line_index(P.load_quote(path, lb), onto)
        res = P.audit([q], onto, templates, rules, coverage,
                      template_key=args.template, scope=args.scope)[q["label"]]
        return q, res

    q1, r1 = load(args.before, args.label_before)
    q2, r2 = load(args.after, args.label_after)

    inq = inquiry.build(q1, r1)
    result = followup.check(inq, q2, r2, before_res=r1)
    result["meta"]["before_file"] = os.path.basename(args.before)
    result["meta"]["after_file"] = os.path.basename(args.after)
    result["meta"]["template"] = args.template

    os.makedirs(args.out, exist_ok=True)
    stem = "followup_" + os.path.splitext(os.path.basename(args.after))[0]
    md_path = os.path.join(args.out, stem + ".md")
    js_path = os.path.join(args.out, stem + ".json")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(followup.to_markdown(result))
    with open(js_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    s = result["summary"]
    print("问询落实核对：%s → %s" % (q1["label"], q2["label"]))
    print("  问询 %d 条 ｜ 已落实 %d ｜ 未落实 %d ｜ 无法核对 %d ｜ 落实率 %s" % (
        s["total"], s["resolved"], s["open"], s["unknown"],
        "—" if s["rate"] is None else "%d%%" % round(s["rate"] * 100)))
    print("  总价 %.2f → %.2f（%+.2f）" % (
        result["meta"]["before_total"] or 0, result["meta"]["after_total"] or 0,
        result["meta"]["delta"] or 0))
    if result["new_findings"]:
        print("  新版新增问题 %d 项：" % len(result["new_findings"]))
        for x in result["new_findings"][:5]:
            print("    - %s：%s" % (x["subject"], x["detail"]))
    print("  产出：%s" % md_path)
    print("        %s" % js_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
