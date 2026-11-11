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

# 量纲中文名：问询单里不能出现 area/length 这种内部枚举值，对方看不懂。
DIM_CN = {"area": "面积", "length": "长度", "count": "个数",
          "volume": "体积", "mass": "重量", "trip": "车次", "item": "包干项"}

CN_NUM = "一二三四五六七八九十"

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
    # 跨单口径：不指控「你多报」，只指出「量纲不同，没法直接比」。
    "unit_cross": "贵司此项按「{mine}」计价；同批另外 {n} 家为：{others}。"
                  "两者不是同一量纲（{dims}），工程量不能直接相加或相乘。请确认："
                  "①贵司单位与「{target}」的折算关系（1 单位 = 多少 {target}）；"
                  "②能否改按「{target}」重报一次，便于与其他家对照。",
    # 单内混用：同一份单据里自己就有两种口径，先让它自己说清适用范围。
    "unit_inner": "本单内同一项目同时出现「{mine}」两种计价方式（第 {lines} 行），"
                  "对应{dims}。请说明各自适用范围与折算关系，并统一为一种口径后重报。",
}


def _fmt(x):
    try:
        return "{:,.2f}".format(float(x))
    except (TypeError, ValueError):
        return "—"


def _units(seq):
    return "、".join(seq) if seq else "—"


def _dim_cn(dim):
    return DIM_CN.get(dim, dim)


def _cn(i):
    return CN_NUM[i] if 0 <= i < len(CN_NUM) else str(i + 1)


def _lines(seq):
    return "、".join(str(x) for x in seq) if seq else "—"


def build(quote, res, cross_unit=None):
    """返回 {meta, sections:[{title, intro, items:[{no, subject, ask, basis}]}]}

    cross_unit：跨单口径条目（`audit()["_cross_unit"]` 的子集）。不传就读
    `res["cross_unit"]`——`audit()` 已经把「跟自己有关的那几条」挂到每份单据上了，
    所以单独调 `inquiry.build(q, res)` 的老写法也能拿到本单相关的口径问题。
    """
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
                          "ask": ask, "basis": basis,
                          # 机器可核对的锚点：新版里这个 cid 出现没出现，就能判「落实没落实」
                          "kind": "missing", "cid": m.get("cid")})
        sections.append({
            "title": "请补齐或说明的工序（%d 项）" % len(miss),
            "intro": "以下工序是同类报价的常规必做项，本单未见。请逐项答复是否包含。",
            "items": items})

    dups = res.get("duplicates") or []
    if dups:
        items = []
        for i, d in enumerate(dups, 1):
            items.append({"no": i, "subject": "%s（涉及 %s 元）" % (d["concept"], _fmt(d.get("amount"))),
                          "ask": ASK["duplicate"].format(lines="、".join(d.get("lines") or [])),
                          "basis": (d.get("evidence") or {}).get("basis", ""),
                          "kind": "duplicate", "cid": d.get("cid"),
                          "lines": list(d.get("lines") or [])})
        sections.append({"title": "请说明的重复计价（%d 项）" % len(dups),
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
                          "basis": (x.get("evidence") or {}).get("basis", ""),
                          "kind": "arithmetic", "cid": x.get("cid"),
                          "line": x.get("line")})
        rec = (res.get("recon") or {})
        if rec.get("diff") is not None:
            n += 1
            items.append({
                "no": n, "subject": "整单合计对不上（差 %s 元）" % _fmt(rec["diff"]),
                "ask": ASK["recon"].format(
                    detail=_fmt(rec.get("detail_sum")), fee=_fmt(rec.get("fee_sum")),
                    calc=_fmt((rec.get("detail_sum") or 0) + (rec.get("fee_sum") or 0)),
                    grand=_fmt((rec.get("grand") or {}).get("amount")), diff=_fmt(rec["diff"])),
                "basis": "印刷合计 vs 逐行明细之和 + 派生取费",
                "kind": "recon"})
        sections.append({"title": "请核对的算术与合计（%d 项）" % len(items),
                         "intro": "这些是可以用加减法直接验证的差异，请核对后给出更正版。",
                         "items": items})

    cross = cross_unit
    if cross is None:
        cross = res.get("cross_unit") or res.get("_cross_unit") or []
    label = quote.get("label")
    mine_rows = [x for x in cross if label in (x.get("by_quote") or {})]
    if mine_rows:
        items = []
        for i, x in enumerate(mine_rows, 1):
            by = x.get("by_quote") or {}
            mine = by.get(label) or []
            others = {k: v for k, v in by.items() if k != label}
            dims = " / ".join(_dim_cn(d) for d in (x.get("dims") or [])) or "不同量纲"
            lines = _lines((x.get("lines") or {}).get(label) or [])
            if others:
                # 同批还有别家：以多数写法作为「建议统一口径」，不硬指定哪家对
                target = sorted({u for v in others.values() for u in v})[0]
                ask = ASK["unit_cross"].format(
                    mine=_units(mine), n=len(others),
                    others="、".join("%s 按「%s」" % (k, _units(v)) for k, v in others.items()),
                    dims=dims, target=target)
                basis = ("本体单位定义 + 量纲归一化；贵司第 %s 行；"
                         "其他家单位取自同批报价原文" % lines)
            else:
                ask = ASK["unit_inner"].format(
                    mine=_units(mine), dims=dims, lines=lines)
                basis = "本体单位定义 + 量纲归一化；本单第 %s 行，同一工序出现两种量纲" % lines
            items.append({"no": i, "subject": "%s（计量口径）" % x["concept"],
                          "ask": ask, "basis": basis,
                          "kind": "unit", "cid": x.get("cid"),
                          "dims": list(x.get("dims") or [])})
        sections.append({
            "title": "请确认的计量口径（%d 项）" % len(items),
            "intro": "这些项目的计量单位不是同一量纲（面积 / 长度 / 个数），单看单价会得出"
                     "相反的结论。不是谁报错了，是比价前必须先换算——请对方给出折算关系，"
                     "或直接按统一口径重报一次。",
            "items": items})

    obs = res.get("obligations") or []
    if obs:
        items = []
        for i, o in enumerate(obs, 1):
            items.append({"no": i, "subject": o["title"],
                          "ask": o["confirm"] + "（请书面答复并作为合同附件）",
                          "basis": "原文命中：%s；涉及金额 %s 元" % (
                              "、".join(o.get("patterns") or []), _fmt(o.get("amount"))),
                          "kind": "obligation", "patterns": list(o.get("patterns") or [])})
        sections.append({"title": "请书面确认的开口条款（%d 类）" % len(obs),
                         "intro": "这些条款意味着金额尚未锁定。请把答案写进合同，不要只口头承诺。",
                         "items": items})

    # 编排时的编号：以前每段标题里写死「一、二、三、四」，多一节或某一节为空就出现
    # 「三、」后面直接跟「五、」。改成按实际出现的顺序重排，编号永远连续。
    for i, s in enumerate(sections):
        s["title"] = "%s、%s" % (_cn(i), s["title"].split("、", 1)[-1])

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
        L.append("## %s、请随合同提供的验收凭证" % _cn(len(inq["sections"])))
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


# ---------------------------------------------------------------------------
# 自测：问询单的判据不该靠「读一遍觉得还行」。
#
# 为什么走 --self-test 而不是加 unittest：`data/claims_v0.json` 锁死了
# 「单元测试 344 个」，多一个用例就让台账违规。判据本身还是要的，
# 所以和 pii_scan / verify_server_attestation 一样，用双向断言通道。
#
# 双向的意思是：**该出现的必须出现，不该出现的必须不出现**。
# 只测前者的话，「没有跨单差异也生成一节」这种讨好式的编造永远不会被发现。
# ---------------------------------------------------------------------------

def _probe_quote(label="甲", total=100000.0):
    return {"label": label, "total": total}


def _probe_res(cross=None, missing=None, obligations=None):
    """最小可跑结果：只要 build() 读到的字段都有默认值，就能只测口径这一层。"""
    return {
        "missing": missing or [],
        "duplicates": [],
        "risks": [],
        "recon": {},
        "obligations": obligations or [],
        "cross_unit": cross or [],
    }


def _probe_cross(**over):
    """一条「甲按根、乙丙按米」的跨单口径样本，字段与 cross_unit_mix 输出一致。"""
    item = {
        "cid": "c_tile_skirting", "concept": "踢脚线",
        "units": ["m", "根"], "dims": ["count", "length"],
        "by_quote": {"甲": ["根"], "乙": ["m"], "丙": ["m"]},
        "lines": {"甲": ["10"], "乙": ["4"], "丙": ["6"]},
        "rule": "R5_unit_mix", "confidence": 0.7, "tier": "review",
        "evidence": {"basis": "本体单位定义 + 量纲归一化"},
    }
    item.update(over)
    return item


def self_test():
    results = []

    def case(name, ok, detail=""):
        results.append({"name": name, "status": "pass" if ok else "fail",
                        "detail": detail})

    q = _probe_quote()

    def sec(inq):
        return [s for s in inq["sections"] if "计量口径" in s["title"]]

    # 1) 负向：没有任何跨单差异时，这一节必须不存在
    clean = build(q, _probe_res())
    case("无跨单差异 → 不生成「计量口径」节", not sec(clean),
         "段数 %d" % len(clean["sections"]))
    case("无跨单差异 → markdown 也不提「计量口径」",
         "计量口径" not in to_markdown(clean))

    # 2) 正向：有差异时必须出现，且问的是「折算」而不是「你是不是多报了」
    inq = build(q, _probe_res(cross=[_probe_cross()]))
    got = sec(inq)
    case("有跨单差异 → 出现该节", len(got) == 1, "段数 %d" % len(inq["sections"]))
    ask = got[0]["items"][0]["ask"] if got else ""
    case("问的是折算关系", "折算" in ask and "同批另外 2 家" in ask, ask[:60])

    # 3) 逐字保留双方单位：泛化成「面积/长度」就失去核对价值
    case("本单单位「根」与对方「m」都进问句",
         "根" in ask and ask.count("m") >= 2, ask[:60])
    # 4) 行号要能指回原表
    basis = got[0]["items"][0]["basis"] if got else ""
    case("依据里带本单行号", "第 10 行" in basis, basis)

    # 5) 单内混用：只有自己一家、但同一工序出现两种量纲，问法必须不同
    inner = _probe_cross(by_quote={"甲": ["㎡", "米"]}, units=["㎡", "米"],
                         dims=["area", "length"], lines={"甲": ["4", "9"]})
    iq = build(q, _probe_res(cross=[inner]))
    iask = sec(iq)[0]["items"][0]["ask"] if sec(iq) else ""
    case("单内混用 → 用「本单内」措辞、不提别家",
         "本单内" in iask and "同批另外" not in iask, iask[:60])

    # 6) 负向：别人的口径不能挂到我头上
    other = _probe_cross(by_quote={"乙": ["根"], "丙": ["m"]}, lines={"乙": ["4"]})
    case("只有别家、没有本单 → 不给本单生成该节",
         not sec(build(q, _probe_res(cross=[other]))))

    # 7) 段标题按实际顺序重排：有漏项 + 有口径时，编号必须是一、二
    mk = build(q, _probe_res(
        cross=[_probe_cross()],
        missing=[{"concept": "闭水试验", "tier": "review", "ref_amount": None}]))
    heads = [s["title"].split("、", 1)[0] for s in mk["sections"]]
    case("段标题编号连续", heads == list("一二三四五")[:len(heads)], str(heads))

    # 8) 验收凭证的编号跟随实际段数（以前写死「五、」）
    ob = [{"title": "按实结算", "confirm": "结算口径？", "patterns": ["按实结算"],
           "amount": 1000.0, "evidence": "请在合同中约定结算上限"}]
    with_ev = build(q, _probe_res(cross=[_probe_cross()], obligations=ob))
    md = to_markdown(with_ev)
    expect = "%s、请随合同提供的验收凭证" % _cn(len(with_ev["sections"]))
    case("验收凭证编号跟随段数", expect in md, expect)

    # 9) 网页版同样要带这一节，不能只有 markdown 有
    case("HTML 里也有该节", "计量口径" in to_html(inq) and "折算" in to_html(inq))

    failed = [r for r in results if r["status"] != "pass"]
    return {"self_test": "PASS" if not failed else "FAIL",
            "checks": len(results), "passed": len(results) - len(failed),
            "results": results,
            "violations": [r["name"] for r in failed]}


def main(argv=None):
    import argparse
    import json
    ap = argparse.ArgumentParser(description="问询单自测（双向断言）")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if not a.self_test:
        ap.error("目前只支持 --self-test；生成问询单请用 scripts/make_inquiry.py")
    out = self_test()
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        for r in out["results"]:
            print("  %s %s%s" % ("✓" if r["status"] == "pass" else "✗", r["name"],
                                 ("  ← " + r["detail"]) if r["status"] != "pass" else ""))
        print("%s 问询单双向断言 %d/%d" % (out["self_test"], out["passed"], out["checks"]))
    return 0 if out["self_test"] == "PASS" else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
