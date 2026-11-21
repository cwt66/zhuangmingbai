# -*- coding: utf-8 -*-
"""按工序模板为一份新单据生成空白双人标注表（RQAB v0）。

只生成待填写的骨架：cid 与模板必做项一一对应，golden_state 留空，
first/second_annotator 留空，避免标注人看到引擎结论后被污染。

用法：
    python3 benchmark/make_annotation_sheet.py \
      --csv data/real/huaxibeian_newhouse.csv \
      --doc huaxibeian --template new_house \
      --out benchmark/annotations/huaxibeian.labels.csv
"""
import argparse
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "data" / "templates_v0.json"
HEADER = ["cid", "quote_doc", "golden_state", "first_annotator",
          "second_annotator", "arbitrated", "note"]
NOTES = [
    "# golden_state ∈ {present, missing, not_applicable}；present=单据中有该工序（含别名/证据词）",
    "# missing=模板要求但单据中确实没有；not_applicable=该口径下不适用（不计 FP）",
    "# 双人盲标：两人独立填写，互不看对方结果；分歧交第三人仲裁并写入 arbitrated",
    "# 每条结论必须能落到单据行号 + 工序概念 ID；不确定的写「待定」，不要猜",
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True, help="相对仓库根的单据路径")
    ap.add_argument("--doc", required=True, help="单据 ID，如 huaxibeian")
    ap.add_argument("--template", choices=["old_house", "new_house"], required=True)
    ap.add_argument("--out", required=True, help="相对仓库根的输出路径")
    args = ap.parse_args()

    src = ROOT / args.csv
    if not src.is_file():
        raise SystemExit(f"找不到单据：{src}")
    items = json.loads(TEMPLATES.read_text(encoding="utf-8"))[args.template]
    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        for n in NOTES:
            w.writerow([n])
        w.writerow(HEADER)
        for cid in items:
            w.writerow([cid, args.doc, "", "", "", "", ""])
    print(f"✓ {out.relative_to(ROOT)}：{len(items)} 个必做项待标注")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
