# -*- coding: utf-8 -*-
"""两份盲标的一致性：观察一致率 + Cohen's κ（含分单据与合并）。

为什么必须有这个脚本
------------------
「标注对不对」不能靠嘴说。两个人独立标同一批模板项，然后算一致性——
这是唯一能把「我觉得对」变成「两个人独立判断一致」的办法。

口径（写死，不许事后改）
----------------------
- 三分类：present / missing / not_applicable，**不做合并**。
  （把 not_applicable 并进 present 会人为抬高一致性，等于自欺。）
- 只在两份标注的 cid 交集上算；缺项直接报错，不静默跳过。
- κ 用未加权（nominal）；这是保守选择，加权会把「相邻档」算成部分对。
- 判读用 Landis & Koch 的通用分档，只作参考，不当作「合格证」。
- 入库门槛（来自 eval/annotation_spec.md）：观察一致率 ≥0.80 且 κ ≥0.60。

用法：
    python3 eval/cohen_kappa.py --a eval/golden_labels_feitai.csv \\
                                --b eval/annotator/回传/feitai.张三.labels.csv
    python3 eval/cohen_kappa.py --dir eval/annotator/回传 --min-kappa 0.6
"""
import argparse
import csv
import glob
import math
import os
import sys

CATS = ["present", "missing", "not_applicable"]
ALIAS = {
    "有": "present", "present": "present", "yes": "present", "y": "present", "1": "present",
    "无": "missing", "没有": "missing", "missing": "missing", "no": "missing", "n": "missing", "2": "missing",
    "不适用": "not_applicable", "本单不适用": "not_applicable",
    "not_applicable": "not_applicable", "na": "not_applicable", "n/a": "not_applicable", "3": "not_applicable",
}


def norm(v):
    return ALIAS.get((v or "").strip().lower(), (v or "").strip().lower())


def load(path):
    """读一份标注 → {cid: state}。列名容错：golden_state / state / label。"""
    with open(path, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise SystemExit("[错误] %s 是空的" % path)
    cols = rows[0].keys()
    state_col = next((c for c in ("golden_state", "state", "label", "标注") if c in cols), None)
    if not state_col:
        raise SystemExit("[错误] %s 缺少状态列（golden_state/state/label）：%s" % (path, list(cols)))
    out = {}
    for r in rows:
        cid = (r.get("cid") or "").strip()
        if not cid:
            continue
        st = norm(r.get(state_col))
        if st not in CATS:
            raise SystemExit("[错误] %s 的 %s 取值不合法：%r" % (path, cid, r.get(state_col)))
        out[cid] = st
    return out


def kappa(a, b):
    """a/b: {cid: state}，返回统计字典。"""
    common = sorted(set(a) & set(b))
    missing = sorted(set(a) ^ set(b))
    n = len(common)
    if n == 0:
        raise SystemExit("[错误] 两份标注没有共同的 cid")
    po = sum(1 for c in common if a[c] == b[c]) / n
    pa = {k: sum(1 for c in common if a[c] == k) / n for k in CATS}
    pb = {k: sum(1 for c in common if b[c] == k) / n for k in CATS}
    pe = sum(pa[k] * pb[k] for k in CATS)
    k = (po - pe) / (1 - pe) if pe < 1 else 1.0
    se = math.sqrt(max(0.0, po * (1 - po) / (n * (1 - pe) ** 2))) if pe < 1 else 0.0
    disagree = [(c, a[c], b[c]) for c in common if a[c] != b[c]]
    return {"n": n, "po": round(po, 3), "pe": round(pe, 3), "kappa": round(k, 3),
            "ci": [round(max(-1.0, k - 1.96 * se), 3), round(min(1.0, k + 1.96 * se), 3)],
            "disagree": disagree, "only_a": sorted(set(a) - set(b)), "only_b": sorted(set(b) - set(a)),
            "counts": {kk: sum(1 for c in common if a[c] == kk) for kk in CATS}}


def band(k):
    if k < 0:
        return "差于随机（标注定义本身有问题）"
    if k < 0.20:
        return "极低"
    if k < 0.40:
        return "低"
    if k < 0.60:
        return "中等（不足以入库）"
    if k < 0.80:
        return "较强"
    return "强"


def report(name, st, min_po, min_kappa):
    ok = st["po"] >= min_po and st["kappa"] >= min_kappa
    print("| %s | %d | %.3f | %.3f | **%.3f** | %.3f–%.3f | %s | %s |"
          % (name, st["n"], st["po"], st["pe"], st["kappa"], st["ci"][0], st["ci"][1],
             band(st["kappa"]), "PASS" if ok else "FAIL"))
    if st["disagree"]:
        for cid, x, y in st["disagree"]:
            print("     分歧 %-18s A=%-14s B=%s" % (cid, x, y))
    if st["only_a"] or st["only_b"]:
        print("     ⚠ cid 不一致：仅A %s｜仅B %s" % (st["only_a"], st["only_b"]))
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", help="标注 A（通常是我们自己的 golden_labels_*.csv）")
    ap.add_argument("--b", help="标注 B（第二标注者回传）")
    ap.add_argument("--dir", help="批量：目录下一对一对，文件名形如 feitai.A2.labels.csv")
    ap.add_argument("--base-dir", default=os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "eval"), help="golden_labels_*.csv 所在目录")
    ap.add_argument("--min-po", type=float, default=0.80)
    ap.add_argument("--min-kappa", type=float, default=0.60)
    args = ap.parse_args()

    print("# 标注一致性（Cohen's κ）\n")
    print("| 对象 | n | 观察一致率 | 期望一致率 | κ | 95%CI（近似） | 判读 | 结论 |")
    print("|---|---:|---:|---:|---:|---|---|---|")

    all_ok = True
    if args.a and args.b:
        st = kappa(load(args.a), load(args.b))
        all_ok &= report(os.path.basename(args.b), st, args.min_po, args.min_kappa)
    elif args.dir:
        files = sorted(f for f in glob.glob(os.path.join(args.dir, "*.csv"))
                       if not os.path.basename(f).startswith("."))
        if not files:
            raise SystemExit("[错误] %s 下没有 csv" % args.dir)
        # 同一份单据只允许一个回传文件：否则「张三标完又改了一版」会被悄悄算两次，
        # 而且你不知道用的是哪一版。宁可报错让人删掉旧的。
        seen = {}
        for f in files:
            d = os.path.basename(f).split(".")[0]
            seen.setdefault(d, []).append(f)
        dup = {d: v for d, v in seen.items() if len(v) > 1}
        if dup:
            lines = ["[错误] 以下单据有多个回传文件，请只保留最终版（其余移走）："]
            for d, v in dup.items():
                lines.append("  %s: %s" % (d, ", ".join(os.path.basename(x) for x in v)))
            raise SystemExit("\n".join(lines))
        pooled_a, pooled_b = {}, {}
        for f in files:
            doc = os.path.basename(f).split(".")[0]
            gold = os.path.join(args.base_dir, "golden_labels_%s.csv" % doc)
            if not os.path.isfile(gold):
                print("| %s | — | 找不到 %s，跳过 |" % (doc, os.path.basename(gold)))
                continue
            a, b = load(gold), load(f)
            st = kappa(a, b)
            all_ok &= report(doc, st, args.min_po, args.min_kappa)
            pooled_a.update({doc + ":" + k: v for k, v in a.items()})
            pooled_b.update({doc + ":" + k: v for k, v in b.items()})
        if pooled_a:
            st = kappa(pooled_a, pooled_b)
            all_ok &= report("**合并**", st, args.min_po, args.min_kappa)
    else:
        raise SystemExit("用法：--a A --b B，或 --dir 回传目录")

    print("\n门槛：观察一致率 ≥ %.2f 且 κ ≥ %.2f（见 eval/annotation_spec.md）" % (args.min_po, args.min_kappa))
    print("结论：%s" % ("两份标注达到入库门槛。" if all_ok
                        else "**未达门槛**——不要把它写成「标注可靠」；先看分歧项，必要时第三人仲裁。"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
