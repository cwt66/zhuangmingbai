# -*- coding: utf-8 -*-
"""评测门禁：低于阈值就失败，防止「改一个规则把数字改坏」还合并。

这不是为了刷分，而是把「可复算」变成 CI 的硬约束：
- 黄金标注 auto 精确率/召回率跌破 0.75 → 失败；
- all 档召回跌破 0.90 → 失败；
- 注入实验少于 230/242 → 失败；
- 消融 L2 的 feitai auto 精确率跌破 0.75 → 失败。

用法：
    python3 eval/gate.py
输出：
    out/gate.md
"""
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(BASE, "out")

THRESHOLDS = {
    "auto_precision": 0.75,
    "auto_recall": 0.75,
    "all_recall": 0.90,
    "injection_min": 230,
    "ablation_l2_precision": 0.75,
}


def load(name):
    p = os.path.join(OUT, name)
    if not os.path.exists(p):
        return None
    return json.load(open(p, encoding="utf-8"))


def last_injection():
    d = os.path.join(BASE, "eval", "injection_runs")
    if not os.path.isdir(d):
        return None
    files = sorted(os.path.join(d, f) for f in os.listdir(d) if f.endswith(".json"))
    if not files:
        return None
    try:
        return json.load(open(files[-1], encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def main():
    ge = load("golden_eval.json") or {}
    ab = load("ablation.json") or []
    inj = last_injection() or {}

    checks = []
    micro = ge.get("micro", {})
    auto = micro.get("auto", {})
    alls = micro.get("all", {})
    checks.append(("auto 精确率", auto.get("precision"), THRESHOLDS["auto_precision"], ">="))
    checks.append(("auto 召回率", auto.get("recall"), THRESHOLDS["auto_recall"], ">="))
    checks.append(("all 召回率", alls.get("recall"), THRESHOLDS["all_recall"], ">="))

    # 注入：取最新 run 的 total/detected（字段名兼容不同版本）
    total = inj.get("total") or inj.get("cases") or inj.get("n")
    detected = inj.get("detected") or inj.get("hit") or inj.get("passed")
    if total is None:
        # 有的 run 把结果放在 results 列表里
        results = inj.get("results") or inj.get("cases") or []
        if isinstance(results, list):
            total = len(results)
            detected = sum(1 for x in results if (x.get("detected") if isinstance(x, dict) else bool(x)))
    checks.append(("注入检出", detected, THRESHOLDS["injection_min"], ">="))

    l2 = next((s for s in ab if (s.get("stage") or {}).get("id") == "L2"), None)
    feitai = None
    if l2:
        for d in l2.get("docs", []):
            if d.get("doc") == "feitai":
                feitai = d.get("precision")
    checks.append(("消融 L2 feitai 精确率", feitai, THRESHOLDS["ablation_l2_precision"], ">="))

    L = ["# 评测门禁\n", "| 指标 | 当前值 | 阈值 | 结果 |", "|---|---:|---:|---|"]
    failed = []
    for name, val, th, op in checks:
        ok = val is not None and float(val) >= float(th)
        L.append("| %s | %s | %s %s | %s |" % (name, val, op, th, "PASS" if ok else "FAIL"))
        if not ok:
            failed.append(name)
    # 注入总数也要展示
    if total is not None:
        L.append("| 注入总例数 | %s | — | %s |" % (total, "OK" if int(total) >= 242 else "WARN"))
    L.append("")
    if failed:
        L.append("**失败项：%s**" % "、".join(failed))
    else:
        L.append("全部通过。")
    text = "\n".join(L)
    os.makedirs(OUT, exist_ok=True)
    open(os.path.join(OUT, "gate.md"), "w", encoding="utf-8").write(text)
    print(text)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
