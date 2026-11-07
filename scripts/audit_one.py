# -*- coding: utf-8 -*-
"""审一份报价单，只给一个文件路径就够。

## 为什么需要这个入口

完整的审计要传五份数据（本体/模板/规则/覆盖率/CSV），这是给「换行业」用的，
`scripts/audit_with.py` 那个接口很合适。

但对**智能体**来说五个参数是负担：它只想知道「这个文件有没有问题」。
所以这里给一个默认值全填好的窄入口，一条命令出结果——
码道的 Skill（`.codeartsdoer/skills/zmb-audit`）调的就是它。

## 用法

    python3 scripts/audit_one.py 报价单.csv
    python3 scripts/audit_one.py 报价单.csv --label 甲公司 --template new_house
    python3 scripts/audit_one.py 报价单.csv --scope base      # 基础报价单（不含主材）
    python3 scripts/audit_one.py 报价单.csv --json            # 机器可读

退出码：0 正常；2 输入不是报价单（守门拦下）；1 其他错误。
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
import pipeline as P  # noqa: E402
import doc_gate  # noqa: E402
import report_gate  # noqa: E402

DATA = os.path.join(ROOT, "data")


def _load(name):
    with open(os.path.join(DATA, name), encoding="utf-8") as f:
        return json.load(f)


def main():
    ap = argparse.ArgumentParser(description="审一份装修报价单")
    ap.add_argument("csv", help="报价单文件（CSV / XLSX / PDF → 需先转 CSV）")
    ap.add_argument("--label", default="", help="单据名称，默认取文件名")
    ap.add_argument("--template", default="auto", choices=["auto", "old_house", "new_house"],
                    help="标准工序模板：老房翻新 / 新房毛坯；auto 按行数猜")
    ap.add_argument("--scope", default="full", choices=["full", "base"],
                    help="full=全包口径；base=基础施工（主材不参与判漏）")
    ap.add_argument("--json", action="store_true", help="输出 JSON 而不是 Markdown")
    args = ap.parse_args()

    label = args.label or os.path.splitext(os.path.basename(args.csv))[0]
    onto = P.Ontology(os.path.join(DATA, "ontology_v0.json"))
    tpl = _load("templates_v0.json")
    rules = _load("rules_v0.json")
    cov = _load("coverage_v0.json")

    # 守门：解析前先判这是不是报价单。合同/清单要拦在解析之前，
    # 否则会以「没找到项目名称列」这种内部错误冒出来。
    ok, info = doc_gate.gate_file(args.csv, label)
    if not ok:
        print("这不是一份可审计的报价单：%s" % info.get("message", ""), file=sys.stderr)
        return 2

    q = P.build_line_index(P.load_quote(args.csv, label), onto)
    tk = args.template
    if tk == "auto":
        tk = "old_house" if len(q["rows"]) >= 80 else "new_house"
    res = P.audit([q], onto, tpl, rules, cov, template_key=tk, scope=args.scope)[label]

    def replay():
        return P.audit([q], onto, tpl, rules, cov, template_key=tk, scope=args.scope)[label]

    gate = report_gate.check(q, res, replay=replay)

    if args.json:
        print(json.dumps({
            "label": label, "template": tk, "scope": args.scope,
            "rows": len(q["rows"]), "total": q["total"],
            "missing_cost": res["missing_cost"], "duplicate_cut": res["duplicate_cut"],
            "verdict": gate["verdict"], "digest": gate["digest"],
            "missing": [{"concept": m["concept"], "tier": m["tier"],
                         "amount": m.get("ref_amount")} for m in res["missing"]],
            "duplicates": [{"concept": d["concept"], "lines": d.get("lines"),
                            "amount": d.get("amount")} for d in res["duplicates"]],
            "risks": [{"rule": x["rule"], "issue": x.get("issue") or x.get("concept"),
                       "line": x.get("line"), "amount": x.get("amount")} for x in res["risks"]],
        }, ensure_ascii=False, indent=2))
        return 0

    print("# 审计结果：%s" % label)
    print()
    print("| 项 | 值 |")
    print("|---|---|")
    print("| 模板 / 口径 | %s / %s |" % (tk, args.scope))
    print("| 工序行数 | %d |" % len(q["rows"]))
    print("| 明细合计 | %s 元 |" % format(q["total"], ",.2f"))
    print("| 可核减漏项 | **%s 元** |" % format(res["missing_cost"], ",.2f"))
    print("| 可核减重复 | **%s 元** |" % format(res["duplicate_cut"], ",.2f"))
    print("| 交付判定 | **%s** |" % gate["verdict"])
    print("| 判定指纹 | `%s` |" % gate["digest"])
    print()
    if res["missing"]:
        print("## 漏项（%d 条，其中 %d 条可进核减）" % (
            len(res["missing"]), sum(1 for m in res["missing"] if m["tier"] == "auto")))
        print()
        print("| 工序 | 档位 | 参照金额 |")
        print("|---|---|---:|")
        for m in res["missing"][:20]:
            amt = m.get("ref_amount")
            print("| %s | %s | %s |" % (m["concept"], m["tier"],
                                        format(amt, ",.2f") if amt else "—"))
    if res["duplicates"]:
        print()
        print("## 重复计价（%d 条）" % len(res["duplicates"]))
        print()
        print("| 工序 | 指向行 | 金额 |")
        print("|---|---|---:|")
        for d in res["duplicates"]:
            print("| %s | %s | %s |" % (d["concept"], "、".join(d.get("lines") or []),
                                        format(d.get("amount") or 0, ",.2f")))
    if res["risks"]:
        print()
        print("## 其他风险（%d 条）" % len(res["risks"]))
        print()
        print("| 规则 | 说明 | 行 | 金额 |")
        print("|---|---|---|---:|")
        for x in res["risks"][:20]:
            print("| %s | %s | %s | %s |" % (
                x["rule"], (x.get("issue") or x.get("concept") or "")[:40],
                x.get("line") or "—",
                format(x["amount"], ",.2f") if x.get("amount") else "—"))
    print()
    print(report_gate.to_markdown(gate))
    return 0


if __name__ == "__main__":
    sys.exit(main())
