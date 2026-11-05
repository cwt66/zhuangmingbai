# -*- coding: utf-8 -*-
"""可分享的 PDF 报告（服务端生成，不依赖浏览器打印）。

为什么做
--------
对标 RenoPit 的「一键下载 PDF，可以拿去和装修公司对峙」。
我们之前只有 `window.print()`，依赖评委/用户自己装打印驱动，而且不同浏览器分页不一致。
这里生成一个自包含的 PDF，内容直接来自同一份 audit 结果——**不重新算，也不重新判断**。

依赖：reportlab（可选）。引擎核心仍然零第三方依赖；没有 reportlab 时
`available()` 返回 False，调用方退回浏览器打印，不静默出错。

用法：
    from report_pdf import available, export_pdf
    export_pdf("out/report.pdf", quotes, res, source="真实单据")
"""
import os

FONT_CANDIDATES = [
    ("/System/Library/Fonts/Supplemental/Arial Unicode.ttf", "CJK", None),
    ("/System/Library/Fonts/Supplemental/Songti.ttc", "CJK", 0),
    ("/System/Library/Fonts/PingFang.ttc", "CJK", 0),
    ("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", "CJK", 0),
]
FONT = "Helvetica"


def available():
    try:
        import reportlab  # noqa: F401
        return True
    except ImportError:
        return False


def _register():
    global FONT
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    for path, name, idx in FONT_CANDIDATES:
        if not os.path.isfile(path):
            continue
        try:
            pdfmetrics.registerFont(TTFont(name, path, subfontIndex=idx) if idx is not None
                                    else TTFont(name, path))
            FONT = name
            return True
        except Exception:  # noqa: BLE001
            continue
    raise RuntimeError("找不到可用的中文字体，无法生成中文 PDF")


def _styles():
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_LEFT
    base = ParagraphStyle("base", fontName=FONT, fontSize=9, leading=13.5,
                          textColor=colors.HexColor("#0A0A0A"), alignment=TA_LEFT,
                          wordWrap="CJK")
    return {
        "h1": ParagraphStyle("h1", parent=base, fontSize=19, leading=25, spaceAfter=4),
        "sub": ParagraphStyle("sub", parent=base, fontSize=9, leading=14,
                              textColor=colors.HexColor("#5A5A55")),
        "h2": ParagraphStyle("h2", parent=base, fontSize=12, leading=17, spaceBefore=12,
                             spaceAfter=5),
        "cell": ParagraphStyle("cell", parent=base, fontSize=8, leading=11.5),
        "note": ParagraphStyle("note", parent=base, fontSize=8, leading=12,
                               textColor=colors.HexColor("#5A5A55")),
    }


def _esc(s):
    return ("" if s is None else str(s)).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _money(v):
    try:
        return "{:,.2f}".format(float(v or 0))
    except (TypeError, ValueError):
        return "—"


def _table(data, widths, st):
    from reportlab.lib import colors
    from reportlab.platypus import Table, TableStyle, Paragraph
    body = [[Paragraph(_esc(c), st["cell"]) if not hasattr(c, "wrap") else c for c in row]
            for row in data]
    t = Table(body, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), FONT),
        ("LINEBELOW", (0, 0), (-1, 0), 1.1, colors.HexColor("#0A0A0A")),
        ("LINEBELOW", (0, 1), (-1, -1), .35, colors.HexColor("#DCDCD4")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
    ]))
    return t


def export_pdf(path, quotes, res, source="", fingerprint=""):
    """把 audit 结果写成 PDF。返回路径。"""
    if not available():
        raise RuntimeError("reportlab 未安装，无法生成 PDF（可退回浏览器打印）")
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                    TableStyle)
    from reportlab.lib import colors
    from datetime import datetime

    _register()
    st = _styles()
    W = A4[0] - 36 * mm
    doc = SimpleDocTemplate(path, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=16 * mm, bottomMargin=16 * mm,
                            title="装明白 · 报价审计报告", author="装明白")
    story = []
    story.append(Paragraph("装明白 · 报价审计报告", st["h1"]))
    story.append(Paragraph(
        "%s ｜ 生成于 %s ｜ 判定结果可由 out/audit_result.json 逐条复核%s"
        % (_esc(source or "报价核对"), datetime.now().strftime("%Y-%m-%d %H:%M"),
           (" ｜ 判定指纹 %s" % _esc(fingerprint)) if fingerprint else ""), st["sub"]))
    story.append(Spacer(1, 8))

    # 总览
    story.append(Paragraph("一、总览", st["h2"]))
    rows = [["单据", "报价(元)", "漏项补项", "重复剔除", "真实成本", "漏项", "重复"]]
    for q in quotes:
        r = res[q["label"]]
        rows.append([q["label"], _money(q["total"]), _money(r["missing_cost"]),
                     _money(r["duplicate_cut"]), _money(r["effective"]),
                     str(len(r["missing"])), str(len(r["duplicates"]))])
    story.append(_table(rows, [W * x for x in (.24, .15, .14, .14, .15, .09, .09)], st))
    story.append(Paragraph(
        "口径：漏项补项=按同批报价横向参照补齐要花的钱（不是省钱）；重复剔除=可砍掉的重复项；"
        "真实成本=总价+漏项−重复；风险敞口单列不计入。", st["note"]))

    for q in quotes:
        r = res[q["label"]]
        story.append(Paragraph("二、%s 明细" % _esc(q["label"]), st["h2"]))
        if r.get("missing"):
            mrows = [["漏项工序", "参照金额", "参照来源", "档位"]]
            for m in r["missing"]:
                src = "、".join(x["quote"] for x in (m.get("ref_source") or [])) or "无参照"
                mrows.append([m["concept"], _money(m.get("ref_amount")) if m.get("ref_amount") else "—",
                              src, m.get("tier", "")])
            story.append(_table(mrows, [W * x for x in (.3, .17, .38, .15)], st))
        if r.get("duplicates"):
            drows = [["重复工序", "行号", "金额", "依据"]]
            for d in r["duplicates"]:
                drows.append([d["concept"], ",".join(d["lines"]), _money(d["amount"]),
                              (d.get("evidence") or {}).get("basis", "")])
            story.append(_table(drows, [W * x for x in (.26, .14, .15, .45)], st))
        rec = r.get("recon") or {}
        if rec.get("grand"):
            story.append(Paragraph(
                "整单对账：明细 %s + 取费 %s，印刷合计 %s，差 %s（%s）"
                % (_money(rec.get("detail_sum")), _money(rec.get("fee_sum")),
                   _money(rec["grand"]["amount"]), _money(rec.get("diff")),
                   "一致" if rec.get("ok") else "不符，须逐项核对"), st["note"]))
        obs = r.get("obligations") or []
        if obs:
            orows = [["开口条款", "涉及金额", "签约前要确认什么"]]
            for o in obs:
                orows.append([o["title"], _money(o.get("amount")) if o.get("amount") else "—",
                              o.get("confirm", "")])
            story.append(_table(orows, [W * x for x in (.28, .16, .56)], st))
        af = r.get("addon_forecast") or {}
        if af.get("summary"):
            sm = af["summary"]
            story.append(Paragraph(
                "增项风险（确定性）：可计价 %d 项，参照价库区间合计 %s – %s 元；"
                "开口条款 %d 类（已列 %s 元，不重复计入）；只可定性的工艺项 %d 项。"
                "<b>不输出发生概率</b>——没有校准过的概率模型。"
                % (sm["priced"], _money(sm["amount_low"]), _money(sm["amount_high"]),
                   sm["open_clause"], _money(sm["exposure_listed"]), sm["process_only"]),
                st["note"]))

    story.append(Spacer(1, 10))
    story.append(Paragraph(
        "结论口径：本报告只回答缺了什么、重了什么、哪些口径不可核、补漏后真实成本是多少；"
        "不判断贵不贵、不评价施工质量、不推断动机。低置信度结论不进入核减金额。"
        "本报告不构成法律意见，也不能替代现场监理。", st["note"]))

    def footer(canvas, d):
        canvas.saveState()
        canvas.setFont(FONT, 7.5)
        canvas.setFillColor(colors.HexColor("#7B7B74"))
        canvas.drawString(18 * mm, 10 * mm, "装明白 · 可计算审计 ｜ 数据仅用于本次核对")
        canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, "第 %d 页" % d.page)
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return path
