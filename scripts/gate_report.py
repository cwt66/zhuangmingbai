# -*- coding: utf-8 -*-
"""报告交付门：5 份真实单据 + 4 个故障注入，一次跑完。

输出分两段，顺序不能换：

1. **真实单据的判定** —— 我们的报告本来是什么状态
2. **故障注入的拦截** —— 证明这道门不是橡皮图章

只跑第 1 段的话，5/5 可交付看着像自夸；只有第 2 段才说明
「可交付」是它**算出来的**，不是它**许愿的**。

用法：
    python3 scripts/gate_report.py            # 全部
    python3 scripts/gate_report.py --json     # 机器可读
"""
import argparse
import copy
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
import pipeline as P  # noqa: E402
import report_gate as G  # noqa: E402

DATA = os.path.join(ROOT, "data")

DOCS = (
    ("sh_zhuoguan", "篱笆·卓观 · 两房半包", "data/real/sh_zhuoguan_halfpack.csv", "old_house"),
    ("huaxibeian", "花溪北岸 · 5 页报价", "data/real/huaxibeian_newhouse.csv", "new_house"),
    ("szxinxin", "深圳新源 · 诊所装饰", "data/real/szxinxin_clinic_pdf.csv", "new_house"),
    ("feitai", "翡翠台 · 老房全案", "data/real/feitai_budget.csv", "old_house"),
    ("guijiao124", "124㎡ · 基础报价", "data/real/guijiao_124_basic.csv", "new_house"),
)

LABEL = {"pass": "可交付", "needs_fix": "需修正", "block": "禁止交付"}


def _env():
    with open(os.path.join(DATA, "templates_v0.json"), encoding="utf-8") as f:
        tpl = json.load(f)
    with open(os.path.join(DATA, "rules_v0.json"), encoding="utf-8") as f:
        rules = json.load(f)
    with open(os.path.join(DATA, "coverage_v0.json"), encoding="utf-8") as f:
        cov = json.load(f)
    return P.Ontology(os.path.join(DATA, "ontology_v0.json")), tpl, rules, cov


def run_real(env):
    onto, tpl, rules, cov = env
    out = []
    for key, label, path, tk in DOCS:
        q = P.build_line_index(P.load_quote(os.path.join(ROOT, path), label), onto)

        def replay(q=q, tk=tk):
            return P.audit([q], onto, tpl, rules, cov, template_key=tk)[q["label"]]

        res = replay()
        gate = G.check(q, res, replay=replay)
        out.append({"key": key, "label": label, "gate": gate})
    return out


def run_injection(env):
    """四个典型破坏，每个都必须被拦。拦不住就是门禁失效，脚本要报错。"""
    onto, tpl, rules, cov = env
    q = P.build_line_index(P.load_quote(os.path.join(ROOT, "data/real/feitai_budget.csv"), "注入"), onto)
    base = P.audit([q], onto, tpl, rules, cov, template_key="old_house")["注入"]

    cases = []

    r = copy.deepcopy(base)
    r["risks"] = [x for x in r["risks"] if not x["rule"].startswith("R13")]
    cases.append(("账目差 11,711 元却删掉 R13 结论", "掩盖对账差异",
                  G.check(q, r), "D3"))

    r = copy.deepcopy(base)
    auto = next(m for m in r["missing"] if m["tier"] == "auto" and m.get("ref_amount"))
    auto["tier"] = "review"
    cases.append(("把一条 auto 降成 review，钱没扣回", "低置信结论混进核减",
                  G.check(q, r), "D5"))

    r = copy.deepcopy(base)
    target = next(m for m in r["missing"] if not m.get("ref_amount"))
    target["ref_amount"] = 8888.0
    target["ref_source"] = []
    target["ref_basis"] = None
    r["missing_cost"] = round(r["missing_cost"] + 8888.0, 2)
    cases.append(("给漏项编 8,888 元、不给来源", "无依据金额",
                  G.check(q, r), "D4"))

    cases.append(("报告指纹与冻结基准不一致", "不可复现",
                  G.check(q, base, expected_digest="deadbeefdeadbeef"), "D6"))

    # D7：100 行各虚 0.99 元 —— 单行低于 R8 的 1 元阈值，累计 99 元低于对账容差
    qd = P.build_line_index(P.load_quote(os.path.join(ROOT, "data/real/feitai_budget.csv"), "漂移"), onto)
    rd = P.audit([qd], onto, tpl, rules, cov, template_key="old_house")["漂移"]
    drift_rows = [r for r in qd["rows"] if r["qty"] > 0 and r["price"] > 0][:100]
    for r in drift_rows:
        r["amount"] = round(r["amount"] + 0.99, 2)
    qd["total"] = round(sum(x["amount"] for x in qd["rows"]), 2)
    qd["recon"] = P.reconcile(qd["rows"], qd.get("summary_rows") or [])
    rd2 = P.audit([qd], onto, tpl, rules, cov, template_key="old_house")["漂移"]
    cases.append(("100 行各虚 0.99 元（累计 99 元）", "系统性小额多收",
                  G.check(qd, rd2), "D7"))

    # 「拦住」= 该检查判失败，无论是 block 还是 needs_fix。
    # 两级都要算，否则 needs_fix 级别的检查会被误记成「失效」。
    return [{"break": b, "risk": risk, "gate": g, "expect": cid,
             "caught": (cid in g["blocking"]) or (cid in g["fixable"]),
             "verdict": g["verdict"]}
            for b, risk, g, cid in cases]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    env = _env()
    real = run_real(env)
    inj = run_injection(env)

    if args.json:
        print(json.dumps({
            "real": [{"key": x["key"], "label": x["label"], "verdict": x["gate"]["verdict"],
                      "blocking": x["gate"]["blocking"], "fixable": x["gate"]["fixable"],
                      "skipped": x["gate"]["skipped"], "digest": x["gate"]["digest"]} for x in real],
            "injection": [{"break": x["break"], "expect": x["expect"], "caught": x["caught"],
                           "verdict": x["verdict"]} for x in inj],
        }, ensure_ascii=False, indent=2))
        return 0

    print("# 报告交付门 · 7 项检查（D1 可追溯 / D2 可复算 / D3 账目闭合 /")
    print("#                        D4 金额有据 / D5 分级守纪 / D6 可复现 /")
    print("#                        D7 无未覆盖偏差）")
    print()
    print("## 一、5 份真实单据")
    print()
    print("| 单据 | 判定 | 阻断项 | 需修正项 | 未验项 | 指纹 |")
    print("|---|---|---|---|---|---|")
    for x in real:
        g = x["gate"]
        print("| %s | **%s** | %s | %s | %s | `%s` |" % (
            x["label"], LABEL[g["verdict"]],
            "、".join(g["blocking"]) or "—",
            "、".join(g["fixable"]) or "—",
            "、".join(g["skipped"]) or "—",
            g["digest"]))

    counts = {}
    for x in real:
        counts[x["gate"]["verdict"]] = counts.get(x["gate"]["verdict"], 0) + 1
    print()
    print("合计：%d/%d 可交付，%d 需修正，%d 禁止交付" % (
        counts.get("pass", 0), len(real),
        counts.get("needs_fix", 0), counts.get("block", 0)))
    if any(x["gate"]["skipped"] for x in real):
        print("（「未验项」是如实标注：这 3 份没有印刷合计行，D3 整单对账无锚点可对，不是跳过检查。）")

    print()
    print("## 二、门禁自检：5 个故障注入")
    print()
    print("| 注入的破坏 | 风险性质 | 期望抓住 | 实际判定 | 结果 |")
    print("|---|---|---|---|---|")
    for x in inj:
        print("| %s | %s | %s | %s | %s |" % (
            x["break"], x["risk"], x["expect"], LABEL[x["verdict"]],
            "✅ 拦住" if x["caught"] else "❌ 漏了"))

    ok = sum(1 for x in inj if x["caught"])
    print()
    print("拦截：%d/%d" % (ok, len(inj)))
    if ok != len(inj):
        print("!! 有破坏没被拦住，门禁失效，必须修")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
