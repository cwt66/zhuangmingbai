# -*- coding: utf-8 -*-
"""对照实验统计：读盲评评分表 → 算 M2 可溯源率 / M4 幻觉数 → 出对照表

口径（v1.1，见 eval/对照实验协议_RenoPit.md 的修订记录）：
  traceable = has_line ∈ {yes, n/a} 且 has_basis==yes 且 has_formula==yes
  hallucination = grounded==no（回原文核实不成立）

为什么 has_line 允许 n/a：漏项类结论的主张是「原文没有这一项」，它**本来就没有行号**。
旧口径要求 has_line==yes，等于把「报漏项」这个核心能力直接判成不可溯源——
对所有系统都不公平（RenoPit 也会报漏项）。修正为 n/a 仍要求 has_basis + has_formula，
不放宽「有依据、有算法」这两个实质要求。此修订发生在对手跑批之前，两侧同时适用。

输入：eval/对照实验_评分表.csv
  列：system, doc, item_id, conclusion, has_line, has_basis, has_formula, grounded, note
  system ∈ {renopit, zhuangmingbai}；has_*/grounded ∈ {yes, no, partial}
  * 不是 yes 的一律按 no 计（partial 保守算 no，避免自评放水）

用法：
  python3 eval/ab_tally.py                        # 读默认评分表，输出 stdout
  python3 eval/ab_tally.py --out eval/对照实验结果.md
"""
import argparse
import csv
import os
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT = os.path.join(ROOT, "eval", "对照实验_评分表.csv")


def norm(v):
    return (v or "").strip().lower()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=DEFAULT)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    if not os.path.exists(args.csv):
        raise SystemExit("找不到评分表：%s（先按模板填，模板见 eval/对照实验_评分表.csv）" % args.csv)

    rows = [r for r in csv.DictReader(open(args.csv, encoding="utf-8-sig"))
            if norm(r.get("system")) in ("renopit", "zhuangmingbai")
            and norm(r.get("conclusion"))]

    agg = defaultdict(lambda: {"n": 0, "trace": 0, "halluc": 0,
                               "by_doc": defaultdict(lambda: {"n": 0, "trace": 0, "halluc": 0})})
    for r in rows:
        s = norm(r["system"])
        line_ok = norm(r.get("has_line")) in ("yes", "n/a", "na", "不适用")
        traceable = line_ok and all(
            norm(r.get(k)) == "yes" for k in ("has_basis", "has_formula"))
        halluc = norm(r.get("grounded")) == "no"
        a = agg[s]
        a["n"] += 1
        a["trace"] += int(traceable)
        a["halluc"] += int(halluc)
        d = a["by_doc"][norm(r.get("doc"))]
        d["n"] += 1
        d["trace"] += int(traceable)
        d["halluc"] += int(halluc)

    def pct(x, n):
        return "%.0f%%" % (100.0 * x / n) if n else "—"

    lines = [
        "# 对照实验结果（自动统计）",
        "",
        "> 由 `eval/ab_tally.py` 从盲评评分表生成；口径见 `eval/对照实验协议_RenoPit.md`（预注册）。",
        "> **partial 一律按 no 计**（保守口径，防自评放水）。",
        "> 漏项类结论的本主张是「原文没有这一项」，无行号属正常，`has_line` 记 `n/a` 不扣分，但仍需基准+公式。",
        "",
        "| 指标 | RenoPit | 装明白 |",
        "|---|---:|---:|",
    ]
    rp, zb = agg["renopit"], agg["zhuangmingbai"]
    lines.append("| 结论总数（盲评条数） | %d | %d |" % (rp["n"], zb["n"]))
    lines.append("| **M2 结论可溯源率** | **%s** | **%s** |" % (pct(rp["trace"], rp["n"]), pct(zb["trace"], zb["n"])))
    lines.append("| M4 幻觉条目数（回原文不成立） | %d | %d |" % (rp["halluc"], zb["halluc"]))
    lines.append("")
    lines.append("## 分单据明细")
    lines.append("")
    lines.append("| 单据 | RenoPit 条数 / 可溯源 / 幻觉 | 装明白 条数 / 可溯源 / 幻觉 |")
    lines.append("|---|---|---|")
    docs = sorted(set(list(rp["by_doc"]) + list(zb["by_doc"])))
    for d in docs:
        a, b = rp["by_doc"][d], zb["by_doc"][d]
        lines.append("| %s | %d / %s / %d | %d / %s / %d |" % (
            d, a["n"], pct(a["trace"], a["n"]), a["halluc"],
            b["n"], pct(b["trace"], b["n"]), b["halluc"]))
    lines.append("")
    lines.append("## 诚实边界")
    lines.append("")
    lines.append("- 盲评仍由项目成员执行（无第三方），主观误差存在；原始评分表与两系统输出全部留痕。")
    lines.append("- RenoPit 在线 Demo 的模型版本不受控制，结论仅代表该类路线的公开实现，不代表 LLM 直读路线上界。")
    lines.append("- 单据数受黄金标注人力限制，结论是方向性的，不是统计显著的。")

    out = "\n".join(lines)
    print(out)
    if args.out:
        open(args.out, "w", encoding="utf-8").write(out + "\n")
        print("\n已写入:", args.out)


if __name__ == "__main__":
    main()
