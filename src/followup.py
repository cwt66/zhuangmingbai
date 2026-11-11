# -*- coding: utf-8 -*-
"""问询闭环：对方回了新版报价之后，逐条核对「问的事到底改了没有」。

## 为什么这件事值得单独做

问询单发出去以后，业主拿到一份「已修改」的新版，通常只会看总价——
但总价降了不等于问的问题解决了：可能把问的漏项补上、同时把别的单价调高，
也可能只是删了一行重复计价、其余一字未动。

这个模块把「问询单」当成施工里的 **punch list（整改清单）**：
每条问询是一个待关闭项，新版报价是对方的"整改回执"，
判定只有两种——**已落实 / 未落实**，每条都给出可回原表核对的位置。

## 三条纪律

1. **只核对事实，不评价答复**：写「闭水试验已单列（第 132 行）」，
   不写「对方很有诚意」；写「乳胶漆单价由 18 调到 22」，不写「涨价不合理」。
2. **判定必须能指回原表**：每条状态都带行号 / 金额 / 条款原文，无法核对的标 `unknown`，
   不猜。
3. **单独列出「新增问题」**：对方补了 A 却顺手加了 B，只报「落实率 80%」会掩盖 B。
   这一条学 reviewdog 的 `filter-mode=added`——只关心这次改动新引入的东西。
"""
from collections import defaultdict

try:  # 支持 `from src import followup` 与 sys.path 里直接 `import followup`
    from . import pipeline
except ImportError:  # pragma: no cover
    import pipeline

STATUS_CN = {"resolved": "已落实", "open": "未落实", "unknown": "无法自动核对"}


def _rows_by_cid(quote):
    out = defaultdict(list)
    for r in (quote.get("rows") or []):
        if r.get("cid"):
            out[r["cid"]].append(r)
    return out


def _quote_text(quote):
    parts = []
    for r in (quote.get("rows") or []):
        parts.append(str(r.get("name") or ""))
        parts.append(str(r.get("spec") or ""))
        parts.append(str(r.get("note") or ""))
    return " ".join(parts)


def _money(x):
    try:
        return "{:,.2f}".format(float(x))
    except (TypeError, ValueError):
        return "—"


def _lines_of(rows):
    return "、".join(str(r.get("no")) for r in rows)


def _judge(item, rows, text, after_res):
    """返回 (status, evidence)。每一支都必须能说清「凭哪一行判的」。"""
    kind = item.get("kind")
    cid = item.get("cid")
    risks = after_res.get("risks") or []
    recon = after_res.get("recon") or {}

    if kind == "missing":
        rs = rows.get(cid) or []
        if not rs:
            # 宁可漏判也不替对方说「补过了」：概念对不上时交给人工
            return "open", "新版未找到同一概念（%s）的条目；若对方换了写法，请人工核对" % (cid or "—")
        amt = sum(float(r.get("amount") or 0) for r in rs)
        if amt > 0:
            return "resolved", "新版第 %s 行已单列，金额 %s 元" % (_lines_of(rs), _money(amt))
        return "open", "新版第 %s 行有该项目但金额为 0" % _lines_of(rs)

    if kind == "duplicate":
        if not cid:
            return "unknown", "该重复项没有概念锚点，无法机读比对"
        # 用引擎在新版上的结论，而不是数行数：同一概念可能合法拆成多行（分部位）
        left = [d for d in (after_res.get("duplicates") or []) if d.get("cid") == cid]
        if not left:
            return "resolved", "新版该项已不再判为重复计价"
        return "open", "新版仍判为重复（第 %s 行）" % "、".join(
            str(x) for x in (left[0].get("lines") or []))

    if kind == "arithmetic":
        line = str(item.get("line"))
        left = [x for x in risks
                if x.get("rule") == "R8_arithmetic" and str(x.get("line")) == line]
        if not left:
            return "resolved", "新版第 %s 行已无算术差异" % line
        amt = left[0].get("amount")
        if amt is None:
            return "open", "新版第 %s 行合价仍与「数量×单价」不符（差额未量化）" % line
        return "open", "新版第 %s 行仍差 %s 元" % (line, _money(amt))

    if kind == "recon":
        if recon.get("ok") or recon.get("diff") is None:
            return "resolved", "新版明细与印刷合计已对上"
        return "open", "新版合计仍差 %s 元" % _money(recon.get("diff"))

    if kind == "unit":
        rs = rows.get(cid) or []
        if not rs:
            return "resolved", "新版已无该项"
        dims = {pipeline.unit_dim(r.get("unit")) for r in rs} - {None, "item"}
        if len(dims) <= 1:
            return "resolved", "新版已统一为%s（第 %s 行）" % (
                "/".join(sorted(dims)) or "单一量纲", _lines_of(rs))
        return "open", "新版仍同时存在 %s（第 %s 行）" % ("/".join(sorted(dims)), _lines_of(rs))

    if kind == "obligation":
        left = [p for p in (item.get("patterns") or []) if p and p in text]
        if not left:
            return "resolved", "新版原文已不再出现「%s」" % "、".join(item.get("patterns") or [])
        return "open", "新版仍出现「%s」" % "、".join(left)

    return "unknown", "该条目没有可机读的锚点（旧版问询单），请人工核对"


def _risk_key(x):
    """风险的「身份」。分组条目的 line 是一串行号（`7,23,27…`），
    只要单据多一行它就会变——按 (rule, line) 比会把「行号集合变了」误报成新问题。"""
    line = str(x.get("line") or "")
    if x.get("cid"):
        return (x.get("rule"), "cid:" + str(x["cid"]))
    if "," in line or "行" in line:
        return (x.get("rule"), "group")
    return (x.get("rule"), line)


def _signature(res):
    """新版 - 旧版 的差异键：只比「问题本身」，不比金额措辞。"""
    miss = {m.get("cid") for m in (res.get("missing") or []) if m.get("cid")}
    dup = {d.get("cid") for d in (res.get("duplicates") or []) if d.get("cid")}
    risk = {_risk_key(x) for x in (res.get("risks") or [])}
    return {"missing": miss, "duplicates": dup, "risks": risk}


def _new_findings(before_res, after_res):
    if not before_res:
        return []
    b, a = _signature(before_res), _signature(after_res)
    out = []
    for m in after_res.get("missing") or []:
        if m.get("cid") and m["cid"] not in b["missing"]:
            out.append({"kind": "missing", "subject": m.get("concept") or m.get("name"),
                        "detail": "新版新增漏项"})
    for d in after_res.get("duplicates") or []:
        if d.get("cid") and d["cid"] not in b["duplicates"]:
            out.append({"kind": "duplicate", "subject": d.get("concept"),
                        "detail": "新版新增重复计价 %s 元" % _money(d.get("amount"))})
    for x in after_res.get("risks") or []:
        if _risk_key(x) not in b["risks"]:
            out.append({"kind": x.get("rule") or "risk",
                        "subject": x.get("concept") or ("第 %s 行" % x.get("line")),
                        "detail": "新版新出现的口径/算术风险 %s 元" % _money(x.get("amount"))})
    return out


def check(inq, after_quote, after_res, before_res=None):
    """逐条核对问询单在新版里是否落实。

    inq：`inquiry.build()` 的产物（含 kind / cid / line / patterns 锚点）
    after_quote / after_res：新版报价与它的 `audit()` 结果
    before_res：旧版 audit 结果；给了它才能报「新增问题」
    """
    rows = _rows_by_cid(after_quote)
    text = _quote_text(after_quote)

    items, counts = [], {"resolved": 0, "open": 0, "unknown": 0}
    for sec in inq["sections"]:
        for it in sec["items"]:
            status, ev = _judge(it, rows, text, after_res)
            counts[status] = counts.get(status, 0) + 1
            items.append({"no": len(items) + 1, "section": sec["title"],
                          "subject": it["subject"], "kind": it.get("kind") or "unknown",
                          "status": status, "evidence": ev})

    judged = counts["resolved"] + counts["open"]
    before_total = inq["meta"].get("declared_total")
    after_total = after_quote.get("total")
    return {
        "meta": {
            "before_label": inq["meta"].get("quote_label"),
            "after_label": after_quote.get("label"),
            "before_total": before_total,
            "after_total": after_total,
            "delta": (None if before_total is None or after_total is None
                      else round(float(after_total) - float(before_total), 2)),
        },
        "items": items,
        "summary": {
            "total": len(items),
            "resolved": counts["resolved"],
            "open": counts["open"],
            "unknown": counts["unknown"],
            "rate": round(counts["resolved"] / judged, 3) if judged else None,
        },
        "new_findings": _new_findings(before_res, after_res),
        "limits": "只核对「问的事实在新版里有没有变化」，不评价答复是否合理、也不判断价格高低。",
    }


def to_markdown(r):
    m, s = r["meta"], r["summary"]
    rate = "—" if s["rate"] is None else "%d%%" % round(s["rate"] * 100)
    L = ["# 问询落实情况核对", "",
         "> 旧版：%s｜%s 元；新版：%s｜%s 元；差额 **%s** 元" % (
             m["before_label"] or "—", _money(m["before_total"]),
             m["after_label"] or "—", _money(m["after_total"]),
             ("%+d" % m["delta"]) if isinstance(m["delta"], (int, float)) else "—"),
         "", "共 %d 条问询：**已落实 %d / 未落实 %d / 无法自动核对 %d**，落实率 %s。" % (
             s["total"], s["resolved"], s["open"], s["unknown"], rate), "",
         "| # | 问询条目 | 状态 | 依据 |", "|---:|---|---|---|"]
    for it in r["items"]:
        L.append("| %d | %s | %s | %s |" % (
            it["no"], it["subject"], STATUS_CN.get(it["status"], it["status"]), it["evidence"]))
    L.append("")
    if r["new_findings"]:
        L.append("## 新版新增的问题（%d 项）" % len(r["new_findings"]))
        L.append("")
        for x in r["new_findings"]:
            L.append("- **%s**：%s" % (x["subject"], x["detail"]))
        L.append("")
    else:
        L.append("新版没有引入新的漏项 / 重复 / 口径问题。")
        L.append("")
    L.append("---")
    L.append("")
    L.append("> %s" % r["limits"])
    return "\n".join(L) + "\n"


def _esc(s):
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def to_html(r):
    """网页版：和问询单同一套视觉（IKB × 纸白 × 墨黑），可直接 Ctrl/⌘+P 存 PDF。"""
    m, s = r["meta"], r["summary"]
    rate = "—" if s["rate"] is None else "%d%%" % round(s["rate"] * 100)
    delta = m["delta"]
    delta_s = ("%+d" % delta) if isinstance(delta, (int, float)) else "—"
    tone = "" if not isinstance(delta, (int, float)) else (" up" if delta > 0 else " down")
    css = """
:root{--ink:#0A0A0A;--paper:#FAFAF8;--ikb:#002FA7;--grey:#6B7280;--hair:#E4E2DC;--faint:#8A877F;
--ok:#0F5132;--okbg:#E7F3EC;--open:#8A4B0F;--openbg:#FBEFE2;--unk:#4B5563;--unkbg:#EEF0F3}
*{box-sizing:border-box}
body{font-family:-apple-system,"PingFang SC","Helvetica Neue",sans-serif;background:var(--paper);
 color:var(--ink);margin:0;line-height:1.75;font-size:14px}
.masthead{background:var(--ikb);color:#fff;padding:10px 48px;display:flex;justify-content:space-between}
.masthead .brand{font-weight:700;letter-spacing:.12em;font-size:13px}
.wrap{max-width:1000px;margin:0 auto;padding:0 48px 56px}
h1{font-size:30px;margin:34px 0 6px;letter-spacing:-.01em}
.sub{color:var(--faint);font-size:13px}
.chips{display:flex;gap:10px;flex-wrap:wrap;margin:22px 0 6px}
.chip{background:#fff;border:1px solid var(--hair);border-radius:2px;padding:12px 16px;min-width:118px}
.chip .n{font-size:24px;font-weight:700;line-height:1.2}
.chip .t{font-size:12px;color:var(--grey)}
.chip.rate .n{color:var(--ikb)}
.lead{margin:14px 0 4px;font-size:15px}
.lead b{font-size:17px}
/* 涨红跌绿：这是中国用户看报价的习惯 */
.lead b.up{color:#B33A2B}
.lead b.down{color:#0F5132}
table{width:100%;border-collapse:collapse;margin:18px 0 8px;font-size:13px}
th{text-align:left;color:var(--grey);font-weight:600;border-bottom:1px solid var(--ink);
 padding:8px 6px;font-size:12px;letter-spacing:.03em}
td{border-bottom:1px solid var(--hair);padding:9px 6px;vertical-align:top}
td.no{width:36px;color:var(--faint)}
td.st{width:96px}
.badge{display:inline-block;font-size:12px;padding:2px 8px;border-radius:2px;white-space:nowrap}
.badge.resolved{background:var(--okbg);color:var(--ok)}
.badge.open{background:var(--openbg);color:var(--open)}
.badge.unknown{background:var(--unkbg);color:var(--unk)}
.ev{color:var(--grey)}
h2{font-size:15px;margin:30px 0 8px;letter-spacing:.04em}
ul.new{margin:6px 0 0;padding-left:20px;color:var(--grey)}
.note{background:#fff;border-left:4px solid var(--ikb);padding:12px 16px;color:var(--grey);
 font-size:12.5px;margin:22px 0}
footer{margin-top:34px;border-top:1px solid var(--hair);padding-top:12px;color:var(--faint);font-size:12px}
@media print{body{background:#fff}.wrap{padding:0}.masthead{background:#fff;color:var(--ink);
 border-bottom:2px solid var(--ikb)}}
"""
    parts = [
        '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">',
        "<title>问询落实情况核对 · %s → %s</title><style>%s</style></head><body>" % (
            _esc(m["before_label"]), _esc(m["after_label"]), css),
        '<div class="masthead"><div class="brand">装明白 · 问询落实核对</div>'
        '<div style="font-size:12px">只核对事实，不评价答复</div></div>',
        '<div class="wrap">',
        "<h1>问询落实情况核对</h1>",
        '<div class="sub">旧版 %s（%s 元）→ 新版 %s（%s 元）</div>' % (
            _esc(m["before_label"]), _money(m["before_total"]),
            _esc(m["after_label"]), _money(m["after_total"])),
        '<div class="chips">',
        '<div class="chip"><div class="n">%d</div><div class="t">问询条目</div></div>' % s["total"],
        '<div class="chip"><div class="n">%d</div><div class="t">已落实</div></div>' % s["resolved"],
        '<div class="chip"><div class="n">%d</div><div class="t">未落实</div></div>' % s["open"],
        '<div class="chip"><div class="n">%d</div><div class="t">无法自动核对</div></div>' % s["unknown"],
        '<div class="chip rate"><div class="n">%s</div><div class="t">落实率</div></div>' % rate,
        "</div>",
        '<p class="lead">总价变化 <b class="%s">%s 元</b>——'
        '总价降了不等于问的问题解决了，要看下面这一列。</p>' % (tone.strip(), delta_s),
        "<table><thead><tr><th>#</th><th>问询条目</th><th>状态</th><th>依据</th></tr></thead><tbody>",
    ]
    for it in r["items"]:
        parts.append(
            '<tr><td class="no">%d</td><td>%s</td><td class="st">'
            '<span class="badge %s">%s</span></td><td class="ev">%s</td></tr>' % (
                it["no"], _esc(it["subject"]), _esc(it["status"]),
                _esc(STATUS_CN.get(it["status"], it["status"])), _esc(it["evidence"])))
    parts.append("</tbody></table>")
    if r["new_findings"]:
        parts.append("<h2>新版新增的问题（%d 项）</h2><ul class=\"new\">" % len(r["new_findings"]))
        for x in r["new_findings"]:
            parts.append("<li><b>%s</b>：%s</li>" % (_esc(x["subject"]), _esc(x["detail"])))
        parts.append("</ul>")
    else:
        parts.append("<h2>新版新增的问题</h2>"
                     '<p class="ev">无。新版没有引入新的漏项 / 重复 / 口径问题。</p>')
    parts.append('<div class="note">%s</div>' % _esc(r["limits"]))
    parts.append("<footer>本页由装明白按两版报价原文生成；每条状态都可回到新版的对应行号核对。"
                 "落实不等于质量合格——它只说明「问到的那件事，原文变了没有」。</footer>")
    parts.append("</div></body></html>")
    return "".join(parts)


# ---------------------------------------------------------------------------
# 自测：双向断言。重点不是「能判出已落实」，而是
# **没改的绝不能被判成已落实**——那等于替对方背书。
# ---------------------------------------------------------------------------

def _q(label, total, rows):
    return {"label": label, "total": total, "rows": rows}


def _row(no, name, cid, unit="m2", amount=100.0, spec=""):
    return {"no": str(no), "name": name, "spec": spec, "note": "",
            "unit": unit, "qty": 1.0, "price": amount, "amount": amount, "cid": cid}


def _inq(items):
    return {"meta": {"quote_label": "旧版", "declared_total": 10000.0},
            "sections": [{"title": "一、测试", "intro": "", "items": items}]}


def self_test():
    results = []

    def case(name, ok, detail=""):
        results.append({"name": name, "status": "pass" if ok else "fail", "detail": detail})

    # 1) 锚点必须齐：问询单每种条目都要带 kind，否则闭环核不了
    try:
        try:
            from . import inquiry as _inquiry_mod
        except ImportError:
            import inquiry as _inquiry_mod
        fake_res = {
            "missing": [{"concept": "闭水试验", "cid": "c1", "tier": "review", "ref_amount": None}],
            "duplicates": [{"concept": "地面找平", "cid": "c2", "amount": 98, "lines": ["57"],
                            "evidence": {"basis": ""}}],
            "risks": [{"rule": "R8_arithmetic", "line": "3", "amount": 5,
                       "cid": "c3", "evidence": {"basis": ""}}],
            "recon": {"diff": 5, "detail_sum": 1, "fee_sum": 1, "grand": {"amount": 2}},
            "obligations": [{"title": "按实结算", "confirm": "？", "patterns": ["按实结算"],
                             "amount": 100}],
            "cross_unit": [],
        }
        built = _inquiry_mod.build({"label": "甲", "total": 1}, fake_res)
        kinds = [it.get("kind") for s in built["sections"] for it in s["items"]]
        case("问询单每种条目都带 kind 锚点", all(kinds) and len(kinds) >= 5, str(kinds))
    except Exception as e:  # noqa: BLE001
        case("问询单每种条目都带 kind 锚点", False, repr(e))

    # 2) 漏项补齐 → 已落实；没补 → 未落实（负向）
    q_done = _q("新版", 10100.0, [_row(1, "闭水试验", "c1", amount=150.0)])
    q_same = _q("新版", 10000.0, [_row(9, "别的东西", "c9")])
    it = [{"no": 1, "subject": "闭水试验", "kind": "missing", "cid": "c1"}]
    r1 = check(_inq(it), q_done, {"missing": [], "duplicates": [], "risks": [], "recon": {}})
    r2 = check(_inq(it), q_same, {"missing": [], "duplicates": [], "risks": [], "recon": {}})
    case("补上了 → 已落实", r1["items"][0]["status"] == "resolved", r1["items"][0]["evidence"])
    case("没补 → 未落实（不许替对方说改好了）", r2["items"][0]["status"] == "open",
         r2["items"][0]["evidence"])

    # 3) 重复计价：合并成 1 行才算落实，剩 2 行不算
    dup = [{"no": 1, "subject": "地面找平", "kind": "duplicate", "cid": "c2"}]
    one = _q("新版", 10000.0, [_row(57, "地面找平", "c2")])
    two = _q("新版", 10000.0, [_row(57, "地面找平", "c2"), _row(58, "地面找平", "c2")])
    d1 = check(_inq(dup), one, {"duplicates": [], "risks": [], "recon": {}})
    d2 = check(_inq(dup), two,
               {"duplicates": [{"cid": "c2", "lines": ["57", "58"]}], "risks": [], "recon": {}})
    case("引擎已不判重复 → 已落实", d1["items"][0]["status"] == "resolved")
    case("引擎仍判重复 → 未落实", d2["items"][0]["status"] == "open")
    case("重复项没有 cid → 标 unknown，不猜",
         check(_inq([{"no": 1, "subject": "x", "kind": "duplicate", "cid": None}]),
               one, {"duplicates": [], "risks": [], "recon": {}})["items"][0]["status"] == "unknown")

    # 4) 口径：统一为一种量纲才算落实
    unit = [{"no": 1, "subject": "踢脚线", "kind": "unit", "cid": "c4"}]
    uni = _q("新版", 1, [_row(1, "踢脚线", "c4", unit="m"), _row(2, "踢脚线", "c4", unit="M")])
    mixed = _q("新版", 1, [_row(1, "踢脚线", "c4", unit="m"), _row(2, "踢脚线", "c4", unit="根")])
    u1 = check(_inq(unit), uni, {"risks": [], "recon": {}})
    u2 = check(_inq(unit), mixed, {"risks": [], "recon": {}})
    case("同量纲不同写法 → 已落实", u1["items"][0]["status"] == "resolved",
         u1["items"][0]["evidence"])
    case("仍是两种量纲 → 未落实", u2["items"][0]["status"] == "open")
    # m 与 M 必须被认成同一量纲，否则又会像 R5 那样误报
    case("m/M 大小写不算两种口径",
         len({pipeline.unit_dim("m"), pipeline.unit_dim("M")} - {None}) == 1)

    # 5) 开口条款：原文删掉才算落实
    ob = [{"no": 1, "subject": "按实结算", "kind": "obligation", "patterns": ["按实结算"]}]
    gone = _q("新版", 1, [_row(1, "水电改造", "c5", spec="按图施工")])
    still = _q("新版", 1, [_row(1, "水电改造", "c5", spec="按实结算，多退少补")])
    o1 = check(_inq(ob), gone, {"risks": [], "recon": {}})
    o2 = check(_inq(ob), still, {"risks": [], "recon": {}})
    case("条款已删除 → 已落实", o1["items"][0]["status"] == "resolved")
    case("条款仍出现 → 未落实", o2["items"][0]["status"] == "open")

    # 6) 算术：新版该行仍错 → 未落实
    ar = [{"no": 1, "subject": "第 3 行", "kind": "arithmetic", "line": "3"}]
    a1 = check(_inq(ar), _q("新版", 1, []),
               {"risks": [{"rule": "R8_arithmetic", "line": "3", "amount": 5}], "recon": {}})
    a2 = check(_inq(ar), _q("新版", 1, []), {"risks": [], "recon": {}})
    case("该行仍错 → 未落实", a1["items"][0]["status"] == "open")
    case("该行已改 → 已落实", a2["items"][0]["status"] == "resolved")

    # 7) 新增问题必须单列：补了闭水试验，但多了一条漏项，不能被 100% 掩盖
    before = {"missing": [{"cid": "c1", "concept": "闭水试验"}], "duplicates": [], "risks": []}
    after = {"missing": [{"cid": "c7", "concept": "成品保护"}], "duplicates": [], "risks": []}
    r7 = check(_inq(it), q_done, after, before_res=before)
    case("新版新增漏项被单列",
         any(x["kind"] == "missing" for x in r7["new_findings"]),
         str(r7["new_findings"]))
    # 8) 旧版就有的问题不能算「新增」
    r8 = check(_inq(it), q_done, before, before_res=before)
    case("旧版已有的问题不算新增", not r8["new_findings"], str(r8["new_findings"]))

    # 9) 落实率只按「能判的」算，unknown 不掺进分母
    mixed_inq = _inq(it + [{"no": 2, "subject": "旧条目", "kind": None}])
    r9 = check(mixed_inq, q_done, {"risks": [], "recon": {}})
    case("unknown 不进落实率分母",
         r9["summary"]["unknown"] == 1 and r9["summary"]["rate"] == 1.0,
         str(r9["summary"]))

    # 10) 空问询单不许冒出「100%」
    empty = check(_inq([]), q_done, {"risks": [], "recon": {}})
    case("没有问询时不报落实率", empty["summary"]["rate"] is None, str(empty["summary"]))

    # 11) 网页版与 markdown 同源：同一份结果，两边都要有这条状态
    html = to_html(r1)
    case("HTML 含落实率与状态徽标",
         "落实率" in html and "已落实" in html and "badge resolved" in html)
    case("HTML 行数 = 条目数 + 表头",
         html.count("<tr>") == r1["summary"]["total"] + 1, str(html.count("<tr>")))
    # 12) 报价单里的项目名会原样进 HTML——必须转义，否则一张单据就能注入脚本
    xss = check(_inq([{"no": 1, "subject": "<script>alert(1)</script>",
                       "kind": "missing", "cid": "c1"}]), q_done, {"risks": [], "recon": {}})
    case("HTML 转义用户文本", "<script>" not in to_html(xss))

    failed = [r for r in results if r["status"] != "pass"]
    return {"self_test": "PASS" if not failed else "FAIL",
            "checks": len(results), "passed": len(results) - len(failed),
            "results": results,
            "violations": [r["name"] for r in failed]}


def main(argv=None):
    import argparse
    import json
    ap = argparse.ArgumentParser(description="问询闭环核验自测")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if not a.self_test:
        ap.error("目前只支持 --self-test；核对请用 scripts/check_followup.py")
    out = self_test()
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        for r in out["results"]:
            print("  %s %s%s" % ("✓" if r["status"] == "pass" else "✗", r["name"],
                                 ("  ← " + r["detail"]) if r["status"] != "pass" else ""))
        print("%s 问询闭环双向断言 %d/%d" % (out["self_test"], out["passed"], out["checks"]))
    return 0 if out["self_test"] == "PASS" else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
