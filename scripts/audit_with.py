# -*- coding: utf-8 -*-
"""用指定的「本体 / 模板 / 规则 / 覆盖率」审一份单据。

存在的理由：**证明引擎与领域解耦**。
引擎（src/pipeline.py）不认识「装修」也不认识「汽修」，它只认四种东西：
本体（概念与别名）、模板（该领域什么叫必须做）、规则（怎么判）、覆盖率（文本证据与吸收关系）。
把四份数据换掉，同一套代码就能审另一个行业——这个脚本就是那个「换数据」的入口。

它本身不含任何领域知识：所有领域相关的部分都必须通过参数传进来。

用法（汽修维保）：
    python3 scripts/audit_with.py \
        --ontology data/repair/ontology_repair.json \
        --templates data/repair/templates_repair.json --template major_service \
        --coverage data/repair/coverage_repair.json \
        --csv data/repair/sample_major_service.csv --label 王先生·迈腾2.0T大保养 \
        --out out/repair
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
import pipeline as P  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ontology", required=True)
    ap.add_argument("--templates", required=True)
    ap.add_argument("--template", required=True, help="模板键名，如 major_service")
    ap.add_argument("--rules", default=os.path.join(ROOT, "data", "rules_v0.json"),
                    help="默认复用同一份规则库（规则是领域无关的判定逻辑）")
    ap.add_argument("--coverage", default=None)
    ap.add_argument("--csv", required=True)
    ap.add_argument("--label", default=None)
    ap.add_argument("--scope", default="full", choices=["full", "base"])
    ap.add_argument("--out", default=os.path.join(ROOT, "out"), help="输出目录")
    args = ap.parse_args()

    onto = P.Ontology(args.ontology)
    with open(args.templates, encoding="utf-8") as f:
        templates = json.load(f)
    with open(args.rules, encoding="utf-8") as f:
        rules = json.load(f)
    coverage = None
    if args.coverage:
        with open(args.coverage, encoding="utf-8") as f:
            coverage = json.load(f)

    label = args.label or os.path.splitext(os.path.basename(args.csv))[0]
    q = P.build_line_index(P.load_quote(args.csv, label), onto)
    results = P.audit([q], onto, templates, rules, coverage,
                      template_key=args.template, scope=args.scope)
    res = results[q["label"]]

    aligned = sum(1 for r in q["rows"] if r["cid"])
    auto = [m for m in res["missing"] if m["tier"] == "auto"]
    review = [m for m in res["missing"] if m["tier"] != "auto"]

    print("单据：%s" % label)
    print("本体：%d 概念 / %d 别名｜模板：%s %d 项｜规则：%d 条" % (
        len(onto.by_id), sum(len(c.get("a") or []) for c in onto.items),
        args.template, res["template_size"], len(rules["rules"])))
    print("明细：%d 行 + 汇总 %d 行｜对齐 %d/%d = %.1f%%" % (
        len(q["rows"]), len(q["summary_rows"]), aligned, len(q["rows"]),
        aligned / max(1, len(q["rows"])) * 100))
    print("判定：漏项 %d（确定 %d / 复核 %d）｜重复 %d｜口径风险 %d" % (
        len(res["missing"]), len(auto), len(review), len(res["duplicates"]), len(res["risks"])))
    if res.get("recon") and res["recon"].get("diff") is not None:
        print("对账：明细 %.2f + 取费 %.2f = %.2f｜印刷合计 %.2f｜差 %.2f %s" % (
            res["recon"]["detail_sum"], res["recon"]["fee_sum"],
            res["recon"]["detail_sum"] + res["recon"]["fee_sum"],
            res["recon"]["grand"]["amount"], res["recon"]["diff"],
            "对平" if res["recon"]["ok"] else "不符"))

    print("\n漏项（对照模板）：")
    for m in res["missing"]:
        print("  [%s] %-14s 参照 %s" % (
            m["tier"], m["concept"],
            ("%.0f" % m["ref_amount"]) if m["ref_amount"] else "无（本领域暂无参照价库）"))
    if res["duplicates"]:
        print("\n重复计价：")
        for d in res["duplicates"]:
            print("  %s 行 %s 金额 %.2f｜%s" % (d["concept"], ",".join(d["lines"]),
                                            d["amount"], d["evidence"]["basis"]))
    if res["risks"]:
        print("\n口径/算术风险：")
        for x in res["risks"]:
            print("  [%s] %-10s %s" % (x["tier"], x["rule"][:18], x["issue"][:58]))

    pr = res.get("pricing") or {}
    print("\n计价体系：%s（置信度 %.2f）｜%s" % (pr.get("label"), pr.get("confidence", 0),
                                          pr.get("implication", "")))
    obs = res.get("obligations") or []
    if obs:
        print("\n签约前（送修前）必须书面确认：")
        for o in obs:
            print("  [%s] %s｜涉及 %.2f｜%s" % (o["severity"], o["title"], o["amount"], o["confirm"]))

    os.makedirs(args.out, exist_ok=True)
    html = P.build_html([q], results)
    md = P.build_markdown([q], results)
    stamp = os.path.splitext(os.path.basename(args.csv))[0]
    with open(os.path.join(args.out, stamp + "_report.html"), "w", encoding="utf-8") as f:
        f.write(html)
    with open(os.path.join(args.out, stamp + "_report.md"), "w", encoding="utf-8") as f:
        f.write(md)
    with open(os.path.join(args.out, stamp + "_result.json"), "w", encoding="utf-8") as f:
        json.dump({"label": q["label"], "template": args.template,
                   "digest": P.result_digest(q, res),
                   "missing": [{"concept": m["concept"], "tier": m["tier"]} for m in res["missing"]],
                   "duplicates": [{"concept": d["concept"], "lines": d["lines"]} for d in res["duplicates"]],
                   "recon": res.get("recon"),
                   "pricing": pr.get("label"), "obligations": [o["id"] for o in obs]},
                  f, ensure_ascii=False, indent=1)
    print("\n报告已写入 %s/（html / md / json）" % args.out)


if __name__ == "__main__":
    main()
