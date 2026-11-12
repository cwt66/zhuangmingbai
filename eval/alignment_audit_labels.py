# -*- coding: utf-8 -*-
"""把对齐抽查的判定算成每档准确率（含 Wilson 95% 区间）。

用法：
    python3 eval/alignment_audit.py --per-bucket 15 --out out/sheet.csv   # 先出抽样表
    python3 eval/alignment_audit_labels.py                                # 再算准确率

判定表在 `data/alignment_audit_labels.json`：key = `档位|名称|概念`。
同 key 重复出现时共用同一判定（同一名称对同一概念，判定不该两样）。
"""
import csv
import json
import math
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "src"))

LABELS = os.path.join(BASE, "data", "alignment_audit_labels.json")
BUCKETS = ("alias", "fuzzy", "contains")


def wilson(k, n, z=1.96):
    if n == 0:
        return None
    p = k / n
    den = 1 + z * z / n
    mid = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(max(0.0, p * (1 - p) / n + z * z / (4 * n * n))) / den
    return [round(max(0.0, mid - half), 3), round(min(1.0, mid + half), 3)]


def main():
    spec = json.load(open(LABELS, encoding="utf-8"))
    labels = spec["labels"]
    sheet_path = os.path.join(BASE, "out", "alignment_audit_sheet.csv")
    if not os.path.isfile(sheet_path):
        print("缺少抽样表：先跑 eval/alignment_audit.py --per-bucket 15")
        return 2
    rows = list(csv.DictReader(open(sheet_path, encoding="utf-8-sig")))

    per = {b: {"n": 0, "correct": 0, "wrong": 0, "unsure": 0, "unlabeled": []} for b in BUCKETS}
    for r in rows:
        b = r["bucket"]
        if b not in per:
            continue
        key = "%s|%s|%s" % (b, r["name"], r["concept"])
        lab = labels.get(key, "")
        per[b]["n"] += 1
        if lab in ("correct", "wrong", "unsure"):
            per[b][lab] += 1
        else:
            per[b]["unlabeled"].append(key)

    print("== 对齐置信度抽查（单标注者，每档 n=15，等距抽样）==")
    total_unlabeled = 0
    for b in BUCKETS:
        s = per[b]
        total_unlabeled += len(s["unlabeled"])
        judged = s["correct"] + s["wrong"]
        acc = (s["correct"] / judged) if judged else None
        ci = wilson(s["correct"], judged) if judged else None
        print("  %-9s n=%-2d 正确 %-2d 错 %-2d 存疑 %-2d ｜ 准确率 %s%s" % (
            b, s["n"], s["correct"], s["wrong"], s["unsure"],
            "—" if acc is None else "%.0f%%" % (acc * 100),
            "" if not ci else "（95%% CI %.0f–%.0f%%）" % (ci[0] * 100, ci[1] * 100)))
        if s["unlabeled"]:
            print("      未标注 %d 条：%s" % (len(s["unlabeled"]), s["unlabeled"][:3]))
    print("  未标注合计：%d（必须为 0 才能引用本结果）" % total_unlabeled)
    return 0 if total_unlabeled == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
