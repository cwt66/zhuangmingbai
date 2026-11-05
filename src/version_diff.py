# -*- coding: utf-8 -*-
"""两版报价对比：回答「补漏之后，钱到底多了没有」。

为什么要做这一层
----------------
审计回答的是「这一份报价有没有问题」；业主真正在签约现场问的是另一句话：
「你上次报 15 万，这次怎么 17 万，多在哪？」

这两个问题不是一回事：
- 漏项、重复、算术错，是**同一份**报价内部的判断；
- 涨价、减项、换材料，是**两份**报价之间的差异，必须逐行配对才能说清。

GitHub 上 `KanadeK/boq-diff`（本地优先的 BOQ 版本对比）做的就是这件事，
但它只做 Excel 表格 diff；我们的差别是：**先按本体对齐到工序概念，再比数量与单价**，
所以「拆墙」和「墙体拆除」能被认成同一项，而不是两行陌生文本。

口径（必须写进材料）
--------------------
1. 只比**同一项目、同一套计价口径**的两版；不做跨公司比价（那是「贵不贵」，我们不做）。
2. 配对键：本体概念 id（cid）优先，没有 cid 时退回规范化名称；同一概念多行时按行序配对。
3. **单位变了不比金额**：m → m2 这种，单列到 `unit_mismatch`，不参与净变化计算。
4. 输出的是「账面差额」，不是「应该赔多少」；不判断哪一版更合理。

用法：
    from version_diff import diff
    d = diff(q1, q2)          # q1/q2 来自 pipeline.build_line_index
"""
import re
import unicodedata


def _norm_name(s):
    """名称规范化：全半角、空白、常见连接符统一，便于没有 cid 时配对。"""
    s = unicodedata.normalize("NFKC", str(s or ""))
    s = re.sub(r"[\s\u3000]+", "", s)
    s = re.sub(r"[·•\-—_/、,，。.:：;；()（）\[\]【】]", "", s)
    return s.lower()


def _key(row):
    cid = row.get("cid")
    if cid:
        return "cid:" + str(cid)
    return "name:" + _norm_name(row.get("name"))


def _amount(row):
    try:
        return float(row.get("amount") or 0)
    except (TypeError, ValueError):
        return 0.0


def _num(row, field):
    try:
        return float(row.get(field) or 0)
    except (TypeError, ValueError):
        return 0.0


def _brief(row):
    return {
        "no": str(row.get("no") or ""),
        "name": row.get("name") or "",
        "spec": row.get("spec") or "",
        "cid": row.get("cid"),
        "unit": row.get("unit") or "",
        "qty": _num(row, "qty"),
        "price": _num(row, "price"),
        "amount": _amount(row),
    }


def _group(rows):
    """按配对键分组，保持原表顺序（同一概念多行时靠行序配对）。"""
    g = {}
    for r in rows:
        g.setdefault(_key(r), []).append(r)
    return g


def diff(q1, q2):
    """对比两版报价。返回可 JSON 序列化的 dict，字段含义见模块文档。"""
    rows1, rows2 = q1.get("rows") or [], q2.get("rows") or []
    g1, g2 = _group(rows1), _group(rows2)

    added, removed, changed, unit_mismatch = [], [], [], []
    unchanged = 0

    for key in list(g1.keys()) + [k for k in g2 if k not in g1]:
        a, b = g1.get(key, []), g2.get(key, [])
        for i in range(min(len(a), len(b))):
            r1, r2 = a[i], b[i]
            u1, u2 = (r1.get("unit") or "").strip(), (r2.get("unit") or "").strip()
            d_amt = _amount(r2) - _amount(r1)
            d_qty = _num(r2, "qty") - _num(r1, "qty")
            d_price = _num(r2, "price") - _num(r1, "price")
            if u1 and u2 and _norm_name(u1) != _norm_name(u2):
                unit_mismatch.append({
                    "name": r2.get("name") or r1.get("name") or "",
                    "cid": r2.get("cid") or r1.get("cid"),
                    "unit1": u1, "unit2": u2,
                    "qty1": _num(r1, "qty"), "qty2": _num(r2, "qty"),
                    "amount1": _amount(r1), "amount2": _amount(r2),
                    "note": "单位不一致，金额不可直接相减",
                })
                continue
            if abs(d_qty) < 1e-9 and abs(d_price) < 1e-9:
                unchanged += 1
                continue
            changed.append({
                "cid": r2.get("cid") or r1.get("cid"),
                "name": r2.get("name") or r1.get("name") or "",
                "unit": u2 or u1,
                "qty1": _num(r1, "qty"), "qty2": _num(r2, "qty"),
                "price1": _num(r1, "price"), "price2": _num(r2, "price"),
                "amount1": _amount(r1), "amount2": _amount(r2),
                "delta_qty": round(d_qty, 4),
                "delta_price": round(d_price, 4),
                "delta_amount": round(d_amt, 2),
                "reason": _reason(d_qty, d_price),
            })
        for r in b[len(a):]:
            added.append(_brief(r))
        for r in a[len(b):]:
            removed.append(_brief(r))

    total1 = float(q1.get("total") or sum(_amount(r) for r in rows1))
    total2 = float(q2.get("total") or sum(_amount(r) for r in rows2))
    added_amount = sum(x["amount"] for x in added)
    removed_amount = sum(x["amount"] for x in removed)
    changed_delta = sum(x["delta_amount"] for x in changed)
    explained = added_amount - removed_amount + changed_delta
    base = total1 or 1.0

    return {
        "v1": {"label": q1.get("label") or "v1", "file": q1.get("file") or "",
               "rows": len(rows1), "total": round(total1, 2)},
        "v2": {"label": q2.get("label") or "v2", "file": q2.get("file") or "",
               "rows": len(rows2), "total": round(total2, 2)},
        "added": sorted(added, key=lambda x: -x["amount"]),
        "removed": sorted(removed, key=lambda x: -x["amount"]),
        "changed": sorted(changed, key=lambda x: -abs(x["delta_amount"])),
        "unit_mismatch": unit_mismatch,
        "summary": {
            "total1": round(total1, 2),
            "total2": round(total2, 2),
            "delta": round(total2 - total1, 2),
            "added_amount": round(added_amount, 2),
            "removed_amount": round(removed_amount, 2),
            "changed_delta": round(changed_delta, 2),
            "explained_delta": round(explained, 2),
            "unexplained": round((total2 - total1) - explained, 2),
            # 净变化率：账面差额 / 原价，正数是变贵
            "net_rate": round((total2 - total1) / base, 4),
            # 变动幅度：增+减+同项调整的绝对值 / 原价，反映「改了多少」，可能 >100%
            "change_rate": round((added_amount + removed_amount
                                  + sum(abs(x["delta_amount"]) for x in changed)) / base, 4),
            "counts": {"added": len(added), "removed": len(removed),
                       "changed": len(changed), "unchanged": unchanged,
                       "unit_mismatch": len(unit_mismatch)},
        },
        "method": {
            "match_by": "本体概念 id 优先，缺失时用规范化名称；同概念多行按行序配对",
            "unit_rule": "单位不一致不并入金额差，单列待人工核对",
            "scope": "仅同一项目、同一计价口径的两版对比；不做跨公司比价",
            "note": "差额是账面差额，不是应赔金额；不判断哪一版更合理",
        },
    }


def _reason(d_qty, d_price):
    parts = []
    if abs(d_qty) > 1e-9:
        parts.append("工程量%s" % ("增加" if d_qty > 0 else "减少"))
    if abs(d_price) > 1e-9:
        parts.append("单价%s" % ("上调" if d_price > 0 else "下调"))
    return "＋".join(parts) or "金额变化"


def to_markdown(d):
    """给 CLI / 报告用的 Markdown 摘要。"""
    s = d["summary"]
    lines = [
        "# 两版报价对比",
        "",
        "| 项 | %s | %s |" % (d["v1"]["label"], d["v2"]["label"]),
        "|---|---:|---:|",
        "| 行项数 | %d | %d |" % (d["v1"]["rows"], d["v2"]["rows"]),
        "| 明细合计 | %s | %s |" % (_money(s["total1"]), _money(s["total2"])),
        "",
        "**账面差额 %s（%+.2f%%）**：增项 %s / 减项 %s / 同项调整 %s。"
        % (_money(s["delta"]), (s["delta"] / (s["total1"] or 1)) * 100,
           _money(s["added_amount"]), _money(s["removed_amount"]),
           _money(s["changed_delta"])),
        "",
        "对账：增 − 减 + 调整 = %s，与账面差额 %s 相差 %s。"
        % (_money(s["explained_delta"]), _money(s["delta"]), _money(s["unexplained"])),
        "",
    ]
    if d["added"]:
        lines += ["## 增项（%s 没有、%s 有）" % (d["v1"]["label"], d["v2"]["label"]), "", "| 行 | 项目 | 单位 | 数量 | 单价 | 金额 |", "|---:|---|---|---:|---:|---:|"]
        for x in d["added"]:
            lines.append("| %s | %s | %s | %g | %s | %s |"
                         % (x["no"], x["name"], x["unit"], x["qty"], _money(x["price"]), _money(x["amount"])))
        lines.append("")
    if d["removed"]:
        lines += ["## 减项（%s 有、%s 没有）" % (d["v1"]["label"], d["v2"]["label"]), "", "| 行 | 项目 | 单位 | 数量 | 单价 | 金额 |", "|---:|---|---|---:|---:|---:|"]
        for x in d["removed"]:
            lines.append("| %s | %s | %s | %g | %s | %s |"
                         % (x["no"], x["name"], x["unit"], x["qty"], _money(x["price"]), _money(x["amount"])))
        lines.append("")
    if d["changed"]:
        lines += ["## 同项变化", "", "| 项目 | 数量 | 单价 | 金额 | 差额 | 原因 |",
                  "|---|---|---|---:|---:|---|"]
        for x in d["changed"]:
            lines.append("| %s | %g → %g | %s → %s | %s → %s | %s | %s |"
                         % (x["name"], x["qty1"], x["qty2"], _money(x["price1"]), _money(x["price2"]),
                            _money(x["amount1"]), _money(x["amount2"]),
                            ("%+.2f" % x["delta_amount"]), x["reason"]))
        lines.append("")
    if d["unit_mismatch"]:
        lines += ["## 单位不一致（不并入金额差）", "", "| 项目 | 单位 | 金额 |", "|---|---|---:|"]
        for x in d["unit_mismatch"]:
            lines.append("| %s | %s → %s | %s → %s |"
                         % (x["name"], x["unit1"], x["unit2"], _money(x["amount1"]), _money(x["amount2"])))
        lines.append("")
    lines += ["---", "", "口径：%s；%s。" % (d["method"]["scope"], d["method"]["unit_rule"])]
    return "\n".join(lines) + "\n"


def _money(x):
    return "{:,.2f}".format(float(x or 0))


def to_html(d, banner="", top_html=""):
    """一页可打印的对比报告（与审计报告同一视觉体系：IKB × 纸白 × 墨黑）。"""
    s = d["summary"]
    c = s["counts"]

    def money(x):
        return "{:,.2f}".format(float(x or 0))

    def diff_cls(x):
        return "up" if x > 0 else ("down" if x < 0 else "")

    def rows_added():
        if not d["added"]:
            return '<tr><td colspan="6" class="empty">无增项</td></tr>'
        out = []
        for x in d["added"]:
            out.append("<tr><td class='num'>%s</td><td>%s</td><td>%s</td><td class='num'>%g</td>"
                       "<td class='num'>%s</td><td class='num'>%s</td></tr>"
                       % (x["no"], _esc(x["name"]), _esc(x["unit"]), x["qty"],
                          money(x["price"]), money(x["amount"])))
        return "".join(out)

    def rows_removed():
        if not d["removed"]:
            return '<tr><td colspan="6" class="empty">无减项</td></tr>'
        out = []
        for x in d["removed"]:
            out.append("<tr><td class='num'>%s</td><td>%s</td><td>%s</td><td class='num'>%g</td>"
                       "<td class='num'>%s</td><td class='num'>%s</td></tr>"
                       % (x["no"], _esc(x["name"]), _esc(x["unit"]), x["qty"],
                          money(x["price"]), money(x["amount"])))
        return "".join(out)

    def rows_changed():
        if not d["changed"]:
            return '<tr><td colspan="6" class="empty">无同项变化</td></tr>'
        out = []
        for x in d["changed"]:
            out.append("<tr><td>%s</td><td class='num'>%g → %g</td><td class='num'>%s → %s</td>"
                       "<td class='num'>%s → %s</td><td class='num %s'>%+.2f</td><td>%s</td></tr>"
                       % (_esc(x["name"]), x["qty1"], x["qty2"], money(x["price1"]), money(x["price2"]),
                          money(x["amount1"]), money(x["amount2"]), diff_cls(x["delta_amount"]),
                          x["delta_amount"], _esc(x["reason"])))
        return "".join(out)

    def rows_unit():
        if not d["unit_mismatch"]:
            return ""
        out = ["<h2>单位不一致（不并入金额差）</h2>",
               "<table><thead><tr><th>项目</th><th>单位</th><th class='num'>金额</th></tr></thead><tbody>"]
        for x in d["unit_mismatch"]:
            out.append("<tr><td>%s</td><td>%s → %s</td><td class='num'>%s → %s</td></tr>"
                       % (_esc(x["name"]), _esc(x["unit1"]), _esc(x["unit2"]),
                          money(x["amount1"]), money(x["amount2"])))
        out.append("</tbody></table>")
        return "".join(out)

    ok = abs(s["unexplained"]) < 0.01
    banner_html = ('<div class="banner">%s</div>' % _esc(banner)) if banner else ""

    return """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>装明白 · 两版报价对比</title>
<style>
:root{--ink:#0A0A0A;--paper:#FAFAF8;--ikb:#002FA7;--grey:#6B7280;--hair:#E4E2DC;--faint:#8A877F;
--up:#B33A2B;--down:#2F7A4F}
*{box-sizing:border-box}
body{font-family:-apple-system,"PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;
background:var(--paper);color:var(--ink);margin:0;line-height:1.7;font-size:14px}
.masthead{background:var(--ikb);color:#fff;padding:10px 48px;display:flex;justify-content:space-between;
font-size:12px;letter-spacing:.12em}
.wrap{max-width:980px;margin:0 auto;padding:0 48px 64px}
h1{font-size:31px;margin:34px 0 6px;letter-spacing:-.01em;font-weight:600}
h2{font-size:15px;margin:34px 0 10px;padding-bottom:6px;border-bottom:1px solid var(--hair);font-weight:600}
.sub{color:var(--faint);font-size:13px}
.banner{margin:18px 0 0;padding:11px 14px;border-left:3px solid var(--ikb);background:#F1F3F9;
color:#33405C;font-size:13px}
.stats{display:flex;gap:26px;margin:24px 0 4px;flex-wrap:wrap}
.stat{min-width:120px}
.stat .k{font-size:11px;color:var(--faint);letter-spacing:.06em}
.stat .v{font-size:30px;font-variant-numeric:tabular-nums;letter-spacing:-.02em}
.stat .v.up{color:var(--up)} .stat .v.down{color:var(--down)}
.recon{margin-top:18px;padding:12px 14px;background:#fff;border:1px solid var(--hair);font-size:13px}
.recon b{font-variant-numeric:tabular-nums}
.tag{display:inline-block;padding:1px 8px;border:1px solid var(--hair);border-radius:10px;
font-size:11px;color:var(--grey);margin-left:8px}
table{width:100%%;border-collapse:collapse;margin:8px 0 4px;font-size:13px}
th{text-align:left;font-weight:500;color:var(--grey);font-size:11.5px;letter-spacing:.04em;
padding:6px 8px;border-bottom:1px solid var(--hair)}
td{padding:7px 8px;border-bottom:1px solid #F0EEE9;vertical-align:top}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
td.up{color:var(--up)} td.down{color:var(--down)}
td.empty{color:var(--faint);text-align:center;padding:14px}
.foot{margin-top:36px;padding-top:14px;border-top:1px solid var(--hair);color:var(--faint);font-size:12px}
.foot code{font-family:ui-monospace,Menlo,monospace;background:#F2F1EC;padding:1px 5px}
@media print{.masthead{-webkit-print-color-adjust:exact;print-color-adjust:exact}
.wrap{max-width:none;padding:0 24px}body{font-size:12px}}
</style></head><body>
<div class="masthead"><span>装明白 · 可计算审计</span><span>两版对比</span></div>
<div class="wrap">
<h1>从「%(l1)s」到「%(l2)s」，多在哪</h1>
<div class="sub">%(f1)s（%(r1)d 行） → %(f2)s（%(r2)d 行）｜明细合计 %(t1)s → %(t2)s</div>
%(banner)s
%(top)s
<div class="stats">
  <div class="stat"><div class="k">账面差额</div><div class="v %(dcls)s">%(delta)s</div></div>
  <div class="stat"><div class="k">净变化率</div><div class="v %(dcls)s">%(net)s</div></div>
  <div class="stat"><div class="k">增项</div><div class="v">%(add)s</div></div>
  <div class="stat"><div class="k">减项</div><div class="v">%(rem)s</div></div>
  <div class="stat"><div class="k">同项调整</div><div class="v">%(chg)s</div></div>
</div>
<div class="recon">
对账：增项 <b>%(add)s</b> − 减项 <b>%(rem)s</b> + 同项调整 <b>%(chg)s</b> = <b>%(expl)s</b>，
与账面差额 <b>%(delta)s</b> %(oktxt)s
<span class="tag">增 %(cn_add)d · 减 %(cn_rem)d · 改 %(cn_chg)d · 未变 %(cn_same)d</span>
</div>

<h2>增项（原版没有、新版有）</h2>
<table><thead><tr><th>行</th><th>项目</th><th>单位</th><th class="num">数量</th>
<th class="num">单价</th><th class="num">金额</th></tr></thead><tbody>%(rows_add)s</tbody></table>

<h2>减项（原版有、新版没有）</h2>
<table><thead><tr><th>行</th><th>项目</th><th>单位</th><th class="num">数量</th>
<th class="num">单价</th><th class="num">金额</th></tr></thead><tbody>%(rows_rem)s</tbody></table>

<h2>同项变化</h2>
<table><thead><tr><th>项目</th><th class="num">数量</th><th class="num">单价</th>
<th class="num">金额</th><th class="num">差额</th><th>原因</th></tr></thead><tbody>%(rows_chg)s</tbody></table>
%(rows_unit)s

<div class="foot">
口径：%(scope)s。%(unit_rule)s。配对方式：%(match_by)s。<br>
差额是<b>账面差额</b>，不是应赔金额；本页不判断哪一版更合理，也不做跨公司比价。
生成命令：<code>python3 scripts/diff_quotes.py a.csv b.csv</code>
</div>
</div></body></html>""" % {
        "l1": _esc(d["v1"]["label"]), "l2": _esc(d["v2"]["label"]),
        "f1": _esc(d["v1"]["file"]), "f2": _esc(d["v2"]["file"]),
        "r1": d["v1"]["rows"], "r2": d["v2"]["rows"],
        "t1": money(s["total1"]), "t2": money(s["total2"]),
        "banner": banner_html, "top": top_html,
        "dcls": diff_cls(s["delta"]),
        "delta": ("%+.2f" % s["delta"]), "net": ("%+.2f%%" % (s["net_rate"] * 100)),
        "add": money(s["added_amount"]), "rem": money(s["removed_amount"]),
        "chg": money(s["changed_delta"]),
        "expl": money(s["explained_delta"]),
        "oktxt": "一致（差 <b>0.00</b>）" if ok else
                 ("<b style='color:#B33A2B'>不一致：差 %s</b>" % money(s["unexplained"])),
        "cn_add": c["added"], "cn_rem": c["removed"], "cn_chg": c["changed"], "cn_same": c["unchanged"],
        "rows_add": rows_added(), "rows_rem": rows_removed(), "rows_chg": rows_changed(),
        "rows_unit": rows_unit(),
        "scope": _esc(d["method"]["scope"]), "unit_rule": _esc(d["method"]["unit_rule"]),
        "match_by": _esc(d["method"]["match_by"]),
    }


def _esc(s):
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
