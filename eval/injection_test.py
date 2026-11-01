# -*- coding: utf-8 -*-
"""注入实验（Fault Injection）：给真实单据埋已知缺陷，测引擎能不能抓到。

## 为什么要有这个

真实单据的黄金标注只有 2 份（第 4、5 批），而且都是单一标注者——所以「召回率」
一直只能小样本报告。**注入实验换了一条路**：真值不是人标的，是程序埋的——
我删掉哪一行、改哪一列，自己知道。于是：

- 真值规模不受标注人力限制（一份单据能生成几十个案例）
- 每个案例的真值 100% 确定，不依赖标注质量
- 能分开测「发现」和「定位」：报出来了没有 / 报的是不是那一行

**它不是黄金标注的替代品**（埋的缺陷模式由我定义，天然偏向引擎能识别的形态），
两者是互补：黄金标注测「真实世界的脏」，注入实验测「已知缺陷的检出能力」。

## 五类注入（每类都对应一条引擎规则）

| 注入 | 操作 | 期望检出 | 对应规则 |
|---|---|---|---|
| drop_concept | 删掉某工序的全部行 | 该工序成为漏项 | R1 |
| dup_row | 复制一行（同概念同分区） | 判为重复计价 | R3 |
| tamper_amount | 合价 ×1.25 | 算术不符 | R8 |
| zero_qty | 数量改为 0 | 数量/单位缺失 | R9 |
| add_vague | 备注追加「，另计」 | 开口表述 | R4 |

## 实验纪律（写死，防止事后调参）

1. **只选基线干净的目标**——目标若在基线里已被标记，注入不产生增量，
   会被误记成「未检出」。所以选目标前先跑基线，把已标记的排除，并记录排除数。
2. **定位也要判**：报出来但指错行，算「检出未定位」，单独统计，不算全对。
3. **误报增量单独记**：注入后新增的、与目标无关的结论，是注入引起的噪声。
4. 结果不修饰，检出率多少写多少。

用法：
    python3 eval/injection_test.py              # 全部 5 份单据
    python3 eval/injection_test.py --per-doc 12 # 每类每单最多 12 个案例
产物：eval/injection_runs/<时间戳>.md + .json
"""
import argparse
import copy
import json
import os
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
import pipeline as P  # noqa: E402

DATA = os.path.join(ROOT, "data")
OUTDIR = os.path.join(ROOT, "eval", "injection_runs")

DOCS = [
    ("sh_zhuoguan", "篱笆·卓观", "data/real/sh_zhuoguan_halfpack.csv", "old_house"),
    ("huaxibeian", "花溪北岸", "data/real/huaxibeian_newhouse.csv", "new_house"),
    ("szxinxin", "深圳新源·诊所", "data/real/szxinxin_clinic_pdf.csv", "new_house"),
    ("feitai", "翡翠台", "data/real/feitai_budget.csv", "old_house"),
    ("guijiao124", "124㎡", "data/real/guijiao_124_basic.csv", "new_house"),
]


def renumber_if_duplicated(q, slug):
    """分页单据的序号会跨页重复（诊所 PDF：每页从 1 重新编号），行号不再唯一。

    不重编号就没法判断「报出来的到底是不是我注入的那一行」——检测与误报都会失真。
    这里在内存里改成全局唯一，**不改 CSV 原始数据**（原始数据是转录留痕，不能动）。
    这个处理本身暴露了一条产品缺陷：分页单据的定位锚点必须是全局行索引，不能只用原单序号。
    """
    nos = [r["no"] for r in q["rows"]]
    if len(set(nos)) == len(nos):
        return False
    for i, r in enumerate(q["rows"], 1):
        r["no"] = str(i)
    return True


def load_env():
    onto = P.Ontology(os.path.join(DATA, "ontology_v0.json"))
    templates = json.load(open(os.path.join(DATA, "templates_v0.json"), encoding="utf-8"))
    rules = json.load(open(os.path.join(DATA, "rules_v0.json"), encoding="utf-8"))
    coverage = json.load(open(os.path.join(DATA, "coverage_v0.json"), encoding="utf-8"))
    return onto, templates, rules, coverage


def run_audit(q, env, template_key):
    onto, templates, rules, coverage = env
    return P.audit([q], onto, templates, rules, coverage, template_key=template_key)[q["label"]]


def sig_missing(r):
    return {m["cid"] for m in r["missing"] if m["cid"]}


def sig_dup_cids(r):
    """重复计价：概念 → 涉及行号集合。同一概念可能有多条重复结论（如既有同区多行、
    又有工艺包含对），必须取并集——直接赋值会让后一条覆盖前一条，把真检出判成漏检。"""
    out = {}
    for d in r["duplicates"]:
        out.setdefault(d["concept"], set()).update(str(x) for x in d["lines"])
    return out


def sig_risk_lines(r, rule_id):
    """把某条规则命中的行号收成集合。

    注意：聚合后的结论把多个行号塞在同一个 line 字段里（"12,13,14"），
    必须拆开——否则「报出来了但判成没报」，会把引擎判成漏检（假阴性）。
    """
    lines = set()
    for x in r["risks"]:
        if x["rule"] != rule_id:
            continue
        raw = []
        if x.get("line"):
            raw.append(str(x["line"]))
        raw += [str(ln) for ln in (x.get("lines") or [])]
        for chunk in raw:
            for part in chunk.replace("，", ",").split(","):
                part = part.strip()
                if part:
                    lines.add(part)
    return lines


def clone_rows(q):
    q2 = copy.deepcopy(q)
    q2["total"] = round(sum(x["amount"] for x in q2["rows"]), 2)
    return q2


# ---------- 目标选择（全部只选基线干净的） ----------

def targets_drop_concept(q, base, env, template_key):
    onto, templates, rules, coverage = env
    ev = (coverage or {}).get("evidence") or {}
    tmpl = templates[template_key]
    have_missing = sig_missing(base)
    out = []
    for cid in tmpl:
        # 「水电改造」是父类目：引擎按设计做类目级覆盖（子项行还在就不算漏），
        # 删它不产生真漏项，作为注入真值不成立，排除。
        if cid == "mep":
            continue
        rows = [x for x in q["rows"] if x["cid"] == cid]
        if not rows or cid in have_missing:
            continue
        others = [P._row_text(x) for x in q["rows"] if x["cid"] != cid]
        # 别的行的备注里若还有该工序的证据词，删了也不算漏项 → 真值不成立，排除
        if P._evidence_hit(ev.get(cid, []), others):
            continue
        out.append(cid)
    return out


def targets_dup_row(q, base):
    """同概念同分区只有一行时才可注入（已有两行说明基线可能已判重）。"""
    dup_cids = set(sig_dup_cids(base))
    seen = {}
    for r in q["rows"]:
        if not r["cid"] or r["amount"] <= 0:
            continue
        key = (r["cid"], P._zone(r))
        seen.setdefault(key, []).append(r)
    return [rows[0] for key, rows in seen.items()
            if len(rows) == 1 and rows[0]["cid"] not in dup_cids]


def targets_tamper(q, base):
    flagged = sig_risk_lines(base, "R8_arithmetic")
    return [r for r in q["rows"]
            if r["qty"] > 0 and r["price"] > 0 and r["amount"] > 0 and str(r["no"]) not in flagged]


def targets_zero_qty(q, base):
    flagged = sig_risk_lines(base, "R9_qty_unit")
    return [r for r in q["rows"]
            if r["qty"] > 0 and r["amount"] > 0 and str(r["no"]) not in flagged]


def targets_vague(q, base):
    flagged = sig_risk_lines(base, "R4_vague_term")
    return [r for r in q["rows"] if str(r["no"]) not in flagged]


# ---------- 注入执行 ----------

def inject_drop_concept(q, cid, env, template_key):
    q2 = clone_rows(q)
    q2["rows"] = [x for x in q2["rows"] if x["cid"] != cid]
    q2["total"] = round(sum(x["amount"] for x in q2["rows"]), 2)
    return q2


def inject_dup_row(q, row, env, template_key):
    q2 = clone_rows(q)
    clone = dict(row)
    clone["no"] = "%s-r" % row["no"]
    q2["rows"] = q2["rows"] + [clone]
    q2["total"] = round(sum(x["amount"] for x in q2["rows"]), 2)
    return q2


def inject_tamper(q, row, env, template_key):
    q2 = clone_rows(q)
    for x in q2["rows"]:
        if x["no"] == row["no"]:
            x["amount"] = round(x["amount"] * 1.25, 2)
    q2["total"] = round(sum(x["amount"] for x in q2["rows"]), 2)
    return q2


def inject_zero_qty(q, row, env, template_key):
    q2 = clone_rows(q)
    for x in q2["rows"]:
        if x["no"] == row["no"]:
            x["qty"] = 0.0
    q2["total"] = round(sum(x["amount"] for x in q2["rows"]), 2)
    return q2


def inject_vague(q, row, env, template_key):
    q2 = clone_rows(q)
    for x in q2["rows"]:
        if x["no"] == row["no"]:
            x["note"] = ((x["note"] or "") + "，另计").strip("，")
    return q2


CASES = [
    # (类型, 目标选择器, 注入器, 判定器)
    ("drop_concept", targets_drop_concept, inject_drop_concept,
     lambda r, t, base: t in sig_missing(r)),
    ("dup_row", targets_dup_row, inject_dup_row,
     lambda r, t, base: any((str(t["no"]) in lines) or ("%s-r" % t["no"] in lines)
                            for lines in sig_dup_cids(r).values())),
    ("tamper_amount", targets_tamper, inject_tamper,
     lambda r, t, base: str(t["no"]) in sig_risk_lines(r, "R8_arithmetic")),
    ("zero_qty", targets_zero_qty, inject_zero_qty,
     lambda r, t, base: str(t["no"]) in sig_risk_lines(r, "R9_qty_unit")),
    ("add_vague", targets_vague, inject_vague,
     lambda r, t, base: str(t["no"]) in sig_risk_lines(r, "R4_vague_term")),
]


def count_findings(r):
    return (len(r["missing"]), len(r["duplicates"]), len(r["risks"]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-doc", type=int, default=10,
                    help="每类每份单据最多注入多少个案例（默认 10）")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    env = load_env()
    onto = env[0]
    os.makedirs(OUTDIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    records = []

    for slug, label, rel, tk in DOCS:
        q = P.build_line_index(P.load_quote(os.path.join(ROOT, rel), label), onto)
        renumbered = renumber_if_duplicated(q, slug)
        base = run_audit(q, env, tk)
        base_f = count_findings(base)
        base_missing = sig_missing(base)
        base_dups = sig_dup_cids(base)
        if not args.quiet:
            print("=== %s（%d 行明细%s）基线：漏 %d 重 %d 口径 %d ===" % (
                label, len(q["rows"]), "，序号重复已重编号" if renumbered else "",
                base_f[0], base_f[1], base_f[2]))

        for mtype, selector, injector, checker in CASES:
            raw_targets = selector(q, base, env, tk) if mtype == "drop_concept" else selector(q, base)
            targets = raw_targets[:args.per_doc]
            det = loc = 0
            extra = []
            for t in targets:
                q2 = injector(q, t, env, tk)
                r2 = run_audit(q2, env, tk)
                ok = checker(r2, t, base)
                det += bool(ok)
                if ok:
                    loc += 1
                # 误报增量：注入后多出来的、与目标无关的结论条数
                f2 = count_findings(r2)
                extra.append(max(0, sum(f2) - sum(base_f)))
                records.append({"doc": slug, "type": mtype,
                                "target": (t if isinstance(t, str) else t["no"]),
                                "detected": bool(ok),
                                "findings_after": f2, "findings_base": base_f})
            if not args.quiet:
                n = len(targets)
                print("  %-14s 注入 %2d | 检出 %2d%s | 候选池 %d" % (
                    mtype, n, det,
                    "" if not n else "（%.0f%%）" % (det / n * 100), len(raw_targets)))

    total = len(records)
    hit = sum(1 for r in records if r["detected"])
    print("\n总计：注入 %d 个已知缺陷，检出 %d 个（%.1f%%）" % (
        total, hit, hit / total * 100 if total else 0))

    by_type = {}
    for r in records:
        d = by_type.setdefault(r["type"], [0, 0])
        d[0] += 1
        d[1] += bool(r["detected"])
    print("\n| 注入类型 | 案例数 | 检出 | 检出率 |")
    print("|---|---:|---:|---:|")
    for k, (n, h) in by_type.items():
        print("| %s | %d | %d | %.1f%% |" % (k, n, h, h / n * 100 if n else 0))

    payload = {"generated_at": stamp, "per_doc_cap": args.per_doc,
               "total": total, "detected": hit, "by_type": by_type, "records": records}
    jp = os.path.join(OUTDIR, "injection_%s.json" % stamp)
    json.dump(payload, open(jp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    md = ["# 注入实验报告 %s" % stamp, "",
          "> 真值来源：程序注入（非人工标注）。注入 %d 个已知缺陷，检出 %d 个（%.1f%%）。" % (
              total, hit, hit / total * 100 if total else 0), "",
          "| 注入类型 | 案例数 | 检出 | 检出率 |", "|---|---:|---:|---:|"]
    for k, (n, h) in by_type.items():
        md.append("| %s | %d | %d | %.1f%% |" % (k, n, h, h / n * 100 if n else 0))
    md += ["", "## 分单据明细", "", "| 单据 | 类型 | 目标 | 检出 |", "|---|---|---|---|"]
    for r in records:
        md.append("| %s | %s | %s | %s |" % (r["doc"], r["type"], r["target"],
                                             "✓" if r["detected"] else "✗"))
    mp = os.path.join(OUTDIR, "injection_%s.md" % stamp)
    open(mp, "w", encoding="utf-8").write("\n".join(md) + "\n")
    print("\n写出 %s\n     %s" % (jp, mp))


if __name__ == "__main__":
    main()
