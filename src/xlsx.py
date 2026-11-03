# -*- coding: utf-8 -*-
"""零依赖 XLSX 导出：把一次审计写成可发给业主/监理的 Excel。

为什么不用 openpyxl
------------------
引擎与离线包的原则是「能不装就不装」。XLSX 本质是一个 zip + 几份 XML，
生成审计工作簿只需要表格、字符串、数值和两种样式，标准库足够。

输出 6 个 sheet：
  总览 / 漏项 / 重复计价 / 风险 / 对账 / 说明

每个数字都是数值单元格（可继续求和），不是文本。
"""
import os
import zipfile
from xml.sax.saxutils import escape

CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
%s
</Types>"""

ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""

STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<numFmts count="1"><numFmt numFmtId="164" formatCode="#,##0.00"/></numFmts>
<fonts count="2">
<font><sz val="10"/><name val="宋体"/></font>
<font><b/><sz val="10"/><color rgb="FFFFFFFF"/><name val="宋体"/></font>
</fonts>
<fills count="3">
<fill><patternFill patternType="none"/></fill>
<fill><patternFill patternType="gray125"/></fill>
<fill><patternFill patternType="solid"><fgColor rgb="FF0A0A0A"/><bgColor indexed="64"/></patternFill></fill>
</fills>
<borders count="2">
<border><left/><right/><top/><bottom/><diagonal/></border>
<border><left style="thin"><color rgb="FFE4E2DC"/></left><right style="thin"><color rgb="FFE4E2DC"/></right><top style="thin"><color rgb="FFE4E2DC"/></top><bottom style="thin"><color rgb="FFE4E2DC"/></bottom><diagonal/></border>
</borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="4">
<xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1"/>
<xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1"/>
<xf numFmtId="164" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1" applyBorder="1"/>
<xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1" applyAlignment="1"><alignment wrapText="1" vertical="top"/></xf>
</cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>"""


def _col(n):
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _cell(ref, value):
    if value is None or value == "":
        return ""
    if isinstance(value, bool):
        return '<c r="%s" s="0" t="inlineStr"><is><t>%s</t></is></c>' % (
            ref, "是" if value else "否")
    if isinstance(value, (int, float)):
        return '<c r="%s" s="2"><v>%s</v></c>' % (ref, value)
    return '<c r="%s" s="3" t="inlineStr"><is><t xml:space="preserve">%s</t></is></c>' % (
        ref, escape(str(value)))


def _sheet_xml(rows, widths=None):
    if not rows:
        rows = [[""]]
    n_cols = max(len(r) for r in rows)
    cols = ""
    if widths:
        cols = "<cols>" + "".join(
            '<col min="%d" max="%d" width="%s" customWidth="1"/>' % (i, i, w)
            for i, w in enumerate(widths, 1)) + "</cols>"
    body = []
    for ri, row in enumerate(rows, 1):
        cells = []
        for ci in range(1, n_cols + 1):
            val = row[ci - 1] if ci - 1 < len(row) else None
            if ri == 1 and val is not None and val != "":
                cells.append('<c r="%s%d" s="1" t="inlineStr"><is><t>%s</t></is></c>'
                             % (_col(ci), ri, escape(str(val))))
            else:
                cells.append(_cell("%s%d" % (_col(ci), ri), val))
        body.append('<row r="%d">%s</row>' % (ri, "".join(cells)))
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" '
            'activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
            '%s<sheetData>%s</sheetData></worksheet>' % (cols, "".join(body)))


def _workbook_xml(names):
    sheets = "".join(
        '<sheet name="%s" sheetId="%d" r:id="rId%d"/>' % (escape(n), i, i)
        for i, n in enumerate(names, 1))
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets>%s</sheets></workbook>' % sheets)


def _workbook_rels(n):
    rows = "".join(
        '<Relationship Id="rId%d" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet%d.xml"/>'
        % (i, i) for i in range(1, n + 1))
    rows += ('<Relationship Id="rId%d" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
             % (n + 1))
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '%s</Relationships>' % rows)


def write_xlsx(path, sheets):
    """sheets: [(name, rows, widths)]；rows 为二维数组，数值保持数值。"""
    names = [s[0] for s in sheets]
    n = len(sheets)
    overrides = "".join(
        '<Override PartName="/xl/worksheets/sheet%d.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        % i for i in range(1, n + 1))
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", CONTENT_TYPES % overrides)
        z.writestr("_rels/.rels", ROOT_RELS)
        z.writestr("xl/workbook.xml", _workbook_xml(names))
        z.writestr("xl/_rels/workbook.xml.rels", _workbook_rels(n))
        z.writestr("xl/styles.xml", STYLES)
        for i, (name, rows, widths) in enumerate(sheets, 1):
            z.writestr("xl/worksheets/sheet%d.xml" % i, _sheet_xml(rows, widths))


def build_audit_sheets(quotes, res, meta=None):
    """把引擎结果转成 6 个 sheet。所有金额写数值，便于继续求和。"""
    overview = [["公司", "报价总价", "漏项补项", "重复剔除", "真实成本",
                 "风险敞口", "漏项数", "重复数", "风险数", "来源文件"]]
    missing = [["公司", "工序", "参照金额", "档位", "基准", "公式"]]
    dups = [["公司", "工序", "行号", "金额", "档位", "基准", "公式"]]
    risks = [["公司", "工序", "行号", "问题", "规则", "证据强度", "档位"]]
    recon = [["公司", "明细合计", "取费", "印刷合计", "差额", "是否对平"]]
    for q in quotes:
        r = res.get(q["label"]) or {}
        exp = r.get("exposure") or []
        exp_s = ("%s–%s" % (int(exp[0]["low"]), int(exp[0]["high"]))) if exp else "—"
        overview.append([q["label"], q.get("total"), r.get("missing_cost"),
                         r.get("duplicate_cut"), r.get("effective"), exp_s,
                         len(r.get("missing") or []), len(r.get("duplicates") or []),
                         len(r.get("risks") or []), q.get("file", "")])
        for m in r.get("missing") or []:
            ev = m.get("evidence") or {}
            missing.append([q["label"], m.get("concept"), m.get("ref_amount"),
                            m.get("tier"), ev.get("basis"), ev.get("formula")])
        for d in r.get("duplicates") or []:
            ev = d.get("evidence") or {}
            dups.append([q["label"], d.get("concept"), ",".join(str(x) for x in (d.get("lines") or [])),
                         d.get("amount"), d.get("tier"), ev.get("basis"), ev.get("formula")])
        for x in r.get("risks") or []:
            risks.append([q["label"], x.get("concept"), x.get("line"), x.get("issue"),
                          x.get("rule"), x.get("confidence"), x.get("tier")])
        rec = q.get("recon") or {}
        recon.append([q["label"], rec.get("detail_sum"), rec.get("fee_sum"),
                      (rec.get("grand") or {}).get("amount"), rec.get("diff"),
                      "是" if rec.get("ok") is True else ("否" if rec.get("ok") is False else "未提供合计")])
    notes = [
        ["装明白 · 可计算审计 · 导出说明"],
        ["生成时间", (meta or {}).get("time") or ""],
        ["判定指纹", (meta or {}).get("fingerprint") or ""],
        ["口径", "漏项补项=补齐要花的钱，不是省下的钱；重复剔除=可从总价砍掉的重复项；真实成本=总价+漏项−重复；风险敞口单列，不计入。"],
        ["边界", "不判断贵不贵；不做价格鉴定、施工验收、法律意见。低置信度结论只进待确认清单。"],
        ["复算", "同一输入同一输出；审计包 verify.py 可离线复算。"],
    ]
    return [
        ("总览", overview, [22, 12, 12, 12, 12, 14, 8, 8, 8, 22]),
        ("漏项", missing, [22, 18, 12, 8, 46, 46]),
        ("重复计价", dups, [22, 18, 14, 12, 8, 40, 40]),
        ("风险", risks, [22, 22, 10, 52, 16, 10, 8]),
        ("对账", recon, [22, 12, 12, 12, 12, 14]),
        ("说明", notes, [12, 100]),
    ]


def export_audit(path, quotes, res, meta=None):
    write_xlsx(path, build_audit_sheets(quotes, res, meta=meta))
    return path
