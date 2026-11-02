# -*- coding: utf-8 -*-
"""本地 OCR CLI：图片 → 识别草稿 CSV（macOS Vision）。

用法：
    bash scripts/build_vision_ocr.sh
    python3 scripts/ocr_vision.py 报价单.jpg --out out/quote_vision.csv
    python3 scripts/ocr_vision.py 报价单.jpg --json out/quote_vision.json

输出：
    CSV（标准 8 列，可直接用 /audit 或 /perceive 继续）
    控制台报告：文本框数 / 行数 / critic 通过 / 需核对 / 量价不符

注意：这是**识别草稿**，不是审计结论。中文可能有错字；缺字段与量价不符的行
会在 --json 里带 _flags，请先校对再拿去判漏项。
"""
import argparse
import csv
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "src"))
import vision_ocr  # noqa: E402
from perception import COLUMNS  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--out", default="")
    ap.add_argument("--json", default="")
    ap.add_argument("--lang", default="zh-Hans")
    args = ap.parse_args()

    ok, why = vision_ocr.available()
    if not ok:
        raise SystemExit("本地 OCR 不可用：%s\n先运行 bash scripts/build_vision_ocr.sh" % why)

    rows, report = vision_ocr.perceive(args.image, lang=args.lang)
    if args.out:
        with open(args.out, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=COLUMNS)
            w.writeheader()
            for r in rows:
                w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in COLUMNS})
    if args.json:
        json.dump({"report": report, "rows": rows}, open(args.json, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("\n%-4s %-24s %-4s %8s %8s %10s  %s" % ("No.", "项目", "单位", "数量", "单价", "合价", "critic"))
    for r in rows:
        print("%-4s %-24s %-4s %8s %8s %10s  %s" % (
            r.get("序号") or "", (r.get("项目名称") or "")[:24], r.get("单位") or "",
            r.get("数量"), r.get("单价"), r.get("合价"), ",".join(r.get("_flags") or [])))
    if args.out:
        print("\n[OK] %s" % args.out)


if __name__ == "__main__":
    main()
