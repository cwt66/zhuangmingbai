# -*- coding: utf-8 -*-
"""把审计结论变成一份「能直接发出去」的问询单。

## 为什么这是产品问题而不是排版问题

审计报告给的是「结论」：漏了 8 项、重了 1 项、差 11,711 元。
但业主真正要做的动作是**去问装修公司**——而绝大多数人拿到结论后不知道第一句该说什么，
于是「审计」就停在了一份没人看的报告里。

问询单把每条结论翻译成一句可以直接复制粘贴的问话，并说明「希望对方怎么答复」。
这是从「我知道你有问题」到「我拿着这个去谈」的那一步。

## 三条纪律

1. **只问事实，不指控**：写「这一项在第 12 行未列，请确认是否包含」，不写「你们故意漏项」；
2. **每条都带依据**：行号或模板项，对方可以核对；
3. **要求书面答复**：口头承诺等于没有，所以每条都写明「请在报价单/合同中书面补充」。
"""
from datetime import datetime

# 不同问题类型对应的「怎么问」模板。措辞刻意中性——目的是拿到答案，不是赢一场吵架。
ASK = {
    # 漏项分两种问法：能估价的问「补多少钱」，不能估价的（工艺项）问「做不做、留什么记录」。
    # 这个区分来自真实数据：闭水试验/打压试验在 4 份单据里从未单列计价，问价是问不出结果的。
    "missing_priced": "报价单中未见此项。请确认：①是否已包含在其他项目中？如是请注明是哪一项；"
                      "②如未包含，请补充单价与工程量。",
    "missing_process": "报价单中未见此项。这类工序通常不单独收费但必须施工，请确认："
                       "①是否包含在报价内？②施工时能否提供过程记录（照片/签字）作为验收依据？",
    "duplicate": "本项目出现多行计价（行 {lines}），请说明各行分别对应什么工作内容；"
                 "如为同一工作，请合并计价。",
    "arithmetic": "第 {line} 行合价与「数量×单价」不一致（差 {diff} 元），请核对后更正。",
    "recon": "报价单明细合计（{detail}）加取费（{fee}）为 {calc}，与印刷合计 {grand} 相差 {diff} 元，"
             "请说明差额来源。",
    "vague": "该行含「{pattern}」表述，金额未锁定。请写明单价、计量方式与上限。",
}


def _fmt(x):
    try:
        return "{:,.2f}".format(float(x))
    except (TypeError, ValueError):
        return "—"


def build(quote, res):
    """返回 {meta, sections:[{title, intro, items:[{no, subject, ask, basis}]}]}"""
    sections = []

    miss = sorted(res.get("missing") or [],
                  key=lambda m: (0 if m.get("ref_amount") else 1, -(m.get("ref_amount") or 0)))
    if miss:
        items = []
        for i, m in enumerate(miss, 1):
            tier = "建议复核" if m.get("tier") != "auto" else "确定漏项"
            basis = "对照标准工序模板；概念对齐与文本证据两路均未命中"
            if m.get("ref_amount"):
                basis += "；参照金额 %s 元（%s）" % (
                    _fmt(m["ref_amount"]),
                    (m.get("ref_source") or [{}])[0].get("quote", "参照"))
            ask = ASK["missing_priced"] if m.get("ref_amount") else ASK["missing_process"]
            items.append({"no": i, "subject": "%s（%s）" % (m["concept"], tier),
                          "ask": ask, "basis": basis})
        sections.append({
            "title": "一、请补齐或说明的工序（%d 项）" % len(miss),
            "intro": "以下工序是同类报价的常规必做项，本单未见。请逐项答复是否包含。",
            "items": items})

    dups = res.get("duplicates") or []
    if dups:
        items = []
        for i, d in enumerate(dups, 1):
            items.append({"no": i, "subject": "%s（涉及 %s 元）" % (d["concept"], _fmt(d.get("amount"))),
                          "ask": ASK["duplicate"].format(lines="、".join(d.get("lines") or [])),
                          "basis": (d.get("evidence") or {}).get("basis", "")})
        sections.append({"title": "二、请说明的重复计价（%d 项）" % len(dups),
                         "intro": "同一工序出现多行。可能是分部位计价，也可能是重复收费，请说明。",
                         "items": items})

    arith = [x for x in (res.get("risks") or []) if x.get("rule") == "R8_arithmetic"]
    recon = [x for x in (res.get("risks") or []) if x.get("rule") == "R13_doc_recon"]
    if arith or recon:
        items = []
        n = 0
        for x in arith:
            n += 1
            diff = (x.get("amount") or 0)
            items.append({"no": n, "subject": "第 %s 行算术不符" % (x.get("line") or "—"),
                          "ask": ASK["arithmetic"].format(line=x.get("line") or "—", diff=_fmt(diff)),
                          "basis": (x.get("evidence") or {}).get("basis", "")})
        rec = (res.get("recon") or {})
        if rec.get("diff") is not None:
            n += 1
            items.append({
                "no": n, "subject": "整单合计对不上（差 %s 元）" % _fmt(rec["diff"]),
                "ask": ASK["recon"].format(
                    detail=_fmt(rec.get("detail_sum")), fee=_fmt(rec.get("fee_sum")),
                    calc=_fmt((rec.get("detail_sum") or 0) + (rec.get("fee_sum") or 0)),
                    grand=_fmt((rec.get("grand") or {}).get("amount")), diff=_fmt(rec["diff"])),
                "basis": "印刷合计 vs 逐行明细之和 + 派生取费"})
        sections.append({"title": "三、请核对的算术与合计（%d 项）" % len(items),
                         "intro": "这些是可以用加减法直接验证的差异，请核对后给出更正版。",
                         "items": items})

    obs = res.get("obligations") or []
    if obs:
        items = []
        for i, o in enumerate(obs, 1):
            items.append({"no": i, "subject": o["title"],
                          "ask": o["confirm"] + "（请书面答复并作为合同附件）",
                          "basis": "原文命中：%s；涉及金额 %s 元" % (
                              "、".join(o.get("patterns") or []), _fmt(o.get("amount")))})
        sections.append({"title": "四、请书面确认的开口条款（%d 类）" % len(obs),
                         "intro": "这些条款意味着金额尚未锁定。请把答案写进合同，不要只口头承诺。",
                         "items": items})

    evidence = [o for o in obs if o.get("evidence")]
    meta = {
        "title": "报价问询单",
        "generated": datetime.now().strftime("%Y-%m-%d"),
        "quote_label": quote.get("label") or "",
        "declared_total": quote.get("total"),
        "printed_total": ((res.get("recon") or {}).get("grand") or {}).get("amount"),
        "pricing": (res.get("pricing") or {}).get("label"),
        "n_items": sum(len(s["items"]) for s in sections),
        "evidence": [{"title": e["title"], "evidence": e["evidence"]} for e in evidence],
    }
    return {"meta": meta, "sections": sections}


def to_markdown(inq):
    m = inq["meta"]
    L = ["# %s" % m["title"], "",
         "> 针对报价单：**%s**｜明细合计 %s 元%s｜生成日期 %s" % (
             m["quote_label"], _fmt(m["declared_total"]),
             ("｜印刷合计 %s 元" % _fmt(m["printed_total"])) if m["printed_total"] else "",
             m["generated"]),
         "> 计价体系：%s" % (m["pricing"] or "—"), "",
         "本单共需澄清 **%d** 处。以下是逐条问询，可直接发给报价方；每条都附了核对依据，" % m["n_items"],
         "对方可以逐项核对，也便于双方在签约前把口径定下来。", ""]
    for s in inq["sections"]:
        L.append("## %s" % s["title"])
        L.append("")
        L.append("> %s" % s["intro"])
        L.append("")
        for it in s["items"]:
            L.append("**%d. %s**" % (it["no"], it["subject"]))
            L.append("")
            L.append("- 问：%s" % it["ask"])
            if it["basis"]:
                L.append("- 依据：%s" % it["basis"])
            L.append("")
    if m["evidence"]:
        L.append("## 五、请随合同提供的验收凭证")
        L.append("")
        for e in m["evidence"]:
            L.append("- **%s**：%s" % (e["title"], e["evidence"]))
        L.append("")
    L.append("---")
    L.append("")
    L.append("> 本问询单由装明白根据报价单原文生成，只做计费与口径核对，**不评价价格高低、不评价施工质量**。")
    L.append("> 所有条目均可回到原表行号核对；如对某条有异议，请以原文为准并说明。")
    return "\n".join(L) + "\n"


def to_html(inq):
    """打印级 A4 版式，与审计报告同一视觉体系（IKB × 纸白 × 墨黑）。"""
    m = inq["meta"]
    css = """
:root{--ink:#0A0A0A;--paper:#FAFAF8;--ikb:#002FA7;--grey:#6B7280;--hair:#E4E2DC;--faint:#8A877F}
*{box-sizing:border-box}
body{font-family:-apple-system,"PingFang SC","Helvetica Neue",sans-serif;background:var(--paper);
 color:var(--ink);margin:0;line-height:1.8;font-size:14px}
.masthead{background:var(--ikb);color:#fff;padding:10px 48px;display:flex;justify-content:space-between}
.masthead .brand{font-weight:700;letter-spacing:.12em;font-size:13px}
.wrap{max-width:900px;margin:0 auto;padding:0 48px 56px}
h1{font-size:30px;margin:36px 0 8px;letter-spacing:-.01em}
.sub{color:var(--faint);font-size:13px}
h2{font-size:15px;margin:30px 0 8px;letter-spacing:.04em}
h2 .idx{color:var(--ikb);margin-right:8px}
.intro{color:var(--grey);font-size:13px;border-left:3px solid var(--hair);padding-left:12px;margin:0 0 14px}
.item{border-top:1px solid var(--hair);padding:12px 0}
.item .t{font-weight:700}
.item .q{margin:6px 0 0}
.item .b{color:var(--grey);font-size:12.5px;margin-top:4px}
footer{margin-top:36px;border-top:1px solid var(--hair);padding-top:12px;color:var(--faint);font-size:12px}
@media print{body{background:#fff}.wrap{padding:0}}
"""
    parts = [
        "<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">",
        "<title>%s · %s</title><style>%s</style></head><body>" % (m["title"], m["quote_label"], css),
        '<div class="masthead"><div class="brand">装明白 · 报价问询单</div>'
        '<div style="font-size:12px">%s</div></div>' % m["generated"],
        '<div class="wrap">',
        "<h1>%s</h1>" % m["title"],
        '<div class="sub">针对：%s｜明细合计 %s 元%s｜计价体系：%s</div>' % (
            m["quote_label"], _fmt(m["declared_total"]),
            ("｜印刷合计 %s 元" % _fmt(m["printed_total"])) if m["printed_total"] else "",
            m["pricing"] or "—"),
        "<p>本单共需澄清 <b>%d</b> 处。以下逐条问询可直接发给报价方，每条附核对依据。</p>" % m["n_items"],
    ]
    for si, s in enumerate(inq["sections"], start=1):
        parts.append('<h2><span class="idx">%02d</span>%s</h2>' % (si, s["title"]))
        parts.append('<div class="intro">%s</div>' % s["intro"])
        for it in s["items"]:
            parts.append('<div class="item"><div class="t">%d. %s</div>'
                         '<div class="q">%s</div>%s</div>' % (
                             it["no"], it["subject"], it["ask"],
                             ('<div class="b">依据：%s</div>' % it["basis"]) if it["basis"] else ""))
    if m["evidence"]:
        parts.append('<h2><span class="idx">%02d</span>请随合同提供的验收凭证</h2>'
                     % (len(inq["sections"]) + 1))
        for e in m["evidence"]:
            parts.append('<div class="item"><div class="t">%s</div>'
                         '<div class="q">%s</div></div>' % (e["title"], e["evidence"]))
    parts.append("<footer>本问询单由装明白根据报价单原文生成，只做计费与口径核对，"
                 "不评价价格高低、不评价施工质量。所有条目可回到原表行号核对。</footer>")
    parts.append("</div></body></html>")
    return "".join(parts)
