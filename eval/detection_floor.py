# -*- coding: utf-8 -*-
"""检测下限：这套引擎最小能抓住多少钱的错误？

## 为什么要有这个指标

现有的指标（精确率 0.833、召回率 0.833）都依赖**人工标注**，而标注只有 2 份，
置信区间宽到 0.55–0.95，说服力有限。

检测下限不需要标注：**错误是我自己埋的，埋了多少我自己知道**。
一份单据可以生成几十个案例，真值 100% 确定。它衡量的是
「给定一个确定的偏差，引擎能不能抓到、能不能指对行」——
这是**确定性能力**，不是统计推断。

对标 Jessmatth/invoice-auditor 的 `smallest error reliably caught`（5 分钱）。

## 两个维度

1. **单行微调**：把某一行合价改动 δ 元，看 R8 算术校验能不能抓到、指不指得对行。
2. **多行小额累计**：每行各改 0.9 元（低于单行阈值），看整单对账能不能兜住。
   这一维更接近真实套路——没人一次虚报 5000，但 100 行各虚 9 毛就是 90 元，
   而且**每一行单看都是"合理的四舍五入"**。

## 口径纪律

- 只选基线干净的行（原来没有 R8 结论），否则分不清是新增还是原有；
- 「检出」与「定位正确」分开统计 —— 报了但指错行，等于没报；
- 结果不修饰：抓到多少写多少，抓不到就是抓不到。
"""
import argparse
import copy
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
import pipeline as P  # noqa: E402

DATA = os.path.join(ROOT, "data")

DELTAS = (0.01, 0.02, 0.05, 0.10, 0.50, 0.99, 1.00, 1.01, 2.00, 5.00, 10.00)

DOCS = (
    ("feitai", "翡翠台 · 老房全案", "data/real/feitai_budget.csv", "old_house"),
    ("guijiao124", "124㎡ · 基础报价", "data/real/guijiao_124_basic.csv", "new_house"),
)


def _env():
    with open(os.path.join(DATA, "templates_v0.json"), encoding="utf-8") as f:
        tpl = json.load(f)
    with open(os.path.join(DATA, "rules_v0.json"), encoding="utf-8") as f:
        rules = json.load(f)
    with open(os.path.join(DATA, "coverage_v0.json"), encoding="utf-8") as f:
        cov = json.load(f)
    return P.Ontology(os.path.join(DATA, "ontology_v0.json")), tpl, rules, cov


def _run(q, env, tk):
    onto, tpl, rules, cov = env
    return P.audit([q], onto, tpl, rules, cov, template_key=tk)[q["label"]]


def _arith_lines(res):
    """R8 结论指向的行号集合。"""
    out = set()
    for x in res.get("risks") or []:
        if (x.get("rule") or "").startswith("R8"):
            ln = x.get("line")
            if ln:
                out.add(str(ln))
    return out


def probe_single(doc, env):
    """单行微调：每一档改一次，记录检出/定位。"""
    key, label, path, tk = doc
    q0 = P.build_line_index(P.load_quote(os.path.join(ROOT, path), label), env[0])
    base = _run(q0, env, tk)
    clean_before = _arith_lines(base)
    # 只在「原来没有 R8 结论、且量价齐全」的行上做
    targets = [r for r in q0["rows"]
               if r["qty"] > 0 and r["price"] > 0 and str(r["no"]) not in clean_before]
    if not targets:
        return None
    target = max(targets, key=lambda r: r["amount"])

    rows = []
    for delta in DELTAS:
        q = copy.deepcopy(q0)
        for r in q["rows"]:
            if r["no"] == target["no"]:
                r["amount"] = round(r["amount"] + delta, 2)
        q["total"] = round(sum(x["amount"] for x in q["rows"]), 2)
        q["recon"] = P.reconcile(q["rows"], q.get("summary_rows") or [])
        res = _run(q, env, tk)
        hit = _arith_lines(res)
        detected = bool(hit - clean_before)
        located = str(target["no"]) in hit
        r13 = [x for x in res.get("risks") or [] if (x.get("rule") or "").startswith("R13")]
        base_diff = (base.get("recon") or {}).get("diff")
        now_diff = (res.get("recon") or {}).get("diff")
        moved = (base_diff is not None and now_diff is not None
                 and abs(now_diff - base_diff) > 0.005)
        rows.append({
            "delta": delta, "detected": detected, "located": located,
            "recon_caught": bool(r13) and moved,
            "recon_diff": now_diff, "recon_moved": moved,
        })
    return {"doc": label, "target_line": target["no"], "target_name": target["name"],
            "target_amount": target["amount"], "rows": rows}


def _clean_base(q0, env, tk):
    """把印刷合计改成明细之和，造一份「账目本来就对得上」的基线。

    为什么要造：现有 5 份单据里 3 份没有印刷合计、2 份本来就不平。
    要在「本来平的账」上才能看清小额偏差会不会被兜住——不平的账上，
    新偏差只是让旧差额变大一点，肉眼分不出来。
    """
    q = copy.deepcopy(q0)
    grand = [r for r in (q.get("summary_rows") or [])
             if any(k in r["name"] for k in P.GRAND_KEYWORDS)]
    keep = [r for r in (q.get("summary_rows") or []) if r not in grand]
    # 合计必须 = 明细 + 取费 − 减项，否则造出来的基线自己就不平（第一版踩过）
    fee = sum(r["amount"] for r in keep
              if any(k in r["name"] for k in P.FEE_KEYWORDS))
    disc = sum(r["amount"] for r in keep
               if any(k in r["name"] for k in P.DISCOUNT_KEYWORDS) and r["amount"] < 0)
    total = round(sum(r["amount"] for r in q["rows"]) + fee + disc, 2)
    keep.append({"no": "999", "name": "合计", "spec": "", "unit": "",
                 "qty": 0.0, "price": 0.0, "amount": total})
    q["summary_rows"] = keep
    q["recon"] = P.reconcile(q["rows"], keep)
    return q


def probe_accumulated(doc, env, per_row, n_rows):
    """多行小额累计：每行各改 per_row 元，单行都低于 R8 的 1 元阈值。

    这才是真实套路：没人一次虚报 5000，但 100 行各虚 9 毛就是 90 元，
    而且每一行单看都像"合理的四舍五入"。
    """
    key, label, path, tk = doc
    q0 = P.build_line_index(P.load_quote(os.path.join(ROOT, path), label), env[0])
    base = _clean_base(q0, env, tk)
    base_res = _run(base, env, tk)
    base_ok = (base_res.get("recon") or {}).get("ok")
    base_diff = (base_res.get("recon") or {}).get("diff")
    clean_before = _arith_lines(base_res)

    cands = [r for r in base["rows"]
             if r["qty"] > 0 and r["price"] > 0 and str(r["no"]) not in clean_before]
    picks = {str(r["no"]) for r in cands[:n_rows]}

    q = copy.deepcopy(base)
    for r in q["rows"]:
        if str(r["no"]) in picks:
            r["amount"] = round(r["amount"] + per_row, 2)
    q["total"] = round(sum(x["amount"] for x in q["rows"]), 2)
    q["recon"] = P.reconcile(q["rows"], q.get("summary_rows") or [])
    res = _run(q, env, tk)
    hit = _arith_lines(res)
    rc = res.get("recon") or {}
    gorilla = (rc.get("grand") or {}).get("amount") or 0
    tol = max(1.0, abs(gorilla) * 0.005)
    return {
        "doc": label, "rows_touched": len(picks), "per_row": per_row,
        "injected_total": round(per_row * len(picks), 2),
        "base_diff": base_diff, "base_ok": base_ok,
        "r8_new": len(hit - clean_before),
        "diff_after": rc.get("diff"), "ok_after": rc.get("ok"),
        "tol": round(tol, 1),
        "caught": bool(rc.get("ok") is False),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    env = _env()

    singles = [probe_single(d, env) for d in DOCS]
    singles = [x for x in singles if x]
    acc = []
    for d in DOCS:
        for per_row, n_rows in ((0.50, 10), (0.90, 10), (0.90, 50), (0.99, 100)):
            acc.append(probe_accumulated(d, env, per_row, n_rows))

    if args.json:
        print(json.dumps({"single": singles, "accumulated": acc},
                         ensure_ascii=False, indent=2))
        return 0

    print("# 检测下限：这套引擎最小能抓住多少钱的错误")
    print()
    print("> 真值来源：程序注入（不是人工标注）。每一档跑一次完整审计。")
    print("> 「检出」= 新增一条 R8 结论；「定位」= 该结论指的就是被改的那一行。")
    print()

    for s in singles:
        print("## %s（改动第 %s 行「%s」，原合价 %s 元）"
              % (s["doc"], s["target_line"], s["target_name"], s["target_amount"]))
        print()
        print("| 改动金额 | R8 检出 | 定位正确 | R13 对账是否报警 | 对账差额 |")
        print("|---:|---|---|---|---:|")
        for r in s["rows"]:
            print("| %+.2f 元 | %s | %s | %s | %s |" % (
                r["delta"],
                "✅" if r["detected"] else "—",
                "✅" if r["located"] else "—",
                "✅" if r["recon_caught"] else "—",
                "%.2f" % r["recon_diff"] if r["recon_diff"] is not None else "—"))
        hit = [r["delta"] for r in s["rows"] if r["detected"] and r["located"]]
        print()
        if hit:
            print("**该单最小可靠捕获：%+.2f 元**" % min(hit))
        else:
            print("**该单在测试档位内未捕获任何偏差**")
        print()

    print("## 多行小额累计（在「账目本来就对得上」的单据上测）")
    print()
    print("基线：把印刷合计改成明细之和，使 diff=0、ok=True。")
    print()
    print("| 单据 | 改动行数 | 每行 | 累计虚增 | R8 新增结论 | 注入后对账差额 | 对账容差 | 对账是否报警 |")
    print("|---|---:|---:|---:|---:|---:|---:|---|")
    for a in acc:
        print("| %s | %d | %+.2f | %+.2f | %d | %s | ±%.0f 元 | %s |" % (
            a["doc"], a["rows_touched"], a["per_row"], a["injected_total"],
            a["r8_new"],
            "%.2f" % a["diff_after"] if a["diff_after"] is not None else "—",
            a["tol"],
            "✅ 报警" if a["caught"] else "**❌ 抓不到**"))
    blind = [a for a in acc if not a["caught"] and a["r8_new"] == 0]
    print()
    if blind:
        print("**盲区确认：%d / %d 组配置完全抓不到。**" % (len(blind), len(acc)))
        worst = max(blind, key=lambda x: x["injected_total"])
        print("最严重的一组：%s 改 %d 行、每行 %+.2f 元，累计虚增 **%+.2f 元**，"
              "R8 与 R13 双双沉默。"
              % (worst["doc"], worst["rows_touched"], worst["per_row"], worst["injected_total"]))
    else:
        print("所有配置都被至少一条规则兜住。")
    print()
    print("口径说明：")
    print("- R8 判据 `|合价 − 数量×单价| > 1`，单行改动要超过 1 元才报；")
    print("- R13 容差 `max(1.0, |印刷合计| × 0.5%)`，对 15 万的单约 750 元；")
    print("- 两条规则之间是一段空隙：**单行 <1 元、累计 <容差** 的偏差谁也抓不到。")
    print("  这不是 bug，是当前阈值的必然结果；写出来是为了不让它变成「不知道」。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
