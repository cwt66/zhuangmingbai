# -*- coding: utf-8 -*-
"""把「冻结的预测」和「人工标注」合起来算混淆矩阵。

这是整套评测的判定器：它不产生结论，只负责把两个独立来源对齐后如实算账。

    python3 scripts/eval_audit.py                 # 算所有已标注的单据
    python3 scripts/eval_audit.py --slug sh_zhuoguan

标注文件：eval/labels/<slug>.gold.csv
    列：cid,truth,evidence,annotator,status
    truth: 1 = 原文确实没有任何一处承诺这道工序（真漏项）
           0 = 原文有覆盖（不管是单列还是写在工艺说明/备注里）
    status: confirmed（已确认）/ pending（待仲裁）

关键口径（必须写进报告，否则数字会被误读）：
- 「真漏项」的判据不是「没有这一行」，而是「没有任何一处文字承诺这道工序」。
  例：防水行备注写「施工结束后需做 48 小时闭水实验」→ 闭水不算漏项。
- 因此 L 类指标只对「逐项过了一遍」的标注有效。只复核引擎报出来的项，
  得到的是精确率，不是召回率。
"""
import argparse
import csv
import json
import os
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRED = os.path.join(ROOT, "eval", "predictions")
LABELS = os.path.join(ROOT, "eval", "labels")


def load_labels(slug):
    path = os.path.join(LABELS, slug + ".gold.csv")
    if not os.path.exists(path):
        return None
    out = {}
    for r in csv.DictReader(open(path, encoding="utf-8")):
        cid = (r.get("cid") or "").strip()
        if not cid:
            continue
        out[cid] = {"truth": int(r["truth"]),
                    "evidence": (r.get("evidence") or "").strip(),
                    "annotator": (r.get("annotator") or "").strip(),
                    "status": (r.get("status") or "pending").strip()}
    return out


def evaluate(slug):
    pred_path = os.path.join(PRED, slug + ".json")
    if not os.path.exists(pred_path):
        print("缺少预测快照：%s（先跑 scripts/snapshot_predictions.py）" % pred_path)
        return None
    snap = json.load(open(pred_path, encoding="utf-8"))
    labels = load_labels(slug)
    if labels is None:
        print("%-14s 未标注（缺 eval/labels/%s.gold.csv）" % (slug, slug))
        return None

    items = snap["items"]
    cids = [i["cid"] for i in items]
    missing_label = [c for c in cids if c not in labels]
    if missing_label:
        print("%-14s 标注不全，缺 %d 项：%s" % (slug, len(missing_label), missing_label[:6]))
        return None

    # 混淆矩阵：正类 = 漏项
    tp = fp = fn = tn = 0
    errs = []
    for i in items:
        truth = labels[i["cid"]]["truth"]
        pred = i["decision"] != "present"
        if truth == 1 and pred:
            tp += 1
        elif truth == 0 and pred:
            fp += 1; errs.append(("假阳性", i["concept"], i["decision"], labels[i["cid"]]["evidence"]))
        elif truth == 1 and not pred:
            fn += 1; errs.append(("漏报", i["concept"], "present", labels[i["cid"]]["evidence"]))
        else:
            tn += 1

    # 只看确定档（auto）时的表现：这是真正会进核减金额的那一档
    tp_a = sum(1 for i in items if i["decision"] == "missing_auto" and labels[i["cid"]]["truth"] == 1)
    fp_a = sum(1 for i in items if i["decision"] == "missing_auto" and labels[i["cid"]]["truth"] == 0)

    def ratio(a, b):
        return round(a / b, 3) if b else None

    res = {
        "slug": slug, "doc": snap["meta"]["doc"], "fingerprint": snap["meta"]["fingerprint"],
        "template_key": snap["meta"]["template_key"], "items": len(items),
        "matrix": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
        "precision": ratio(tp, tp + fp), "recall": ratio(tp, tp + fn),
        "f1": ratio(2 * tp, 2 * tp + fp + fn),
        "auto_tier": {"tp": tp_a, "fp": fp_a,
                      "precision": ratio(tp_a, tp_a + fp_a)},
        "errors": errs,
        "annotators": dict(Counter(v["annotator"] for v in labels.values())),
        "pending": sum(1 for v in labels.values() if v["status"] != "confirmed"),
    }
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug")
    args = ap.parse_args()

    slugs = [args.slug] if args.slug else sorted(
        f[:-5] for f in os.listdir(PRED) if f.endswith(".json"))
    rows = []
    for s in slugs:
        r = evaluate(s)
        if r:
            rows.append(r)

    if not rows:
        print("\n结论：目前没有一份单据具备完整标注，因此不能报精确率或召回率。")
        print("这不是程序问题——是标注还没做。已冻结预测，标注补上即可算出。")
        return 0

    print("\n" + "=" * 78)
    print("%-14s %-4s %-22s %-6s %-6s %s" % ("单据", "项数", "混淆矩阵(TP/FP/FN/TN)", "精确率", "召回率", "auto档精确率"))
    print("-" * 78)
    for r in rows:
        m = r["matrix"]
        print("%-14s %-4d %-22s %-6s %-6s %s" % (
            r["slug"], r["items"], "%d/%d/%d/%d" % (m["tp"], m["fp"], m["fn"], m["tn"]),
            r["precision"], r["recall"], r["auto_tier"]["precision"]))

    print("\n标注人：", {r["slug"]: r["annotators"] for r in rows})
    print("待仲裁项：", {r["slug"]: r["pending"] for r in rows})

    print("\n错误明细：")
    for r in rows:
        for kind, concept, decision, ev in r["errors"]:
            print("  %-12s %-6s %-14s %-14s %s" % (r["slug"], kind, concept, decision, ev[:50]))

    print("\n报告写法提醒：")
    print("  · 只有「逐项标注了全部模板项」时，召回率才成立；")
    print("  · 只复核引擎报出来的项 → 只能报精确率；")
    print("  · 必须同时给出预测指纹与标注人，否则数字不可比。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
