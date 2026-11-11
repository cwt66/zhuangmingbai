# -*- coding: utf-8 -*-
"""三家（或 2–4 家）报价的「同一批」对照表。

## 为什么要单独做一张表

问询单解决「我该问这一家什么」，闭环球解决「这一家改没改」。
但业主真正在做的动作是**比价**：手里三家，先要知道「谁缺了什么、谁多算了什么」。

单独看三份问询单，会出现一个假象——每家都"有问题"，看不出**问题是不是同一处**。
把三份并成一张表，才看得见：

- 同一个漏项，只有 A 报了、B/C 都没报 → 大概率是行业常规做法，不是 A 多事
- 同一条开口条款，三家都写了 → 是行业惯例，问了也没用，得写进合同
- 同一个工序，A 按㎡、B 按米 → 比价前必须换算，不能直接比单价

## 三条纪律

1. **只列事实，不排名次**：不写「A 最规范」。缺项多不等于贵，也不等于坑。
2. **每格可回原表**：写「有（第 12 行）」/「缺」，不写「看起来还行」。
3. **口径不同单列**，不和漏项混在一起——前者是无法比较，后者是缺东西，性质不同。
"""

STATUS_ABSENT = "缺"
COLUMN_ORDER = ("missing", "duplicate", "unit", "obligation")


def _money(x):
    try:
        return "{:,.0f}".format(float(x))
    except (TypeError, ValueError):
        return "—"


def _rows_by_cid(quote):
    out = {}
    for r in (quote.get("rows") or []):
        cid = r.get("cid")
        if cid:
            out.setdefault(cid, []).append(r)
    return out


def _lines(rows):
    return "、".join(str(r.get("no")) for r in rows)


def compare(quotes, res):
    """返回 {meta, sections:[{kind,title,intro,rows:[{subject,cells:{label:...}}]}], summary}"""
    labels = [q.get("label") or "" for q in quotes]
    by_cid = {lb: _rows_by_cid(q) for lb, q in zip(labels, quotes)}

    def cell_missing(lb, cid):
        rs = by_cid[lb].get(cid) or []
        return ("有（第 %s 行）" % _lines(rs)) if rs else STATUS_ABSENT

    # 漏项：所有单据的并集
    miss = {}
    for lb in labels:
        for m in res.get(lb, {}).get("missing") or []:
            key = m.get("cid") or m.get("concept")
            if not key:
                continue
            row = miss.setdefault(key, {"kind": "missing", "cid": m.get("cid"),
                                        "subject": m.get("concept") or m.get("name"),
                                        "ref_amount": 0.0, "cells": {}})
            row["ref_amount"] = max(row["ref_amount"], float(m.get("ref_amount") or 0))
    for key, row in miss.items():
        for lb in labels:
            row["cells"][lb] = cell_missing(lb, row["cid"])
    miss_rows = sorted(miss.values(),
                       key=lambda r: (-sum(1 for v in r["cells"].values() if v == STATUS_ABSENT),
                                      -r["ref_amount"]))

    # 重复计价
    dup = {}
    for lb in labels:
        for d in res.get(lb, {}).get("duplicates") or []:
            key = d.get("cid") or d.get("concept")
            if not key:
                continue
            row = dup.setdefault(key, {"kind": "duplicate", "cid": d.get("cid"),
                                       "subject": d.get("concept"), "cells": {}})
            row["cells"][lb] = "重复 %d 行（%s 元）" % (
                len(d.get("lines") or []), _money(d.get("amount")))
    for key, row in dup.items():
        for lb in labels:
            row["cells"].setdefault(lb, "—")
    dup_rows = sorted(dup.values(),
                      key=lambda r: -sum(1 for v in r["cells"].values() if v != "—"))

    # 跨单口径：只有存在对照时才成立，所以整节可能为空
    unit = {}
    for x in res.get("_cross_unit") or []:
        cid = x.get("cid")
        if not cid:
            continue
        by = x.get("by_quote") or {}
        row = {"kind": "unit", "cid": cid, "subject": x.get("concept"),
               "dims": list(x.get("dims") or []), "cells": {}}
        for lb in labels:
            row["cells"][lb] = "按 %s" % "、".join(by.get(lb) or []) if lb in by else "—"
        unit[row.get("concept") or cid] = row
    unit_rows = sorted(unit.values(), key=lambda r: r["subject"] or "")

    # 开口条款：按标题归并（同一类条款在不同单据里措辞不同，标题是模板化的）
    ob = {}
    for lb in labels:
        for o in res.get(lb, {}).get("obligations") or []:
            title = o.get("title") or "、".join(o.get("patterns") or [])
            row = ob.setdefault(title, {"kind": "obligation", "subject": title, "cells": {}})
            row["cells"][lb] = "含「%s」" % "、".join(o.get("patterns") or [])
    for title, row in ob.items():
        for lb in labels:
            row["cells"].setdefault(lb, "—")
    ob_rows = sorted(ob.values(),
                     key=lambda r: -sum(1 for v in r["cells"].values() if v != "—"))

    sections = []
    if miss_rows:
        sections.append({
            "kind": "missing", "title": "漏项对照（%d 个工序）" % len(miss_rows),
            "intro": "同一工序在这一批里谁列了、谁没列。缺项多不等于贵，"
                     "只说明「同样一份报价，别人算了这一项」。",
            "rows": miss_rows})
    if dup_rows:
        sections.append({
            "kind": "duplicate", "title": "重复计价对照（%d 项）" % len(dup_rows),
            "intro": "同一工序在同一份单据里出现多行。可能是分部位计价，也可能是重复收费。",
            "rows": dup_rows})
    if unit_rows:
        sections.append({
            "kind": "unit", "title": "计量口径对照（%d 项）" % len(unit_rows),
            "intro": "同一工序各家用了不同量纲（面积 / 长度 / 个数）。这不是谁错了，"
                     "是**单价不可直接比**，比价前必须先换算。",
            "rows": unit_rows})
    if ob_rows:
        sections.append({
            "kind": "obligation", "title": "开口条款对照（%d 类）" % len(ob_rows),
            "intro": "金额尚未锁定的表述。三家都写的，通常是行业惯例，"
                     "不是某一家在挖坑——但都要写进合同。",
            "rows": ob_rows})

    summary = []
    for lb, q in zip(labels, quotes):
        r = res.get(lb) or {}
        summary.append({
            "label": lb, "total": q.get("total"),
            "missing": len(r.get("missing") or []),
            "duplicates": len(r.get("duplicates") or []),
            "effective": r.get("effective"),
        })

    return {"meta": {"labels": labels, "count": len(labels)},
            "sections": sections, "summary": summary,
            "limits": "只对照「同一批报价里的同一处」，不做跨项目比较；"
                      "缺项数、重复数都不是评分，不排名次。"}


def to_markdown(c):
    labels = c["meta"]["labels"]
    head = "| 工序 / 条款 | " + " | ".join(labels) + " |"
    sep = "|---|" + "---|" * len(labels)
    L = ["# 同一批报价对照", "",
         "| 公司 | 报价总价 | 漏项 | 重复计价 | 真实成本（补漏−剔重） |",
         "|---|---:|---:|---:|---:|"]
    for s in c["summary"]:
        L.append("| %s | %s | %d | %d | %s |" % (
            s["label"], _money(s["total"]), s["missing"], s["duplicates"],
            _money(s["effective"])))
    L.append("")
    for sec in c["sections"]:
        L.append("## %s" % sec["title"])
        L.append("")
        L.append("> %s" % sec["intro"])
        L.append("")
        L.append(head)
        L.append(sep)
        for row in sec["rows"]:
            L.append("| %s | %s |" % (
                row["subject"], " | ".join(row["cells"].get(lb, "—") for lb in labels)))
        L.append("")
    L.append("---")
    L.append("")
    L.append("> %s" % c["limits"])
    return "\n".join(L) + "\n"


def _esc(s):
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def to_html(c):
    labels = c["meta"]["labels"]
    css = """
:root{--ink:#0A0A0A;--paper:#FAFAF8;--ikb:#002FA7;--grey:#6B7280;--hair:#E4E2DC;--faint:#8A877F}
*{box-sizing:border-box}
body{font-family:-apple-system,"PingFang SC","Helvetica Neue",sans-serif;background:var(--paper);
 color:var(--ink);margin:0;line-height:1.75;font-size:14px}
.masthead{background:var(--ikb);color:#fff;padding:10px 44px;display:flex;justify-content:space-between}
.masthead .brand{font-weight:700;letter-spacing:.12em;font-size:13px}
.wrap{max-width:1040px;margin:0 auto;padding:0 44px 56px}
h1{font-size:30px;margin:34px 0 6px;letter-spacing:-.01em}
.sub{color:var(--faint);font-size:13px}
h2{font-size:15px;margin:30px 0 6px;letter-spacing:.04em}
.intro{color:var(--grey);font-size:13px;border-left:3px solid var(--hair);padding-left:12px;margin:0 0 12px}
table{width:100%;border-collapse:collapse;margin:10px 0 6px;font-size:13px}
th{text-align:left;color:var(--grey);font-weight:600;border-bottom:1px solid var(--ink);
 padding:8px 8px;font-size:12px;letter-spacing:.03em}
td{border-bottom:1px solid var(--hair);padding:8px;vertical-align:top}
td.zero{color:#B33A2B}
td.ok{color:#0F5132}
.sum td:first-child{font-weight:600}
footer{margin-top:34px;border-top:1px solid var(--hair);padding-top:12px;color:var(--faint);font-size:12px}
@media print{body{background:#fff}.wrap{padding:0}.masthead{background:#fff;color:var(--ink);
 border-bottom:2px solid var(--ikb)}}
"""
    parts = ['<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">',
             "<title>同一批报价对照</title><style>%s</style></head><body>" % css,
             '<div class="masthead"><div class="brand">装明白 · 同一批报价对照</div>'
             '<div style="font-size:12px">%d 家 · 只列事实，不排名次</div></div>'
             % c["meta"]["count"],
             '<div class="wrap">', "<h1>同一批报价对照</h1>",
             '<div class="sub">同一批、同一模板下的横向对照；单价在不同量纲之间不可直接比。</div>',
             '<table class="sum"><thead><tr><th>公司</th><th>报价总价</th><th>漏项</th>'
             "<th>重复计价</th><th>真实成本（补漏−剔重）</th></tr></thead><tbody>"]
    for s in c["summary"]:
        parts.append("<tr><td>%s</td><td>%s</td><td>%d</td><td>%d</td><td>%s</td></tr>" % (
            _esc(s["label"]), _money(s["total"]), s["missing"], s["duplicates"],
            _money(s["effective"])))
    parts.append("</tbody></table>")
    for sec in c["sections"]:
        parts.append("<h2>%s</h2>" % _esc(sec["title"]))
        parts.append('<div class="intro">%s</div>' % _esc(sec["intro"]))
        parts.append("<table><thead><tr><th>工序 / 条款</th>%s</tr></thead><tbody>"
                     % "".join("<th>%s</th>" % _esc(lb) for lb in labels))
        for row in sec["rows"]:
            cells = []
            for lb in labels:
                v = row["cells"].get(lb, "—")
                cls = "zero" if v == STATUS_ABSENT else ""
                cells.append('<td class="%s">%s</td>' % (cls, _esc(v)))
            parts.append("<tr><td>%s</td>%s</tr>" % (_esc(row["subject"]), "".join(cells)))
        parts.append("</tbody></table>")
    parts.append("<footer>%s</footer>" % _esc(c["limits"]))
    parts.append("</div></body></html>")
    return "".join(parts)


# ---------------------------------------------------------------------------
# 自测：对照表最容易出的错是「张冠李戴」——把 A 的漏项算到 B 头上，
# 或者把「有」写成「缺」。所以每条断言都带**方向**：谁是缺、谁是有。
# ---------------------------------------------------------------------------

def _q(label, total, rows):
    return {"label": label, "total": total, "rows": rows}


def _row(no, name, cid):
    return {"no": str(no), "name": name, "spec": "", "unit": "m2",
            "qty": 1.0, "price": 100.0, "amount": 100.0, "cid": cid}


def self_test():
    results = []

    def case(name, ok, detail=""):
        results.append({"name": name, "status": "pass" if ok else "fail", "detail": detail})

    quotes = [
        _q("A 公司", 30000.0, [_row(1, "打拆墙体", "demolish"), _row(2, "贴地砖", "tile")]),
        _q("B 公司", 28000.0, [_row(1, "打拆墙体", "demolish")]),
        _q("C 公司", 31000.0, [_row(1, "打拆墙体", "demolish"), _row(2, "贴地砖", "tile")]),
    ]
    res = {
        "A 公司": {"missing": [{"cid": "waterproof", "concept": "防水", "ref_amount": 1200}],
                   "duplicates": [],
                   "obligations": [{"title": "按实结算 → 结算口径未定",
                                    "patterns": ["按实结算"], "amount": 9000}],
                   "effective": 31200.0},
        "B 公司": {"missing": [{"cid": "waterproof", "concept": "防水", "ref_amount": 1200},
                               {"cid": "tile", "concept": "贴地砖", "ref_amount": 800}],
                   "duplicates": [{"cid": "demolish", "concept": "打拆墙体", "amount": 400,
                                   "lines": ["1", "5"]}],
                   "obligations": [{"title": "按实结算 → 结算口径未定",
                                    "patterns": ["按实结算"], "amount": 9000}],
                   "effective": 28800.0},
        "C 公司": {"missing": [{"cid": "waterproof", "concept": "防水", "ref_amount": 1200}],
                   "duplicates": [],
                   "obligations": [{"title": "按实结算 → 结算口径未定",
                                    "patterns": ["按实结算"], "amount": 9000}],
                   "effective": 32200.0},
        "_cross_unit": [],
    }
    c = compare(quotes, res)

    def find(kind, subject):
        for sec in c["sections"]:
            if sec["kind"] != kind:
                continue
            for row in sec["rows"]:
                if row["subject"] == subject:
                    return row
        return None

    water = find("missing", "防水")
    case("三家都缺的漏项：三格都是缺",
         water is not None and all(v == STATUS_ABSENT for v in water["cells"].values()),
         str(water and water["cells"]))
    tile = find("missing", "贴地砖")
    case("只有 A 有贴地砖：A 有、B 缺、C 有",
         tile is not None and tile["cells"]["A 公司"].startswith("有")
         and tile["cells"]["B 公司"] == STATUS_ABSENT
         and tile["cells"]["C 公司"].startswith("有"),
         str(tile and tile["cells"]))
    case("「有」的格子带回原表行号",
         tile is not None and "第 2 行" in tile["cells"]["A 公司"],
         str(tile and tile["cells"]))

    d = find("duplicate", "打拆墙体")
    case("重复只标在 B 身上，A/C 是破折号",
         d is not None and "重复" in d["cells"]["B 公司"]
         and d["cells"]["A 公司"] == "—" and d["cells"]["C 公司"] == "—",
         str(d and d["cells"]))

    ob = find("obligation", "按实结算 → 结算口径未定")
    case("同一类条款三家都含，才说明是惯例",
         ob is not None and all("含" in v for v in ob["cells"].values()),
         str(ob and ob["cells"]))

    case("漏项行按「缺的家数」排序（三家都缺的在最前）",
         c["sections"][0]["rows"][0]["subject"] == "防水",
         str([r["subject"] for r in c["sections"][0]["rows"]]))

    # 空输入：不许崩，也不许凭空造行
    empty = compare([_q("A", 1.0, []), _q("B", 1.0, [])],
                    {"A": {"missing": [], "duplicates": [], "obligations": []},
                     "B": {"missing": [], "duplicates": [], "obligations": []}})
    case("没有发现时不出任何对照节", empty["sections"] == [], str(len(empty["sections"])))

    md = to_markdown(c)
    heads = md.count("| 工序 / 条款 | A 公司 | B 公司 | C 公司 |")
    case("markdown 表头列数 = 公司数 + 1",
         md.count("| 公司 | 报价总价") == 1 and heads == len(c["sections"]),
         "表头 %d 个 / 对照节 %d 个" % (heads, len(c["sections"])))

    xss = compare([_q("<script>x</script>", 1.0, [])],
                  {"<script>x</script>": {"missing": [], "duplicates": [], "obligations": []}})
    case("HTML 转义公司名", "<script>" not in to_html(xss))

    failed = [r for r in results if r["status"] != "pass"]
    return {"self_test": "PASS" if not failed else "FAIL",
            "checks": len(results), "passed": len(results) - len(failed),
            "results": results,
            "violations": [r["name"] for r in failed]}


def main(argv=None):
    import argparse
    import json
    ap = argparse.ArgumentParser(description="同一批报价对照（自测）")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if not a.self_test:
        ap.error("目前只支持 --self-test；生成对照请用 scripts/make_batch_inquiry.py")
    out = self_test()
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        for r in out["results"]:
            print("  %s %s%s" % ("✓" if r["status"] == "pass" else "✗", r["name"],
                                 ("  ← " + r["detail"]) if r["status"] != "pass" else ""))
        print("%s 三家对照双向断言 %d/%d" % (out["self_test"], out["passed"], out["checks"]))
    return 0 if out["self_test"] == "PASS" else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
