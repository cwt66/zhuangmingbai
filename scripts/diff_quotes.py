# -*- coding: utf-8 -*-
"""两版报价对比（命令行）。

用法：
    python3 scripts/diff_quotes.py a.csv b.csv
    python3 scripts/diff_quotes.py a.csv b.csv --md out/diff.md --html out/diff.html

a.csv 是原版、b.csv 是变更后；输出 Markdown 摘要（默认打印）与可选 HTML 报告。
口径见 src/version_diff.py 文件头：只比同一项目、同一计价口径的两版，不做跨公司比价。
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import pipeline as P  # noqa: E402
import version_diff as V  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="两版报价对比")
    ap.add_argument("v1", help="原版 CSV/XLSX")
    ap.add_argument("v2", help="变更后 CSV/XLSX")
    ap.add_argument("--label1", default="原版")
    ap.add_argument("--label2", default="变更后")
    ap.add_argument("--md", help="Markdown 输出路径")
    ap.add_argument("--html", help="HTML 输出路径")
    a = ap.parse_args()

    onto = P.Ontology(os.path.join(ROOT, "data", "ontology_v0.json"))
    q1 = P.build_line_index(P.load_quote(a.v1, a.label1), onto)
    q2 = P.build_line_index(P.load_quote(a.v2, a.label2), onto)
    d = V.diff(q1, q2)

    md = V.to_markdown(d)
    print(md)
    if a.md:
        with open(a.md, "w", encoding="utf-8") as f:
            f.write(md)
        print("saved", a.md)
    if a.html:
        with open(a.html, "w", encoding="utf-8") as f:
            f.write(V.to_html(d))
        print("saved", a.html)

    s = d["summary"]
    if abs(s["unexplained"]) >= 0.01:
        print("[WARN] 差额未对上账：%s 元，请检查是否有未配对行" % s["unexplained"], file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
