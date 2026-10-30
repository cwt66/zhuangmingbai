# -*- coding: utf-8 -*-
"""装明白 · 可计算审计引擎 v0.2
输入：CSV 报价单（结构化行项；真实链路为 VLM 解析后的同一结构）
输出：
  out/audit_report.md      Markdown 报告
  out/audit_report.html    可打印 HTML 报告（浏览器打印为 PDF）
  out/audit_result.json    汇总 JSON
  out/review_data.js       校对页数据（web/review.html 直接引用）

设计原则（对齐 v2 方向）：
- 大模型不在本引擎内：本引擎只做「判断」，解析由上游 VLM 完成
- 每条结论带证据：行号 + 基准 + 公式 + 置信度 + 三档处置
- 低置信度不进核减金额，只进「待确认清单」
- 不依赖外部价格库：漏项金额用「同一批报价的横向参照」估计
- 漏项是「补项成本」（加），重复是「可剔除」（减），两者不能相加
"""
import csv
import difflib
import json
import os
import re
import statistics
from collections import defaultdict

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE, "data")
OUT = os.path.join(BASE, "out")

PUNCT = re.compile(r"[\s\-—_（）()【】\[\]、，,。.:：;；/\\|·\"'“”‘’]+")
PAREN = re.compile(r"[（(][^）)]*[）)]")


def norm(s):
    return PUNCT.sub("", (s or "").strip().lower())


def fmt_money(x):
    return "—" if x is None else f"{x:,.0f}"


class Ontology:
    def __init__(self, path):
        with open(path, encoding="utf-8") as f:
            self.items = json.load(f)
        self.by_id = {it["id"]: it for it in self.items}
        self.alias = {}
        for it in self.items:
            self.alias[norm(it["n"])] = it["id"]
            for a in it["a"]:
                self.alias.setdefault(norm(a), it["id"])

    def align(self, name):
        # 真实单据常把规格写进括号：「铺墙砖（300*450）」——先按原名匹配，再试去括号后的名字
        cands = [norm(name)]
        stripped = norm(PAREN.sub("", name or ""))
        if stripped and stripped != cands[0]:
            cands.append(stripped)
        for n in cands:
            if n and n in self.alias:
                cid = self.alias[n]
                exact = norm(self.by_id[cid]["n"]) == n
                return cid, (0.98 if exact else 0.95), ("exact" if exact else "alias")
        n = max(cands, key=len)
        if not n:
            return None, 0.0, "empty"
        best, best_r = None, 0.0
        for a, cid in self.alias.items():
            r = difflib.SequenceMatcher(None, n, a).ratio()
            if r > best_r:
                best, best_r = cid, r
        if best_r >= 0.62:
            return best, round(best_r * 0.9, 3), "fuzzy"
        # 包含匹配兜底：真实单据常写成「安装人造大理石门槛」「封管道（湿封）」，
        # 精确与编辑距离都打不中。取被包含的最长别名词（≥2 字），置信度低于别名命中。
        contain, clen = None, 0
        for a, cid in self.alias.items():
            if len(a) >= 2 and (a in n or n in a) and len(a) > clen:
                contain, clen = cid, len(a)
        if contain:
            return contain, 0.85, "contains"
        return None, 0.0, "unmatched"


def load_quote(path, label):
    rows = []
    with open(path, encoding="utf-8-sig") as f:
        for i, r in enumerate(csv.DictReader(f), start=1):
            def num(k):
                v = (r.get(k) or "").replace(",", "").strip()
                try:
                    return float(v)
                except ValueError:
                    return 0.0
            rows.append({
                "no": (r.get("序号") or str(i)).strip(),
                "name": (r.get("项目名称") or "").strip(),
                "spec": (r.get("规格工艺") or "").strip(),
                "unit": (r.get("单位") or "").strip(),
                "qty": num("数量"), "price": num("单价"), "amount": num("合价"),
                "note": (r.get("备注") or "").strip(),
            })
    return {"label": label, "file": os.path.basename(path), "rows": rows,
            "total": round(sum(x["amount"] for x in rows), 2)}


def build_line_index(quote, onto):
    for row in quote["rows"]:
        cid, conf, how = onto.align(row["name"])
        row["cid"], row["align_conf"], row["align_how"] = cid, conf, how
    return quote


def confidence(rule_strength, parse_q, align_conf, in_template):
    return round(0.4 * rule_strength + 0.2 * parse_q + 0.2 * align_conf + 0.2 * (1.0 if in_template else 0.5), 3)


def tier(c):
    return "auto" if c >= 0.90 else ("review" if c >= 0.60 else "manual")


def _row_text(row):
    return " ".join([row.get("name") or "", row.get("spec") or "", row.get("note") or ""])


# 「含拆除，不含垃圾外运」——词出现了，但意思相反。证据词命中前必须先看否定语境。
NEG_PREFIX = ("不含", "不含在", "不包括", "除外", "另计", "另收", "自理", "自购", "不包", "另付")


def _evidence_hit(words, texts):
    """返回第一个「非否定语境」下命中的证据词，没有则返回 None。"""
    for w in words:
        for t in texts:
            i = t.find(w)
            while i >= 0:
                pre = t[max(0, i - 6):i]
                if not any(n in pre for n in NEG_PREFIX):
                    return w
                i = t.find(w, i + 1)
    return None


def find_missing(quote, others, template, onto, rule, coverage=None):
    have = {r["cid"] for r in quote["rows"] if r["cid"]}
    have_cats = {onto.by_id[c].get("c") for c in have if c in onto.by_id}
    texts = [_row_text(r) for r in quote["rows"]]
    ev = (coverage or {}).get("evidence") or {}
    absorbed_by = (coverage or {}).get("soft", {}).get("absorbed_by") or {}

    def present(cid):
        """模板项是否已被满足：概念命中，或行项文本里出现其证据词。"""
        return cid in have or bool(_evidence_hit(ev.get(cid, []), texts))

    out = []
    for cid in template:
        if cid in have:
            continue
        # 类目级覆盖：模板项是"水电改造"这类父类目时，只要已有同类目行项即视为未漏
        # （真实报价单按材料/线路逐行列，不会出现"水电改造"这四个字）
        if cid == "mep" and "mep" in have_cats:
            continue
        # 文本证据覆盖：真实报价单是复合行，一行可含多个工序。
        # 「墙面基础处理+批嵌砂光」同时是基层处理与批腻子，仅靠概念 1:1 对齐
        # 会把后者判成漏项。词表见 data/coverage_v0.json，逐条可人工审阅。
        if _evidence_hit(ev.get(cid, []), texts):
            continue
        refs = []
        for o in others:
            vals = [r["amount"] for r in o["rows"] if r.get("cid") == cid and r["amount"] > 0]
            if vals:
                refs.append({"quote": o["label"], "amount": round(statistics.median(vals), 2)})
        ref_amt = round(statistics.median([x["amount"] for x in refs]), 2) if refs else None
        c = confidence(rule["strength"], 0.95, 0.90, True)
        basis = "标准工序模板（老房翻新）；概念对齐与文本证据词两路均未命中"
        formula = "T \\ S（模板必做项 − 实列项），再扣除文本证据命中项"
        absorbers = [a for a in absorbed_by.get(cid, []) if present(a)]
        if absorbers:
            # 复合工序在，而它没单列：可能被打包进去了，降为建议复核
            c = min(c, 0.85)
            basis = "未单列，但复合工序「%s」已在报价中，可能已含" % "／".join(
                onto.by_id[a]["n"] for a in absorbers if a in onto.by_id)
            formula = "T \\ S 未命中，且存在可吸收它的复合工序"
        out.append({"cid": cid, "concept": onto.by_id[cid]["n"], "rule": rule["id"],
                    "ref_amount": ref_amt, "ref_source": refs, "confidence": c, "tier": tier(c),
                    "evidence": {"basis": basis, "formula": formula,
                                 "checked_words": ev.get(cid, [])}})
    return out


ZONE_RE = re.compile(r"分区[:：]\s*([^\s;；,，|]+)")


def _zone(row):
    """取行项所属分区（客餐厅 / 主卧 / 厨房 …）。无分区标注时返回空串。"""
    m = ZONE_RE.search(row.get("spec") or "")
    return m.group(1) if m else ""


def _variant_residual(onto, row):
    """行名里去掉「概念的正式名」之后剩下的部分，作为变体标识。

    只剥正式名、不剥别名：别名本身常带部位词（顶面涂料／墙面涂料都是 topcoat 的别名），
    剥别名会把部位信息抹掉，导致顶面和墙面被判成重复计价。
    「墙面找平」−「墙面找平」= 空，两条同名仍是重复；顶面/墙面各留部位词，不判重复。
    """
    n = norm(row["name"])
    item = onto.by_id.get(row["cid"]) or {}
    base = norm(item.get("n"))
    if base and base in n:
        return n.replace(base, "", 1)
    return n


def find_duplicates(quote, onto, rule_pair, rule_same, template):
    """返回 (重复项, 已排除的同概念不同变体组数)。

    同一概念在同一分区出现多行，先看变体（行名去掉正式名后的剩余部分）：
    变体不同说明是不同部位/不同规格的两件事（12 个不同柜子、4 种电线、
    墙面防水与地面防水），不是重复计价，直接排除而不是降级——
    否则报告里堆满假阳性，用户会把真结论一起忽略。
    """
    out = []
    excluded = 0
    by_cid = defaultdict(list)
    for r in quote["rows"]:
        if r["cid"]:
            # 按「概念 + 分区」分组：不同房间各做一次吊顶不是重复计价
            by_cid[(r["cid"], _zone(r))].append(r)
    for (cid, zone), lines in by_cid.items():
        if len(lines) >= 2:
            variants = {_variant_residual(onto, x) for x in lines}
            if len(variants) > 1:
                excluded += 1
                continue
            ls = sorted(lines, key=lambda x: -x["amount"])
            dup = round(sum(x["amount"] for x in ls[1:]), 2)
            c = confidence(rule_same["strength"], 0.95, min(x["align_conf"] for x in lines), cid in template)
            basis = ("本体归一后同一概念在「%s」内多行计费" % zone) if zone else "本体归一后同一概念多行计费"
            out.append({"cid": cid, "concept": onto.by_id[cid]["n"], "rule": rule_same["id"],
                        "lines": [x["no"] for x in lines], "amount": dup, "confidence": c, "tier": tier(c),
                        "evidence": {"basis": basis,
                                     "formula": "count(concept, zone) >= 2 且行名去掉正式名后完全一致",
                                     "line_nos": [x["no"] for x in lines]}})
    present = {r["cid"] for r in quote["rows"] if r["cid"]}
    for p in rule_pair["pairs"]:
        c_, d_ = p["container"], p["contained"]
        if c_ in present and d_ in present and d_ in template and c_ not in template:
            amt = round(sum(r["amount"] for r in quote["rows"] if r["cid"] == d_), 2)
            c = confidence(rule_pair["strength"], 0.95, 0.9, True)
            out.append({"cid": d_, "concept": onto.by_id[d_]["n"], "rule": rule_pair["id"],
                        "lines": [r["no"] for r in quote["rows"] if r["cid"] == d_], "amount": amt,
                        "confidence": c, "tier": tier(c),
                        "evidence": {"basis": f"工艺惯例：{onto.by_id[c_]['n']} 通常已含 {onto.by_id[d_]['n']}",
                                     "formula": "contained ∈ container 的工艺范围"}})
    return out, excluded


def find_vague(quote, rule):
    out = []
    for r in quote["rows"]:
        text = " ".join([r["name"], r["spec"], r["note"], r["unit"]])
        for p in rule["patterns"]:
            if p in text:
                c = confidence(rule["strength"], 0.95, r["align_conf"] or 0.85, False)
                out.append({"cid": r["cid"], "concept": r["name"], "line": r["no"], "issue": f"命中「{p}」",
                            "amount": r["amount"], "rule": rule["id"], "confidence": c, "tier": tier(c),
                            "evidence": {"basis": "报价单风险表述清单 v0", "formula": f"命中「{p}」"}})
                break
    return out


def find_unit_anomaly(quote, onto, rule):
    out = []
    for r in quote["rows"]:
        if not r["cid"]:
            continue
        default = onto.by_id[r["cid"]]["u"]
        if default in ("㎡", "m") and r["unit"] == "项":
            c = confidence(rule["strength"], 0.95, r["align_conf"], True)
            out.append({"cid": r["cid"], "concept": onto.by_id[r["cid"]]["n"], "line": r["no"],
                        "issue": "面积/长度类工序按「项」报价，工程量不可核",
                        "rule": rule["id"], "confidence": c, "tier": tier(c),
                        "evidence": {"basis": f"本体默认单位 {default}", "formula": "unit=项 且 default∈{㎡,m}"}})
    return out


def find_arithmetic(quote, rule):
    out = []
    for r in quote["rows"]:
        if r["qty"] > 0 and r["price"] > 0:
            diff = round(r["amount"] - r["qty"] * r["price"], 2)
            if abs(diff) > 1:
                c = confidence(rule["strength"], 0.95, r["align_conf"] or 0.9, False)
                out.append({"cid": r["cid"], "concept": r["name"], "line": r["no"],
                            "issue": f"合价与数量×单价不一致（差 {diff:+.0f} 元）",
                            "rule": rule["id"], "confidence": c, "tier": tier(c),
                            "evidence": {"basis": "报价单算术一致性",
                                         "formula": f"{r['qty']}×{r['price']}={r['qty']*r['price']:.0f}，单列 {r['amount']:.0f}"}})
    return out


def find_qty_unit(quote, rule):
    out = []
    for r in quote["rows"]:
        if r["qty"] <= 0 or not r["unit"]:
            c = confidence(rule["strength"], 0.95, r["align_conf"] or 0.8, False)
            out.append({"cid": r["cid"], "concept": r["name"], "line": r["no"],
                        "issue": "数量或单位缺失，工程量不可核", "rule": rule["id"],
                        "confidence": c, "tier": tier(c),
                        "evidence": {"basis": "报价单完整性", "formula": "qty<=0 或 unit 为空"}})
    return out


def find_fee_rate(quote, rule):
    out = []
    total = quote["total"] or 1
    for r in quote["rows"]:
        if r["cid"] == "mgmt_fee" and r["amount"] / total > rule["threshold"]:
            rate = r["amount"] / total * 100
            c = confidence(rule["strength"], 0.95, r["align_conf"], True)
            out.append({"cid": r["cid"], "concept": r["name"], "line": r["no"],
                        "issue": f"管理费率 {rate:.1f}% 偏高（参考上限 8%）", "rule": rule["id"],
                        "confidence": c, "tier": tier(c),
                        "evidence": {"basis": "行业惯例：管理费通常 3%–8%", "formula": "管理费/总价 > 8%"}})
    return out


def find_spec_missing(quote, rule):
    out = []
    for r in quote["rows"]:
        if r["amount"] >= rule["min_amount"] and not r["spec"]:
            c = confidence(rule["strength"], 0.95, r["align_conf"] or 0.85, False)
            out.append({"cid": r["cid"], "concept": r["name"], "line": r["no"],
                        "issue": "金额较大但未写明品牌/型号/规格", "rule": rule["id"],
                        "confidence": c, "tier": tier(c),
                        "evidence": {"basis": "合同附件要求：品牌+型号+规格+工艺", "formula": "spec 为空且合价 ≥ 2000"}})
    return out


def find_wp_height(quote, rule):
    out = []
    have = {x["cid"] for x in quote["rows"] if x["cid"]}
    if "wp_height" in have:          # 已单列墙面防水加高，不判漏
        return out
    for r in quote["rows"]:
        if r["cid"] != "waterproof":
            continue
        spec = r["spec"]
        if "地面" in spec and not any(k in spec for k in ("1.8", "1800", "墙面")):
            c = confidence(rule["strength"], 0.95, r["align_conf"], True)
            out.append({"cid": r["cid"], "concept": r["name"], "line": r["no"],
                        "issue": "防水仅写地面，未注明淋浴区墙面 1.8m", "rule": rule["id"],
                        "confidence": c, "tier": tier(c),
                        "evidence": {"basis": "国标：卫生间墙面防水≥1.8m，厨房≥0.3m",
                                     "formula": "spec 含「地面」且不含 1.8/1800/墙面"}})
    return out


def find_split(quote, rule, onto, template):
    out = []
    by_cid = defaultdict(list)
    for r in quote["rows"]:
        if r["cid"]:
            by_cid[r["cid"]].append(r)
    for cid, lines in by_cid.items():
        if len(lines) < 2:
            continue
        kw = any(any(k in (x["name"] + x["spec"]) for k in rule["keywords"]) for x in lines)
        if kw:
            c = confidence(rule["strength"], 0.95, min(x["align_conf"] for x in lines), cid in template)
            out.append({"cid": cid, "concept": onto.by_id[cid]["n"], "lines": [x["no"] for x in lines],
                        "issue": "疑似拆分报价（人工/材料分开计费）", "rule": rule["id"],
                        "confidence": c, "tier": tier(c),
                        "evidence": {"basis": "拆分报价模式", "formula": "同概念多行且命中人工/材料关键词"}})
    return out


def cross_unit_mix(quotes, onto, rule):
    units = defaultdict(lambda: defaultdict(set))
    for q in quotes:
        for r in q["rows"]:
            if r["cid"]:
                units[r["cid"]][q["label"]].add(r["unit"])
    out = []
    for cid, per_q in units.items():
        allu = set()
        for s in per_q.values():
            allu |= s
        if len(allu) > 1:
            out.append({"cid": cid, "concept": onto.by_id[cid]["n"], "units": sorted(allu),
                        "rule": rule["id"], "confidence": 0.70, "tier": "review",
                        "evidence": {"basis": "本体单位定义", "formula": "unit(q1) != unit(q2)"}})
    return out


def exposure(quote, others, rule):
    out = []
    for r in quote["rows"]:
        if r["cid"] == "mep" and any(p in (r["spec"] + r["note"]) for p in ("按实结算", "多退少补")):
            others_amt = [x["amount"] for o in others for x in o["rows"]
                          if x["cid"] == "mep" and "包干" in (x["spec"] + x["note"])]
            if len(others_amt) >= 2:
                out.append({"concept": r["name"], "current": r["amount"],
                            "low": round(min(others_amt) - r["amount"], 2),
                            "high": round(max(others_amt) - r["amount"], 2),
                            "basis": f"同批包干价 {min(others_amt):.0f}–{max(others_amt):.0f} 元"})
    return out


def audit(quotes, onto, templates, rules, coverage=None, template_key="old_house"):
    """template_key: old_house（老房翻新，38 项）或 new_house（新房毛坯，25 项）。
    模板选错，漏项结论全错——所以做成显式参数，并在报告里写明用的是哪套。"""
    R = {r["id"]: r for r in rules["rules"]}
    template = templates[template_key]
    results = {}
    for q in quotes:
        others = [x for x in quotes if x is not q]
        missing = find_missing(q, others, template, onto, R["R1_missing_item"], coverage)
        dups, dup_excluded = find_duplicates(q, onto, R["R2_duplicate_known"], R["R3_duplicate_same_concept"], template)
        risks = []
        risks += find_vague(q, R["R4_vague_term"])
        risks += find_unit_anomaly(q, onto, R["R6_unit_anomaly"])
        risks += find_arithmetic(q, R["R8_arithmetic"])
        risks += find_qty_unit(q, R["R9_qty_unit"])
        risks += find_fee_rate(q, R["R10_fee_rate"])
        risks += find_spec_missing(q, R["R11_spec_missing"])
        risks += find_wp_height(q, R["R12_wp_height"])
        risks += find_split(q, R["R7_split_billing"], onto, template)
        miss_cost = sum(m["ref_amount"] for m in missing if m["tier"] == "auto" and m["ref_amount"]) or 0.0
        dup_cut = sum(d["amount"] for d in dups if d["tier"] == "auto") or 0.0
        results[q["label"]] = {
            "quote": q, "missing": missing, "duplicates": dups, "risks": risks,
            "template_key": template_key, "template_size": len(template),
            "exposure": exposure(q, others, R["R4_vague_term"]),
            "missing_cost": round(miss_cost, 2), "duplicate_cut": round(dup_cut, 2),
            "dup_excluded": dup_excluded,
            "effective": round(q["total"] + miss_cost - dup_cut, 2),
        }
    results["_cross_unit"] = cross_unit_mix(quotes, onto, R["R5_unit_mix"])
    return results


def build_markdown(quotes, res):
    L = ["# 装明白 · 报价审计报告（引擎 v0.2）\n",
         "> 输入：三家报价单（80㎡ 老房翻新）。只做计费核对，**不判断贵不贵**；低置信度结论只进待确认清单。\n",
         "\n## 一、总览\n",
         "| 公司 | 报价总价 | 漏项补项成本 | 重复计价剔除 | 真实成本（补漏−剔重） | 风险敞口 | 漏项 | 重复 | 口径/算术风险 |",
         "|---|---:|---:|---:|---:|---|---:|---:|---:|"]
    for q in quotes:
        r = res[q["label"]]
        exp = r["exposure"]
        exp_s = f"{fmt_money(exp[0]['low'])}–{fmt_money(exp[0]['high'])}" if exp else "—"
        L.append(f"| {q['label']} | {fmt_money(q['total'])} | {fmt_money(r['missing_cost'])} | "
                 f"{fmt_money(r['duplicate_cut'])} | {fmt_money(r['effective'])} | {exp_s} | {len(r['missing'])} | "
                 f"{len(r['duplicates'])} | {len(r['risks'])} |")
    L.append("\n**口径**：漏项成本=按同批报价横向参照补齐要花的钱（不是省钱）；重复计价剔除=可从总价砍掉的重复项；"
             "真实成本=总价+漏项−重复；风险敞口单列，不计入。\n")
    cn = ["二", "三", "四", "五", "六"]
    for i, q in enumerate(quotes):
        r = res[q["label"]]
        L.append(f"\n## {cn[i]}、{q['label']} 明细（{q['file']}）\n")
        L.append(f"总价 **{fmt_money(q['total'])}**；漏项补项 **{fmt_money(r['missing_cost'])}**；"
                 f"重复剔除 **{fmt_money(r['duplicate_cut'])}**；真实成本 **{fmt_money(r['effective'])}**。\n")
        L.append("\n### 漏项（对照老房翻新模板）\n")
        if r["missing"]:
            L.append("| 工序 | 参照金额 | 参照来源 | 置信度 | 档位 | 公式 |")
            L.append("|---|---:|---|---:|---|---|")
            for m in r["missing"]:
                src = "、".join(x["quote"] for x in m["ref_source"]) or "无参照"
                L.append(f"| {m['concept']} | {fmt_money(m['ref_amount'])} | {src} | {m['confidence']:.2f} | {m['tier']} | {m['evidence']['formula']} |")
        else:
            L.append("无。\n")
        L.append("\n### 重复计价\n")
        if r["duplicates"]:
            L.append("| 工序 | 行号 | 金额 | 依据 | 置信度 | 档位 |")
            L.append("|---|---|---:|---|---:|---|")
            for d in r["duplicates"]:
                L.append(f"| {d['concept']} | {','.join(d['lines'])} | {fmt_money(d['amount'])} | {d['evidence']['basis']} | {d['confidence']:.2f} | {d['tier']} |")
        else:
            L.append("无。\n")
        L.append("\n### 口径 / 算术 / 拆分风险\n")
        if r["risks"]:
            L.append("| 行号 | 项目 | 问题 | 依据 | 档位 |")
            L.append("|---|---|---|---|---|")
            for x in r["risks"]:
                ln = x.get("line") or ",".join(x.get("lines", []))
                L.append(f"| {ln} | {x['concept']} | {x['issue']} | {x['evidence']['basis']} | {x['tier']} |")
        else:
            L.append("无。\n")
        if r["exposure"]:
            e = r["exposure"][0]
            L.append(f"\n### 风险敞口（不计入核减）\n\n- {e['concept']}：现报 {fmt_money(e['current'])}，{e['basis']}，预计上浮 **{fmt_money(e['low'])}–{fmt_money(e['high'])}**。\n")
    L.append(f"\n## {cn[len(quotes)]}、跨单口径对比\n")
    if res["_cross_unit"]:
        L.append("| 工序 | 各单位口径 | 依据 |")
        L.append("|---|---|---|")
        for x in res["_cross_unit"]:
            L.append(f"| {x['concept']} | {' / '.join(x['units'])} | {x['evidence']['basis']} |")
    else:
        L.append("无跨单单位差异。\n")
    L.append(f"\n## {cn[len(quotes)+1]}、结论口径（对外话术）\n")
    L.append("- 本报告**不判断哪家便宜**，只回答：缺了什么、重了什么、哪些口径不可核、按同批参照补漏后真实成本是多少。\n")
    L.append("- 最低报价不等于最低成本：补齐漏项后排序会变化；开口项另计敞口。\n")
    L.append("- 低置信度结论不进入核减金额，只进入待确认清单——**宁可少报，不可错报**。\n")
    return "\n".join(L)


HTML_TPL = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>装明白 · 报价审计报告</title>
<style>
body{font-family:-apple-system,"PingFang SC","Helvetica Neue",sans-serif;color:#1f2329;background:#fff;margin:0;padding:40px 56px;line-height:1.7}
h1{font-size:26px;margin:0 0 6px;font-weight:600}
.sub{color:#646a73;font-size:13px;margin-bottom:28px}
.kpis{display:flex;gap:16px;margin:24px 0}
.kpi{flex:1;border:1px solid #e5e6eb;border-radius:10px;padding:16px 18px}
.kpi .l{font-size:12px;color:#646a73}
.kpi .v{font-size:24px;font-weight:600;margin-top:6px}
.kpi .n{font-size:12px;color:#8f959e;margin-top:4px}
table{border-collapse:collapse;width:100%;margin:12px 0 24px;font-size:13px}
th,td{border:1px solid #e5e6eb;padding:8px 10px;text-align:left;vertical-align:top}
th{background:#f7f8fa;font-weight:500}
td.num,th.num{text-align:right}
h2{font-size:18px;margin:34px 0 10px;font-weight:600;border-left:3px solid #185fa5;padding-left:10px}
.note{background:#f7f8fa;border-radius:8px;padding:12px 16px;font-size:13px;color:#4e5969;margin:16px 0}
.tag{display:inline-block;padding:1px 6px;border-radius:4px;font-size:11px;background:#e8f3ff;color:#185fa5}
.bar{height:10px;background:#e8f3ff;border-radius:5px;position:relative;margin-top:4px}
.bar i{display:block;height:10px;background:#185fa5;border-radius:5px}
.barcell{min-width:160px}
footer{margin-top:40px;color:#8f959e;font-size:12px;border-top:1px solid #e5e6eb;padding-top:16px}
@media print{body{padding:20mm}.kpis{break-inside:avoid}tr{break-inside:avoid}}
</style></head><body>
<h1>装明白 · 报价审计报告</h1>
<div class="sub">可计算审计（Computable Audit）｜80㎡ 老房翻新场景｜只做计费核对，不判断贵不贵</div>
<div class="kpis">{{KPIS}}</div>
<h2>一、总览</h2>
{{SUMMARY}}
<div class="note">{{NOTE}}</div>
{{SECTIONS}}
<h2>结论口径</h2>
<ul>
<li>本报告不判断哪家便宜，只回答：缺了什么、重了什么、哪些口径不可核、补漏后真实成本是多少。</li>
<li>最低报价不等于最低成本：补齐漏项后排序会变化；开口项另计敞口。</li>
<li>低置信度结论不进入核减金额，只进入待确认清单——宁可少报，不可错报。</li>
</ul>
<footer>装明白 v0.2 引擎自动生成｜证据链：原表行号 + 对照基准 + 计算公式 + 置信度｜置信度校准与 ECE 报告见评测文档</footer>
</body></html>"""


def build_html(quotes, res):
    kpis = []
    for q in quotes:
        r = res[q["label"]]
        kpis.append('<div class="kpi"><div class="l">%s · 真实成本</div><div class="v">%s</div>'
                    '<div class="n">报价 %s ｜ 漏项 %d 项 ｜ 重复 %d 项</div></div>' %
                    (q["label"], fmt_money(r["effective"]), fmt_money(q["total"]),
                     len(r["missing"]), len(r["duplicates"])))
    rows = []
    maxv = max(res[q["label"]]["effective"] for q in quotes)
    for q in quotes:
        r = res[q["label"]]
        exp = r["exposure"]
        exp_s = f"{fmt_money(exp[0]['low'])}–{fmt_money(exp[0]['high'])}" if exp else "—"
        pct = int(r["effective"] / maxv * 100)
        rows.append("<tr><td>%s</td><td class='num'>%s</td><td class='num'>%s</td><td class='num'>%s</td>"
                    "<td class='num barcell'>%s<div class='bar'><i style='width:%d%%'></i></div></td>"
                    "<td class='num'>%s</td><td class='num'>%d</td><td class='num'>%d</td><td class='num'>%d</td></tr>" %
                    (q["label"], fmt_money(q["total"]), fmt_money(r["missing_cost"]),
                     fmt_money(r["duplicate_cut"]), fmt_money(r["effective"]), pct,
                     exp_s, len(r["missing"]), len(r["duplicates"]), len(r["risks"])))
    summary = ("<table><tr><th>公司</th><th class='num'>报价总价</th><th class='num'>漏项补项</th>"
               "<th class='num'>重复剔除</th><th class='num'>真实成本</th><th class='num'>风险敞口</th>"
               "<th class='num'>漏项</th><th class='num'>重复</th><th class='num'>口径风险</th></tr>"
               + "".join(rows) + "</table>")
    note = ("漏项成本 = 按同批报价横向参照补齐要花的钱（不是省钱）；重复计价剔除 = 可从总价砍掉的重复项；"
            "真实成本 = 总价 + 漏项 − 重复；风险敞口单列，不计入。")
    secs = []
    for q in quotes:
        r = res[q["label"]]
        h = ["<h2>%s 明细</h2>" % q["label"],
             "<p>总价 <b>%s</b>；漏项补项 <b>%s</b>；重复剔除 <b>%s</b>；真实成本 <b>%s</b>。</p>" %
             (fmt_money(q["total"]), fmt_money(r["missing_cost"]), fmt_money(r["duplicate_cut"]), fmt_money(r["effective"]))]
        if r["missing"]:
            h.append("<table><tr><th>漏项工序</th><th class='num'>参照金额</th><th>参照来源</th><th class='num'>置信度</th><th>档位</th></tr>")
            for m in r["missing"]:
                src = "、".join(x["quote"] for x in m["ref_source"]) or "无参照"
                h.append("<tr><td>%s</td><td class='num'>%s</td><td>%s</td><td class='num'>%.2f</td><td><span class='tag'>%s</span></td></tr>" %
                         (m["concept"], fmt_money(m["ref_amount"]), src, m["confidence"], m["tier"]))
            h.append("</table>")
        if r["duplicates"]:
            h.append("<table><tr><th>重复工序</th><th>行号</th><th class='num'>金额</th><th>依据</th><th class='num'>置信度</th></tr>")
            for d in r["duplicates"]:
                h.append("<tr><td>%s</td><td>%s</td><td class='num'>%s</td><td>%s</td><td class='num'>%.2f</td></tr>" %
                         (d["concept"], ",".join(d["lines"]), fmt_money(d["amount"]), d["evidence"]["basis"], d["confidence"]))
            h.append("</table>")
        if r["risks"]:
            h.append("<table><tr><th>行号</th><th>项目</th><th>问题</th><th>依据</th><th>档位</th></tr>")
            for x in r["risks"][:12]:
                ln = x.get("line") or ",".join(x.get("lines", []))
                h.append("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td><span class='tag'>%s</span></td></tr>" %
                         (ln, x["concept"], x["issue"], x["evidence"]["basis"], x["tier"]))
            h.append("</table>")
        secs.append("".join(h))
    return (HTML_TPL.replace("{{KPIS}}", "".join(kpis))
            .replace("{{SUMMARY}}", summary).replace("{{NOTE}}", note)
            .replace("{{SECTIONS}}", "".join(secs)))


def build_review_data(quotes, onto):
    data = {"meta": {"generated": "2026-10-23", "note": "核对页演示数据；真实链路由 VLM 返回字段置信度"},
            "quotes": []}
    for q in quotes:
        rows = []
        for i, r in enumerate(q["rows"]):
            low = q["label"].startswith("B") and i in (9, 16)  # 演示：两行低置信度
            conf = min(r["align_conf"] or 0.9, 0.99)
            if low:
                conf = 0.72
            rows.append({"no": r["no"], "name": r["name"], "spec": r["spec"], "unit": r["unit"],
                         "qty": r["qty"], "price": r["price"], "amount": r["amount"],
                         "cid": r["cid"], "conf": round(conf, 2), "demo_low": low})
        data["quotes"].append({"label": q["label"], "total": q["total"], "rows": rows})
    return data


def main():
    onto = Ontology(os.path.join(DATA, "ontology_v0.json"))
    templates = json.load(open(os.path.join(DATA, "templates_v0.json"), encoding="utf-8"))
    rules = json.load(open(os.path.join(DATA, "rules_v0.json"), encoding="utf-8"))
    cover_path = os.path.join(DATA, "coverage_v0.json")
    coverage = json.load(open(cover_path, encoding="utf-8")) if os.path.exists(cover_path) else None
    files = [("A 公司（规范型）", "company_a.csv"), ("B 公司（低价引流型）", "company_b.csv"),
             ("C 公司（高价项全型）", "company_c.csv")]
    quotes = [build_line_index(load_quote(os.path.join(DATA, "samples", fn), label), onto) for label, fn in files]
    res = audit(quotes, onto, templates, rules, coverage)
    os.makedirs(OUT, exist_ok=True)
    md = build_markdown(quotes, res)
    open(os.path.join(OUT, "audit_report.md"), "w", encoding="utf-8").write(md)
    open(os.path.join(OUT, "audit_report.html"), "w", encoding="utf-8").write(build_html(quotes, res))
    summary = {q["label"]: {"total": q["total"], "missing_cost": res[q["label"]]["missing_cost"],
                            "duplicate_cut": res[q["label"]]["duplicate_cut"],
                            "effective": res[q["label"]]["effective"],
                            "missing": len(res[q["label"]]["missing"]),
                            "duplicates": len(res[q["label"]]["duplicates"]),
                            "risks": len(res[q["label"]]["risks"]),
                            "exposure": res[q["label"]]["exposure"]} for q in quotes}
    json.dump(summary, open(os.path.join(OUT, "audit_result.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    rv = build_review_data(quotes, onto)
    open(os.path.join(OUT, "review_data.js"), "w", encoding="utf-8").write(
        "window.REVIEW_DATA = " + json.dumps(rv, ensure_ascii=False) + ";")
    print(md)
    print("\n[OK] out/audit_report.md | out/audit_report.html | out/audit_result.json | out/review_data.js")


if __name__ == "__main__":
    main()
