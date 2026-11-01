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
import hashlib
import json
import os
import re
import statistics
from collections import defaultdict
from datetime import datetime

try:                      # 作为包引用（main.py: from src import pipeline）
    from . import obligations as oblig
    from . import pricing_system as pricing
except ImportError:       # 作为脚本/顶层模块引用（scripts 里 sys.path 指向 src）
    import obligations as oblig
    import pricing_system as pricing

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
        # 「按实结算/暂估」是口径标记，不是工序：精确命中仍保留（它确实是原表里的词），
        # 但在候选竞争时不优先——否则「水路（预收）据实结算」会被吸成「按实结算」，
        # 导致给水管缺失 + 误报口径风险。
        for it in self.items:                       # 正式名优先占位
            self.alias[norm(it["n"])] = it["id"]
        for it in self.items:                       # 别名不覆盖正式名
            for a in it["a"]:
                self.alias.setdefault(norm(a), it["id"])
        self.fee_ids = {it["id"] for it in self.items if it.get("c") == "fee"}

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
        best, best_r, best_fee = None, 0.0, False
        for a, cid in self.alias.items():
            r = difflib.SequenceMatcher(None, n, a).ratio()
            is_fee = cid in self.fee_ids
            # 同分时工序优先于口径；分更高则照常覆盖
            if r > best_r + 1e-9 or (abs(r - best_r) <= 1e-9 and best_fee and not is_fee):
                best, best_r, best_fee = cid, r, is_fee
        if best_r >= 0.62:
            return best, round(best_r * 0.9, 3), "fuzzy"
        # 包含匹配兜底：真实单据常写成「安装人造大理石门槛」「封管道（湿封）」，
        # 精确与编辑距离都打不中。取被包含的最长别名词（≥2 字），置信度低于别名命中。
        contain, clen, c_fee = None, 0, True
        for a, cid in self.alias.items():
            if len(a) < 2 or not (a in n or n in a):
                continue
            is_fee = cid in self.fee_ids
            if len(a) > clen or (len(a) == clen and c_fee and not is_fee):
                contain, clen, c_fee = cid, len(a), is_fee
        if contain:
            return contain, 0.85, "contains"
        return None, 0.0, "unmatched"


# 汇总/派生费用行的名称特征。命中了还不够——必须过「算术门控」（见 _is_summary_row）。
SUMMARY_KEYWORDS = ("合计", "总计", "总价", "小计", "造价", "管理费", "设计费",
                    "税金", "税费", "服务费", "措施费", "优惠", "实收", "折扣")
# 派生费用（按费率算出来的钱，不是工序）：管理费/设计费/税金这类
FEE_KEYWORDS = ("管理费", "设计费", "税金", "税费", "服务费", "措施费")
# 印刷总额（对账锚点）：这些字样代表「整单合计」而不是分项小计
GRAND_KEYWORDS = ("合计", "总计", "总价", "造价")
PAID_KEYWORDS = ("实收", "优惠", "折扣")


def _is_summary_row(row):
    """判断一行是「汇总/派生费用」而不是「工序」。

    真实反例（必须保护的）：深圳新源诊所 PDF 里有 19 行「批腻子打磨刷乳胶漆小计」
    ——名称带「小计」，其实是 PDF 把分区小计标签拼进了工序名，数量×单价=合价 精确成立。
    若只按关键词剔除，会删掉 19 行真实工序。

    所以门控是「算不出来才算汇总」：
      ① 缺单价或数量 → 是汇总（装修造价 1 项 / 空单价 / 69,864）
      ② 数量×单价 ≠ 合价 → 是汇总（金额不是这一行自己算出来的）
      ③ 费率行：名称含费类词 + 数量<1 + 单位「项」（工程管理费 0.05 项 × 69,864）
    """
    name = row["name"]
    if not any(k in name for k in SUMMARY_KEYWORDS):
        return False
    if row["price"] <= 0 or row["qty"] <= 0:
        return True
    if abs(row["qty"] * row["price"] - row["amount"]) > 0.01:
        return True
    if any(k in name for k in FEE_KEYWORDS) and row["qty"] < 1 and row["unit"] == "项":
        return True
    return False


def reconcile(detail_rows, summary_rows):
    """文档级对账：工序明细之和 + 派生费用 是否等于印刷合计。

    这不是「多此一举」——真实单据里对不上是常态（本项目 5 份单据里 2 份对不上账：
    124㎡ 三处小计误差合计 506.6 元；翡翠台主材区疑有未拍到行）。
    对账差额本身就是一条审计结论：**报价单自己的数都对不上，签之前必须问清楚。**
    """
    detail_sum = round(sum(r["amount"] for r in detail_rows), 2)
    fees = [r for r in summary_rows
            if any(k in r["name"] for k in FEE_KEYWORDS)
            and not any(k in r["name"] for k in GRAND_KEYWORDS)]
    fee_sum = round(sum(r["amount"] for r in fees), 2)
    grand_rows = [r for r in summary_rows
                  if any(k in r["name"] for k in GRAND_KEYWORDS)
                  and not any(k in r["name"] for k in PAID_KEYWORDS)]
    grand = max(grand_rows, key=lambda r: r["amount"]) if grand_rows else None
    paid_rows = [r for r in summary_rows if any(k in r["name"] for k in PAID_KEYWORDS)]
    paid = max(paid_rows, key=lambda r: r["amount"]) if paid_rows else None
    subtotals = [r for r in summary_rows if "小计" in r["name"]]
    out = {
        "detail_sum": detail_sum,
        "fee_sum": fee_sum,
        "fees": [{"name": r["name"], "no": r["no"], "amount": r["amount"]} for r in fees],
        "grand": {"name": grand["name"], "no": grand["no"], "amount": grand["amount"]} if grand else None,
        "paid": {"name": paid["name"], "no": paid["no"], "amount": paid["amount"]} if paid else None,
        "subtotals": [{"name": r["name"], "no": r["no"], "amount": r["amount"]} for r in subtotals],
        "diff": None,
        "ok": None,
    }
    if grand:
        diff = round(grand["amount"] - detail_sum - fee_sum, 2)
        tol = max(1.0, abs(grand["amount"]) * 0.005)
        out["diff"] = diff
        out["ok"] = abs(diff) <= tol
    return out


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
    summary = [x for x in rows if _is_summary_row(x)]
    detail = [x for x in rows if not _is_summary_row(x)]
    return {"label": label, "file": os.path.basename(path), "rows": detail,
            "summary_rows": summary,
            "total": round(sum(x["amount"] for x in detail), 2),
            "recon": reconcile(detail, summary)}


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


_REF_BOOK = {"loaded": False, "items": {}, "meta": {}}


def reference_book():
    """同工序参照价库（本项目 4 份家装单据的金额中位数）。

    用途：单份单据上传时，没有「同批其他报价」可参照，漏项只能说出缺什么、说不出多少钱。
    退回步骤：同批参照（更可比）→ 参照价库（量级估算）→ 无参照（如实留空，不编数）。
    边界写在 data/reference_prices.json 的 meta 里：非市场行情价，只做量级参照。
    """
    if not _REF_BOOK["loaded"]:
        p = os.path.join(DATA, "reference_prices.json")
        if os.path.isfile(p):
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            _REF_BOOK["items"] = d.get("items") or {}
            _REF_BOOK["meta"] = d.get("meta") or {}
        _REF_BOOK["loaded"] = True
    return _REF_BOOK


def find_missing(quote, others, template, onto, rule, coverage=None):
    have = {r["cid"] for r in quote["rows"] if r["cid"]}
    have_cats = {onto.by_id[c].get("c") for c in have if c in onto.by_id}
    texts = [_row_text(r) for r in quote["rows"]]
    # 汇总行文本单独放：费类概念（管理费/设计费/税金…）本来就按费率在表尾算出来，
    # 不会作为工序行出现在明细里。把汇总行当工序证据会「顶替」真工序（错的），
    # 但完全不让它作证会把「有管理费」误判成漏项（也是错的）——所以只对 fee 类开放。
    summary_texts = [_row_text(r) for r in (quote.get("summary_rows") or [])]
    ev = (coverage or {}).get("evidence") or {}
    absorbed_by = (coverage or {}).get("soft", {}).get("absorbed_by") or {}
    # 父类目吸收：只写「水电改造」这一行时，电/水的子项已含在里面，不应再判漏。
    # 与「别名把同一工序拆开」是相反方向的问题，必须显式声明（见 coverage_v0.json absorb）。
    absorb = (coverage or {}).get("absorb") or {}

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
        if any(p_ in have for p_ in absorb.get(cid, [])):
            continue
        # 文本证据覆盖：真实报价单是复合行，一行可含多个工序。
        # 「墙面基础处理+批嵌砂光」同时是基层处理与批腻子，仅靠概念 1:1 对齐
        # 会把后者判成漏项。词表见 data/coverage_v0.json，逐条可人工审阅。
        if _evidence_hit(ev.get(cid, []), texts):
            continue
        # 费类概念：只认汇总行（管理费 3,493 + 4,761 在表尾，不看这里就会误报漏项）
        if onto.by_id.get(cid, {}).get("c") == "fee":
            keys = [onto.by_id[cid]["n"]] + list(onto.by_id[cid].get("a") or [])
            if _evidence_hit(keys, summary_texts):
                continue
        refs = []
        for o in others:
            vals = [r["amount"] for r in o["rows"] if r.get("cid") == cid and r["amount"] > 0]
            if vals:
                refs.append({"quote": o["label"], "amount": round(statistics.median(vals), 2)})
        # 参照金额三级退回：同批其他报价（最可比）→ 参照价库（量级）→ 无参照（留空）。
        ref_basis = "batch" if refs else None
        if not refs:
            bk = reference_book()["items"].get(cid) or {}
            if bk.get("amount_median"):
                refs = [{"quote": "参照价库（%d 份家装中位）" % (bk.get("n_docs") or 0),
                         "amount": bk["amount_median"]}]
                ref_basis = "corpus"
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
                    "ref_amount": ref_amt, "ref_source": refs, "ref_basis": ref_basis,
                    "confidence": c, "tier": tier(c),
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
            # 同分区约束：工艺包含惯例只在同一空间内成立。
            # 反例（真实数据）：翡翠台「回填宝回填」在卫生间，却被拿来扣掉
            # 阳台/厨房/两个卫生间的全部「地面找平」700 元——该惯例对阳台厨房不适用。
            # scope_hint：进一步限定 container 行必须含某类词（如「卫生间」），
            # 否则「空调机位回填」会把厨房也算成卫生间回填的同区。
            hint = p.get("scope_hint") or ""
            c_rows = [r for r in quote["rows"] if r["cid"] == c_]
            if hint:
                c_rows = [r for r in c_rows if hint in (r["name"] + r["spec"] + r["note"])]
            c_zones = {_zone(r) for r in c_rows if _zone(r)}
            d_rows = [r for r in quote["rows"] if r["cid"] == d_]
            if c_zones:
                hit = [r for r in d_rows if (not _zone(r)) or _zone(r) in c_zones]
                scope = "同分区（%s）" % "、".join(sorted(c_zones))
            else:
                hit = d_rows
                scope = "未标分区（保守全保留）"
            if not hit:
                continue
            amt = round(sum(r["amount"] for r in hit), 2)
            c = confidence(rule_pair["strength"], 0.95, 0.9, True)
            out.append({"cid": d_, "concept": onto.by_id[d_]["n"], "rule": rule_pair["id"],
                        "lines": [r["no"] for r in hit], "amount": amt,
                        "confidence": c, "tier": tier(c),
                        "evidence": {"basis": f"工艺惯例：{onto.by_id[c_]['n']} 通常已含 {onto.by_id[d_]['n']}",
                                     "formula": "contained ∈ container 的工艺范围；命中范围=" + scope}})
    return out, excluded


def find_vague(quote, rule):
    """口径风险：同一风险表述在同一单据内聚合为一条。

    为什么聚合：真实单据里「另计 / 按根 / 单边」大量出现在工艺说明备注里，
    逐行报会把真正该看的结论淹没（实测花溪单 12 条里 10 条是同一句「…等另计」）。
    聚合后每条给出：命中的模式、行号列表、涉及金额——信息不减，噪声大幅下降。
    只有 1 处命中时保持单行形态，方便直接定位。
    """
    by_pat = defaultdict(list)
    for r in quote["rows"]:
        text = " ".join([r["name"], r["spec"], r["note"], r["unit"]])
        for p in rule["patterns"]:
            if p in text:
                by_pat[p].append(r)
                break  # 一行只归入第一个命中的模式，避免重复计数
    out = []
    for p, rows in by_pat.items():
        if len(rows) == 1:
            r = rows[0]
            c = confidence(rule["strength"], 0.95, r["align_conf"] or 0.85, False)
            out.append({"cid": r["cid"], "concept": r["name"], "line": r["no"], "issue": f"命中「{p}」",
                        "amount": r["amount"], "rule": rule["id"], "confidence": c, "tier": tier(c),
                        "evidence": {"basis": "报价单风险表述清单 v0", "formula": f"命中「{p}」"}})
        else:
            amt = round(sum(r["amount"] for r in rows), 2)
            c = confidence(rule["strength"], 0.95, min(r["align_conf"] or 0.85 for r in rows), False)
            out.append({"cid": None, "concept": f"「{p}」×{len(rows)} 行",
                        "line": ",".join(r["no"] for r in rows),
                        "issue": f"命中「{p}」{len(rows)} 处，合并为 1 条口径风险",
                        "amount": amt, "rule": rule["id"], "confidence": c, "tier": tier(c),
                        "evidence": {"basis": "报价单风险表述清单 v0（按单据聚合）",
                                     "formula": f"同一模式命中 {len(rows)} 行 → 合并，行号与金额见左"}})
    return out


def find_unit_anomaly(quote, onto, rule):
    out = []
    for r in quote["rows"]:
        if not r["cid"]:
            continue
        # 单位异常规则依赖本体里声明的「默认单位」。这不是所有领域都需要的字段
        # （汽修按「次/套/支」计价，没有默认面积单位）——缺字段就跳过，不能崩。
        # 这条是迁移实验（装修→汽修）跑出来的隐式依赖。
        default = onto.by_id[r["cid"]].get("u")
        if not default:
            continue
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
                base = r["qty"] * r["price"]
                out.append({"cid": r["cid"], "concept": r["name"], "line": r["no"],
                            "issue": f"合价与数量×单价不一致（差 {diff:+.0f} 元）",
                            "rule": rule["id"], "confidence": c, "tier": tier(c),
                            "ratio": round(r["amount"] / base, 3) if base else None,
                            "evidence": {"basis": "报价单算术一致性",
                                         "formula": f"{r['qty']}×{r['price']}={r['qty']*r['price']:.0f}，单列 {r['amount']:.0f}"}})
    return out


def find_arithmetic_grouped(quote, rule):
    """算术校验，但把「同一比例反复出现」聚合成一条。

    真实单据常见做法：对主材行统一乘一个系数（如 10% 损耗）却不写进损耗列。
    逐行报会产生十几条同类告警，把真正的问题淹掉——这属于"口径"而不是"错行"，
    所以合并成一条，并明确说出比例与命中行数。
    """
    raw = find_arithmetic(quote, rule)
    if not raw:
        return raw

    by_ratio = defaultdict(list)
    others = []
    for x in raw:
        r = x.get("ratio")
        if r is None or r == 1:
            # 比例恰好是 1（差额来自四舍五入）→ 不是系统性系数，保留原样
            others.append(x)
        else:
            by_ratio[round(float(r), 3)].append(x)

    out = list(others)
    for ratio, items in by_ratio.items():
        if len(items) < 3:                       # 少于 3 次不算"系统性"
            out.extend(items)
            continue
        pct = (ratio - 1) * 100
        out.append({
            "rule": rule["id"], "concept": "多行合计",
            "line": ",".join(str(i.get("line")) for i in items[:6]) + ("…" if len(items) > 6 else ""),
            "issue": ("%d 行的「合价 ÷（数量×单价）」恒为 %.3f（%+.0f%%），"
                      "但表内未声明损耗/系数列" % (len(items), ratio, pct)),
            "detail": "逐行明细见数据文件；这是单据层面的口径问题，不是单行算错",
            "confidence": confidence(rule["strength"], 0.95, 0.95, True),
            "tier": "auto",
            "evidence": {"basis": "同一比例在 ≥3 行重复出现",
                         "formula": "amount / (qty × price) = const",
                         "sample": [i.get("concept") for i in items[:3]]},
        })
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
    """管理费率异常。

    注意：2026-11-01 引入「汇总行分层」后，管理费不再作为工序行出现在 rows 里
    （它是按费率在表尾算出来的），所以必须同时扫 summary_rows——
    否则这条规则会静默失效，而失效的规则比没有规则更危险（以为查过了）。
    """
    out = []
    total = quote["total"] or 1
    cands = [r for r in quote["rows"] if r.get("cid") == "mgmt_fee"]
    for r in (quote.get("summary_rows") or []):
        if "管理费" in r["name"]:
            cands.append(r)
    for r in cands:
        if r["amount"] / total > rule["threshold"]:
            rate = r["amount"] / total * 100
            c = confidence(rule["strength"], 0.95, r.get("align_conf") or 0.95, True)
            out.append({"cid": "mgmt_fee", "concept": r["name"], "line": r["no"],
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


def find_doc_recon(quote, rule):
    """R13 文档级对账：整单加总对不上。

    与 R8 的分工：R8 查「这一行算不算得对」，R13 查「整单加起来对不对」。
    真实单据里整单对不上比单行错更常见——报价单多为手工加总，改过某行单价
    却不重算合计是常态。本项目 5 份真实单据里 2 份对不上账。
    """
    rec = quote.get("recon") or {}
    if rec.get("ok") is not False:
        return []
    diff = rec["diff"]
    calc = round(rec["detail_sum"] + rec["fee_sum"], 2)
    c = confidence(rule["strength"], 0.98, 0.95, True)
    return [{
        "cid": None, "concept": "文档合计",
        "line": (rec.get("grand") or {}).get("no") or "—",
        "issue": "整单加总对不上：明细 %s + 取费 %s = %s，印刷合计 %s，差 %s 元" % (
            fmt_money(rec["detail_sum"]), fmt_money(rec["fee_sum"]), fmt_money(calc),
            fmt_money(rec["grand"]["amount"]), fmt_money(diff)),
        "amount": abs(diff), "rule": rule["id"], "confidence": c, "tier": tier(c),
        "evidence": {"basis": rule["then"]["evidence"]["basis"],
                     "formula": rule["then"]["evidence"]["formula"]},
    }]


_SCOPES = {"loaded": False, "material": set(), "meta": {}}


def template_scopes():
    """报价范围标签：哪些模板项属于「主材/安装」（业主可自购或另计）。"""
    if not _SCOPES["loaded"]:
        p = os.path.join(DATA, "template_scopes.json")
        if os.path.isfile(p):
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            _SCOPES["material"] = set(d.get("material_concepts") or [])
            _SCOPES["meta"] = d.get("meta") or {}
        _SCOPES["loaded"] = True
    return _SCOPES


def trim_template(template, scope):
    """按报价范围裁剪模板。

    scope='full'（默认，行为等同历史版本）——全部模板项参与判漏；
    scope='base'——只对基础施工负责，主材/安装类模板项不参与判漏。

    为什么必须有这个开关：124㎡ 那份是「基础报价单」，模板按全包口径要求门槛石/踢脚线/
    木门/洁具/灯具，7 条全被判漏——**不是判定能力差，是模板不知道单据范围**。
    裁剪只减少「要求项」，不改任何已列项的判定，避免范围声明变成漏报的挡箭牌。
    """
    if scope != "base":
        return list(template)
    mat = template_scopes()["material"]
    return [cid for cid in template if cid not in mat]


def audit(quotes, onto, templates, rules, coverage=None, template_key="old_house",
          scope="full"):
    """template_key: old_house（老房翻新，39 项）或 new_house（新房毛坯，28 项）。
    scope: full（全包，默认）或 base（只报基础施工，主材/安装类不判漏）。
    模板与范围都会写进报告——选错模板，漏项结论全错，所以做成显式参数。"""
    R = {r["id"]: r for r in rules["rules"]}
    template = trim_template(templates[template_key], scope)
    results = {}
    for q in quotes:
        others = [x for x in quotes if x is not q]
        missing = find_missing(q, others, template, onto, R["R1_missing_item"], coverage)
        dups, dup_excluded = find_duplicates(q, onto, R["R2_duplicate_known"], R["R3_duplicate_same_concept"], template)
        risks = []
        risks += find_vague(q, R["R4_vague_term"])
        risks += find_unit_anomaly(q, onto, R["R6_unit_anomaly"])
        risks += find_arithmetic_grouped(q, R["R8_arithmetic"])
        risks += find_qty_unit(q, R["R9_qty_unit"])
        risks += find_fee_rate(q, R["R10_fee_rate"])
        risks += find_spec_missing(q, R["R11_spec_missing"])
        risks += find_wp_height(q, R["R12_wp_height"])
        risks += find_split(q, R["R7_split_billing"], onto, template)
        risks += find_doc_recon(q, R["R13_doc_recon"])
        miss_cost = sum(m["ref_amount"] for m in missing if m["tier"] == "auto" and m["ref_amount"]) or 0.0
        dup_cut = sum(d["amount"] for d in dups if d["tier"] == "auto") or 0.0
        results[q["label"]] = {
            "quote": q, "missing": missing, "duplicates": dups, "risks": risks,
            "template_key": template_key, "template_size": len(template),
            "template_full_size": len(templates[template_key]), "scope": scope,
            "pricing": pricing.classify(q),
            "obligations": oblig.extract(q),
            "exposure": exposure(q, others, R["R4_vague_term"]),
            "missing_cost": round(miss_cost, 2), "duplicate_cut": round(dup_cut, 2),
            "dup_excluded": dup_excluded,
            "recon": q.get("recon"),
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
                src = "、".join(x["quote"] for x in m["ref_source"]) or "—"
                L.append(f"| {m['concept']} | {fmt_money(m['ref_amount']) if m['ref_amount'] else '—'} | {src} | {m['confidence']:.2f} | {m['tier']} | {m['evidence']['formula']} |")
            note = _missing_note(r["missing"])
            if note:
                L.append("\n" + note.replace("<b>", "**").replace("</b>", "**").replace("<div class=\"note\">", "> ").replace("</div>", "\n> ").replace("<div>", "\n> "))
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
    if res.get("_cross_unit"):
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
:root{--ink:#0A0A0A;--paper:#FAFAF8;--ikb:#002FA7;--amber:#B45309;--grey:#6B7280;--hair:#E4E2DC;--faint:#8A877F}
*{box-sizing:border-box}
body{font-family:-apple-system,"PingFang SC","Helvetica Neue",sans-serif;color:var(--ink);background:var(--paper);margin:0;line-height:1.75;font-size:14px}
.masthead{background:var(--ikb);color:#fff;padding:10px 48px;display:flex;justify-content:space-between;align-items:baseline}
.masthead .brand{font-weight:700;letter-spacing:.12em;font-size:13px}
.masthead .brand span{opacity:.75;font-weight:400;margin-left:10px;font-size:11px;letter-spacing:.28em}
.masthead .no{font-size:12px;letter-spacing:.08em;opacity:.92;font-variant-numeric:tabular-nums}
.wrap{max-width:980px;margin:0 auto;padding:0 48px 56px}
h1{font-size:34px;line-height:1.25;margin:38px 0 6px;font-weight:700;letter-spacing:-.01em}
.sub{color:var(--faint);font-size:13px;margin-bottom:8px}
.meta{font-size:12px;color:var(--grey);margin-bottom:24px}
.meta code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11px;background:#EFEEE9;padding:2px 6px;border-radius:3px}
.kpis{display:grid;grid-template-columns:repeat(3,1fr);border-top:2px solid var(--ink);border-bottom:1px solid var(--hair);margin:22px 0 30px}
.kpi{padding:16px 20px;border-left:1px solid var(--hair)}
.kpi:first-child{border-left:none;padding-left:0}
.kpi .l{font-size:12px;color:var(--grey)}
.kpi .v{font-size:30px;font-weight:700;margin:2px 0;font-variant-numeric:tabular-nums;letter-spacing:-.01em}
.kpi .n{font-size:12px;color:var(--faint)}
h2{font-size:15px;margin:34px 0 12px;font-weight:700;letter-spacing:.05em}
h2 .idx{color:var(--ikb);margin-right:10px;font-variant-numeric:tabular-nums}
table{border-collapse:collapse;width:100%;margin:10px 0 26px;font-size:13px}
th{font-size:11px;font-weight:600;color:var(--grey);letter-spacing:.05em;text-align:left;padding:8px 10px;border-bottom:2px solid var(--ink);white-space:nowrap}
td{padding:9px 10px;border-bottom:1px solid var(--hair);vertical-align:top}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
tr:last-child td{border-bottom:1px solid var(--ink)}
.bar{height:6px;background:#EFEEE9;position:relative;margin-top:5px;width:140px}
.bar i{display:block;height:6px;background:var(--ikb)}
.barcell{min-width:170px}
.tag{display:inline-block;padding:0 8px;font-size:11px;font-weight:600;letter-spacing:.04em;border:1px solid;white-space:nowrap}
.tag.auto{color:var(--ikb);border-color:var(--ikb)}
.tag.review{color:var(--amber);border-color:var(--amber)}
.tag.manual{color:var(--grey);border-color:var(--grey)}
.evid{font-size:12px;color:var(--grey)}
.evid b{color:var(--ink);font-weight:600}
.evid code{font-family:ui-monospace,Menlo,monospace;font-size:11px;background:#EFEEE9;padding:1px 5px;border-radius:3px}
.sub-h{font-size:13px;letter-spacing:.05em;margin:26px 0 8px;font-weight:700}
.note{border-left:3px solid var(--ikb);background:#F2F1EC;padding:12px 16px;font-size:13px;color:#3D3B36;margin:14px 0 26px}
.recon{border-top:2px solid var(--hair);border-bottom:1px solid var(--hair);padding:11px 0;margin:0 0 22px;font-size:12.5px;display:flex;gap:16px;flex-wrap:wrap;align-items:baseline}
.recon .t{font-weight:700;letter-spacing:.06em;font-size:11px;color:var(--grey);min-width:56px}
.recon .k{font-variant-numeric:tabular-nums;color:var(--grey)}
.recon .k b{color:var(--ink);font-weight:600}
.recon .st{font-weight:700;font-size:11px;letter-spacing:.06em;padding:1px 8px;border:1px solid}
.recon .st.ok{color:#0F7B4F;border-color:#0F7B4F}
.recon .st.bad{color:var(--amber);border-color:var(--amber)}
.recon .hint{flex-basis:100%;color:var(--faint);font-size:11.5px;line-height:1.6}
.concl{border-top:2px solid var(--ink);margin-top:38px;padding-top:12px}
.concl ul{margin:8px 0;padding-left:1.2em}
.concl li{margin:6px 0}
footer{margin-top:44px;padding-top:14px;border-top:1px solid var(--hair);color:var(--faint);font-size:11.5px;display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap}
footer code{font-family:ui-monospace,Menlo,monospace}
@media print{body{background:#fff;padding:14mm 16mm}.masthead{margin:-14mm -16mm 8mm;padding:10px 16mm}.wrap{max-width:none;padding:0}.kpis,tr,.note{break-inside:avoid}}
</style></head><body>
<div class="masthead"><div class="brand">装明白<span>COMPUTABLE AUDIT</span></div><div class="no">{{DOC_NO}}</div></div>
<div class="wrap">
<h1>{{TITLE}}</h1>
<div class="sub">可计算审计报告 —— 只做计费核对，不判断贵不贵</div>
<div class="meta">{{META}}</div>
<div class="kpis">{{KPIS}}</div>
<h2><span class="idx">01</span>总览</h2>
{{SUMMARY}}
{{RECON}}
<div class="note">{{NOTE}}</div>
{{SECTIONS}}
<div class="concl">
<h2><span class="idx">{{CONCL_IDX}}</span>结论口径</h2>
<ul>
<li>本报告不判断哪家便宜，只回答：缺了什么、重了什么、哪些口径不可核、补漏后真实成本是多少。</li>
<li>最低报价不等于最低成本：补齐漏项后排序会变化；开口项另计敞口。</li>
<li>低置信度结论不进入核减金额，只进入待确认清单——宁可少报，不可错报。</li>
</ul>
</div>
<footer><div>装明白 v0.2 引擎自动生成｜证据链：原表行号 + 对照基准 + 计算公式 + 置信度</div><div>判定指纹 <code>{{FP}}</code>｜ECE 校准报告见评测文档</div></footer>
</div></body></html>"""


def _report_fingerprint(res):
    """报告判定指纹：对审计结果做规范化序列化后取 sha256 前 16 位。
    与 scripts/snapshot_predictions.py 的冻结口径同源（sort_keys + ensure_ascii=False）。"""
    body = json.dumps(res, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(body.encode()).hexdigest()[:16]


def _missing_note(missing):
    """漏项表的脚注：区分「可计价项」与「只能定性的工艺项」。

    这个区分是从真实数据里长出来的：4 份单据里闭水试验/打压试验/挂网/打胶
    从来没被单列计价过——不是数据缺，是行业就不单收这份钱。
    所以「没金额」不等于「不重要」，必须说清楚，否则会被读成「这条可以忽略」。
    """
    corpus = [m for m in missing if m.get("ref_basis") == "corpus"]
    noamt = [m for m in missing if not m["ref_amount"]]
    parts = []
    if corpus:
        parts.append("其中 <b>%d 条</b>金额取自「参照价库」——本项目 4 份家装单据同工序金额的中位数，"
                     "<b>不是市场行情价</b>，只用来估算补上这项要多少钱的量级。" % len(corpus))
    if noamt:
        parts.append("<b>%d 条没有金额</b>：这类工序在 4 份真实单据里从未单列计价（闭水试验、打压试验、"
                     "挂网、打胶等多为工艺项），只能要求补做、不能估钱——宁可留空，不可编数。" % len(noamt))
    if not parts:
        return ""
    return '<div class="note">' + "".join("<div>%s</div>" % p for p in parts) + "</div>"


def build_html(quotes, res):
    fp = _report_fingerprint(res)
    doc_no = "№ ZMB-%s-%s" % (datetime.now().strftime("%Y%m%d"), fp[:4])
    title = "%d 家报价 · 可计算审计" % len(quotes) if len(quotes) > 1 else "单份报价 · 可计算审计"
    meta = ("%s ｜ 生成于 %s ｜ 判定结果可由 <code>out/audit_result.json</code> 逐条复核"
            % (quotes[0].get("scene", "多单对比场景"), datetime.now().strftime("%Y-%m-%d %H:%M")))
    kpis = []
    for q in quotes:
        r = res[q["label"]]
        kpis.append('<div class="kpi"><div class="l">%s · 真实成本</div>'
                    '<div class="v">%s <small>元</small></div>'
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
    tpl_name = {"old_house": "老房翻新", "new_house": "新房毛坯"}
    scope_name = {"full": "全包（含主材）", "base": "基础施工（主材另计）"}
    r0 = res[quotes[0]["label"]]
    note += ("<br><b>本次口径</b>：模板「%s」%d 项（完整 %d 项）｜报价范围「%s」。"
             "范围只减少「要求项」，不改任何已列项的判定。" % (
                 tpl_name.get(r0.get("template_key"), r0.get("template_key")),
                 r0.get("template_size") or 0, r0.get("template_full_size") or 0,
                 scope_name.get(r0.get("scope"), r0.get("scope"))))
    secs = []
    idx = 1
    for q in quotes:
        r = res[q["label"]]
        idx += 1
        h = ["<h2><span class='idx'>%02d</span>%s 明细</h2>" % (idx, q["label"]),
             "<p>总价 <b>%s</b>；漏项补项 <b>%s</b>；重复剔除 <b>%s</b>；真实成本 <b>%s</b>。</p>" %
             (fmt_money(q["total"]), fmt_money(r["missing_cost"]), fmt_money(r["duplicate_cut"]), fmt_money(r["effective"]))]
        pr = r.get("pricing") or {}
        if pr:
            h.append("<div class='note'><b>计价体系：%s</b>（置信度 %.2f）｜%s"
                     "<br><span class='evid'>依据：%s</span></div>" % (
                         pr.get("label"), pr.get("confidence", 0), pr.get("implication", ""),
                         "；".join(pr.get("evidence") or [])))
        if r["missing"]:
            h.append("<table><tr><th>漏项工序</th><th class='num'>参照金额</th><th>参照来源</th><th class='num'>置信度</th><th>档位</th></tr>")
            for m in r["missing"]:
                src = "、".join(x["quote"] for x in m["ref_source"]) or "—"
                amt = fmt_money(m["ref_amount"]) if m["ref_amount"] else "—"
                h.append("<tr><td>%s</td><td class='num'>%s</td><td class='evid'>%s</td><td class='num'>%.2f</td>"
                         "<td><span class='tag %s'>%s</span></td></tr>" %
                         (m["concept"], amt, src, m["confidence"], m["tier"], m["tier"]))
            h.append("</table>")
            h.append(_missing_note(r["missing"]))
        if r["duplicates"]:
            h.append("<table><tr><th>重复工序</th><th>行号</th><th class='num'>金额</th><th>依据</th><th class='num'>置信度</th></tr>")
            for d in r["duplicates"]:
                h.append("<tr><td>%s</td><td class='evid'><code>%s</code></td><td class='num'>%s</td>"
                         "<td class='evid'>%s</td><td class='num'>%.2f</td></tr>" %
                         (d["concept"], ",".join(d["lines"]), fmt_money(d["amount"]),
                          d["evidence"]["basis"], d["confidence"]))
            h.append("</table>")
        if r["risks"]:
            h.append("<table><tr><th>行号</th><th>项目</th><th>问题</th><th>依据</th><th>档位</th></tr>")
            for x in r["risks"][:12]:
                ln = x.get("line") or ",".join(x.get("lines", []))
                h.append("<tr><td class='evid'><code>%s</code></td><td>%s</td><td>%s</td>"
                         "<td class='evid'>%s</td><td><span class='tag %s'>%s</span></td></tr>" %
                         (ln, x["concept"], x["issue"], x["evidence"]["basis"], x["tier"], x["tier"]))
            h.append("</table>")
        obs = r.get("obligations") or []
        if obs:
            h.append("<h3 class='sub-h'>签约前必须书面确认（%d 类开口条款）</h3>" % len(obs))
            h.append("<table><tr><th>条款</th><th>原文命中</th><th class='num'>涉及金额</th>"
                     "<th>要确认什么</th><th>要留什么证据</th></tr>")
            for o in obs:
                h.append("<tr><td><b>%s</b></td><td class='evid'>%s<br><code>行 %s</code></td>"
                         "<td class='num'>%s</td><td>%s</td><td class='evid'>%s</td></tr>" % (
                             o["title"], "、".join(o["patterns"]),
                             ",".join(o["lines"][:8]) + ("…" if len(o["lines"]) > 8 else ""),
                             fmt_money(o["amount"]) if o["amount"] else "—",
                             o["confirm"], o["evidence"]))
            h.append("</table>")
            h.append("<div class='note'>%s</div>" % oblig.summary(obs))
        secs.append("".join(h))

    # 对账条：整单加起来对不对。差额本身是一条结论，不是噪声——
    # 报价单自己都对不上账，签约前必须逐项问清楚。
    rec_lines = []
    for q in quotes:
        rec = (res[q["label"]] or {}).get("recon") or {}
        head = ('<span class="t">对账</span>' if len(quotes) == 1
                else '<span class="t">对账 · %s</span>' % q["label"])
        if not rec.get("grand"):
            rec_lines.append('<div class="recon">%s<span class="k">本单未提供印刷合计，跳过对账'
                             '（明细之和 <b>%s</b>）</span></div>'
                             % (head, fmt_money(rec.get("detail_sum") or 0)))
            continue
        calc = round((rec["detail_sum"] or 0) + (rec["fee_sum"] or 0), 2)
        ok = rec.get("ok")
        diff = rec.get("diff") or 0
        rec_lines.append(
            '<div class="recon">%s'
            '<span class="k">工序明细 <b>%s</b></span>'
            '<span class="k">取费 <b>%s</b></span>'
            '<span class="k">合计 <b>%s</b></span>'
            '<span class="k">印刷合计 <b>%s</b></span>'
            '<span class="k">差 <b>%s</b> 元</span>'
            '<span class="st %s">%s</span>'
            '%s</div>' % (
                head, fmt_money(rec["detail_sum"]), fmt_money(rec["fee_sum"]), fmt_money(calc),
                fmt_money(rec["grand"]["amount"]), fmt_money(diff),
                "ok" if ok else "bad", "对平" if ok else "不符",
                "" if ok else '<span class="hint">差额三个常见来源：①原单手工加总错误 '
                              '②照片遮挡/转录缺行 ③取费未单列。签约前逐项核对，别默认是自己看错。</span>'))
    recon_html = "".join(rec_lines)

    return (HTML_TPL.replace("{{DOC_NO}}", doc_no).replace("{{TITLE}}", title)
            .replace("{{META}}", meta)
            .replace("{{KPIS}}", "".join(kpis))
            .replace("{{SUMMARY}}", summary).replace("{{RECON}}", recon_html)
            .replace("{{NOTE}}", note)
            .replace("{{SECTIONS}}", "".join(secs))
            .replace("{{CONCL_IDX}}", "%02d" % (idx + 1))
            .replace("{{FP}}", fp))


def result_digest(quote, res):
    """判定摘要（16 位）。审计包用它做「同一输入 + 同一标准 → 同一结论」的比对基准。

    只摘要「结论」，不摘要时间、文件名等易变字段——否则同一份单据换个文件名就对不上，
    校验器会变成噪声。金额保留 2 位，避免浮点尾差。
    """
    payload = {
        "label_total": round(quote.get("total") or 0, 2),
        "missing": sorted([[m.get("cid") or "", m.get("tier") or "",
                            round(m.get("ref_amount") or 0, 2)] for m in res.get("missing") or []]),
        "duplicates": sorted([[d.get("concept") or "", sorted(d.get("lines") or []),
                               round(d.get("amount") or 0, 2)] for d in res.get("duplicates") or []]),
        "risks": sorted([[x.get("rule") or "", str(x.get("line") or ""),
                          round(x.get("amount") or 0, 2)] for x in res.get("risks") or []]),
        "recon": None if not res.get("recon") else [
            round(res["recon"].get("detail_sum") or 0, 2),
            None if res["recon"].get("diff") is None else round(res["recon"]["diff"], 2)],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False)
                          .encode()).hexdigest()[:16]


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
