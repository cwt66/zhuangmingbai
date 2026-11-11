# -*- coding: utf-8 -*-
"""装明白 · 在线审计 Demo（FastAPI 外壳，引擎零依赖）

本地运行：
    uvicorn main:app --host 0.0.0.0 --port 8000 --reload

设计边界：
- 本服务只做「判断」与「表达」，不做 OCR —— 照片/PDF 的解析由上游 MaaS Qwen-VL 完成，
  解析结果落成与 data/samples/*.csv 相同的结构，因此解析链路可替换、可对照。
- 不判断贵不贵，只判断漏没漏、重没重；低置信度结论不进核减金额。
"""
import csv
import html as html_mod
import io
import json
import os
import sys
import tempfile
import zipfile
from typing import List

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response

BASE = os.path.dirname(os.path.abspath(__file__))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

from src import pipeline  # noqa: E402
from src import perception  # noqa: E402
from src import inquiry as inquiry_mod  # noqa: E402
from src import followup as followup_mod  # noqa: E402
from src import cache as cache_mod  # noqa: E402
from src import llm as llm_mod  # noqa: E402
from src import privacy  # noqa: E402
from src import store as store_mod  # noqa: E402
from src import vision_ocr  # noqa: E402
from src import xlsx as xlsx_mod  # noqa: E402
from src import money  # noqa: E402
from src import doc_gate  # noqa: E402
from src import report_pdf  # noqa: E402
from src import report_gate  # noqa: E402
from src import version_diff as diff_mod  # noqa: E402
fmt_money = pipeline.fmt_money

app = FastAPI(title="装明白 · 可计算审计", version="0.5.0")

MAX_FILES = 4
MIN_FILES = 2
MAX_BYTES = 4 * 1024 * 1024
SAMPLES = [
    ("A 公司（规范型）", "company_a.csv"),
    ("B 公司（低价引流型）", "company_b.csv"),
    ("C 公司（高价项全型）", "company_c.csv"),
]

CSS = """
:root{--paper:#FAFAF8;--ink:#0A0A0A;--ink2:#56606C;--amber:#002FA7;--line:#E4E2DC;--red:#B33A2B}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);
 font-family:"Noto Sans SC","PingFang SC","Hiragino Sans GB","Microsoft YaHei",system-ui,sans-serif;
 -webkit-font-smoothing:antialiased}
a{color:var(--amber);text-decoration:none}
a:hover{text-decoration:underline}
.wrap{max-width:1080px;margin:0 auto;padding:56px 32px 96px}
.kicker{font-size:14px;letter-spacing:2px;color:var(--amber);font-weight:600}
h1{font-size:52px;line-height:1.15;margin:14px 0 10px;letter-spacing:-1px}
.lead{font-size:19px;color:var(--ink2);line-height:1.7;max-width:760px}
.rule{height:2px;background:var(--ink);margin:34px 0 0}
.grid{display:grid;grid-template-columns:1.35fr .95fr;gap:44px;margin-top:38px}
.card{background:#fff;border:1px solid #E4E2DC;border-radius:3px;padding:30px;border-top:2px solid var(--ink)}
.card h2{font-size:20px;margin:0 0 6px}
.card p{font-size:15px;color:var(--ink2);line-height:1.7;margin:0 0 18px}
.drop{border:1.5px dashed #C9C5BA;border-radius:3px;padding:26px;text-align:center;background:#fff}
.drop input{display:block;margin:0 auto;font-size:14px;color:var(--ink2)}
.btn{display:inline-block;border:0;cursor:pointer;background:var(--ink);color:#fff;
 font-size:17px;font-weight:600;padding:14px 30px;border-radius:3px;margin-top:20px;font-family:inherit}
.btn:hover{background:var(--amber)}
.btn.ghost{background:transparent;color:var(--ink);border:1.5px solid var(--ink);padding:12px 24px}
.meta{font-size:13px;color:#8A877F;margin-top:12px;line-height:1.7}
ol.steps{padding-left:20px;margin:0;color:var(--ink2);font-size:15px;line-height:2}
table.s{margin-top:22px;border-collapse:collapse;width:100%;font-size:14px}
table.s td{border-bottom:1px solid var(--line);padding:9px 0;color:var(--ink2)}
table.s td:first-child{color:var(--ink);font-weight:600}
.err{background:#fff;border-left:4px solid var(--red);padding:22px 26px;margin-top:30px}
.err h2{margin:0 0 8px;font-size:20px}
.nav{position:sticky;top:0;z-index:9;background:rgba(250,250,248,.95);border-bottom:1px solid var(--line);
 padding:12px 32px;font-size:14px;color:var(--ink2);backdrop-filter:blur(6px)}
.nav b{color:var(--ink)}
.nav a{margin-right:18px}
.nav .right{float:right}
.note{font-size:13px;color:#8A877F;line-height:1.7}
.plain{padding-left:18px;color:var(--ink2);font-size:15px;line-height:1.9}
.plain code,code{background:#F1EFE9;padding:1px 5px;border-radius:2px;font-size:13px}
label{display:inline-block;margin:10px 16px 0 0;font-size:14px;color:var(--ink2)}
select{font:inherit;font-size:14px;padding:7px 10px;border:1px solid #C9C5BA;background:#fff;border-radius:2px}
.banner{background:#0A0A0A;color:#fff;padding:14px 22px;margin:0;font-size:14px;line-height:1.7}
.banner b{color:#F0B429}
h2{font-size:22px;margin:30px 0 10px}
.ai-note{background:#fff;border:1px solid #E4E2DC;border-left:4px solid #002FA7;padding:22px 26px;margin:26px 0}
.ai-note h2{margin:0 0 8px;font-size:20px}
.ai-note .badge{display:inline-block;font-size:12px;color:#002FA7;border:1px solid #002FA7;
 border-radius:2px;padding:2px 8px;margin-left:8px;vertical-align:middle}
@media print{.no-print{display:none!important}body{background:#fff}}
textarea.csv{width:100%;min-height:420px;font:13px/1.5 "SF Mono",Consolas,monospace;
 border:1px solid #C9C5BA;padding:14px;background:#fff;border-radius:3px}
table.draft{width:100%;border-collapse:collapse;font-size:13px;margin-top:10px}
table.draft th,table.draft td{border-bottom:1px solid var(--line);padding:7px 6px;text-align:left;vertical-align:top}
table.draft th{background:#0A0A0A;color:#fff;font-weight:600}
table.draft tr.warn td{background:#FFF7E6}
.flag{display:inline-block;font-size:11px;color:#B33A2B;border:1px solid #B33A2B;
 border-radius:2px;padding:1px 5px;margin:1px 2px 1px 0}
"""


class EmptyRowsError(ValueError):
    """CSV 解析后没有行项（只有表头或空数据）"""


class WrongDocTypeError(ValueError):
    """上传的不是报价单（合同 / 无关文档 / 证据不足），按报价单审会误导用户。"""
    pass

PAGE = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>装明白 · 装修报价可计算审计</title><style>__CSS__</style></head><body>
<div class="wrap">
  <div class="kicker">华为 ICT 大赛创新赛 · 赛题1 · 在线 Demo</div>
  <h1>装明白 · 可计算审计</h1>
  <p class="lead">上传 2–4 家装修公司的报价单，输出一份可核对、可溯源、带证据强度的审计报告：
  谁漏了项、谁重复收了钱、谁口径混乱、补漏后真实成本是多少。
  <b>我们不判断贵不贵，只判断漏没漏、重没重。</b></p>
  <div class="rule"></div>

  <div class="grid">
    <div class="card">
      <h2>上传报价单</h2>
      <p>当前支持 CSV 行项（项目名称 / 规格工艺 / 单位 / 数量 / 单价 / 合价）。
      照片与 PDF 由 MaaS Qwen-VL 解析为同一结构后进入本引擎。</p>
      <form action="/audit" method="post" enctype="multipart/form-data">
        <div class="drop">
          <input type="file" name="files" accept=".csv" multiple required>
        </div>
        <div class="meta" style="display:flex;gap:16px;flex-wrap:wrap;margin:10px 0 4px">
          <label>模板
            <select name="template_key">
              <option value="old_house">老房翻新（39 项）</option>
              <option value="new_house">新房毛坯（28 项）</option>
            </select>
          </label>
          <label>报价范围
            <select name="scope">
              <option value="full">全包（含主材，默认）</option>
              <option value="base">基础施工（主材另计）</option>
            </select>
          </label>
        </div>
        <div class="meta">范围选「基础施工」时，门槛石/踢脚线/木门/洁具/灯具等主材项不再判漏——
        你的单据只报基础工程，就不该按全包口径要求它。</div>
        <button class="btn" type="submit">开始审计</button>
        <button class="btn ghost" type="submit" formaction="/audit.xlsx">下载 Excel（可审计工作簿）</button>
        <div class="meta">2–4 份，单份 ≤ 4MB，UTF-8 编码（可带 BOM）</div>
      </form>
      <p style="margin-top:26px">没有单据？<a href="/demo">用三家样例跑一次</a>　·　<a href="/review">看解析校对台</a></p>
    </div>

    <div class="card">
      <h2>它到底判断什么</h2>
      <ol class="steps">
        <li>漏项：标准工序模板里必须有、这份报价里没有</li>
        <li>重复计价：本体归一后同一工序被收两次</li>
        <li>口径风险：按实结算 / 按根 / 按米 混着报</li>
        <li>算术与规格：合价对不上数量×单价、缺规格</li>
      </ol>
      <table class="s">
        <tr><td><a href="/samples/company_a.csv">下载样例 A</a></td><td>结构完整，无重复</td></tr>
        <tr><td><a href="/samples/company_b.csv">下载样例 B</a></td><td>漏 17 项，口径异常 7 处</td></tr>
        <tr><td><a href="/samples/company_c.csv">下载样例 C</a></td><td>重复 2 项，规格齐全</td></tr>
      </table>
      <div class="meta">样例为 80㎡ 老房翻新的合成单据（结构取自真实晒单，金额重制），仅用于链路验证。</div>
    </div>
  </div>
  __BODY__
</div></body></html>"""

ERR = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>输入有误</title>
<style>__CSS__</style></head><body><div class="wrap">
<div class="kicker">装明白 · 可计算审计</div><h1 style="font-size:40px">这次没跑成</h1>
<div class="err"><h2>__TITLE__</h2><p class="lead" style="font-size:16px">__DETAIL__</p></div>
<p style="margin-top:30px"><a href="/">返回上传页</a>　·　<a href="/demo">用样例跑一次</a></p>
</div></body></html>"""

NAV = """<div class="nav"><b>装明白 · 可计算审计</b>
<a href="/" style="margin-left:18px">重新上传</a><a href="/demo">样例演示</a><a href="/real">真实单据</a><a href="/perceive">图片识别</a><a href="/review">解析校对</a><a href="/inquiry">问询单</a><a href="/inquiry/demo">对照问询单</a><a href="/followup">落实情况</a><a href="/diff">两版对比</a>
<span class="right">浏览器 Ctrl/⌘+P 可另存为 PDF · <a href="/health">健康检查</a></span></div>"""


def _page(body: str = "") -> str:
    """基础页：把 __BODY__ 填上，避免各路由各自拼整页（曾导致 /perceive 渲染成首页）。"""
    hosted = PAGE.replace("__CSS__", CSS)
    if "<body>" in hosted:
        hosted = hosted.replace("<body>", "<body>" + NAV, 1)
    return hosted.replace("__BODY__", body)


def _sub_page(body: str) -> HTMLResponse:
    return HTMLResponse(_page(body))


def _err(title: str, detail: str, code: int = 400) -> HTMLResponse:
    return HTMLResponse(
        ERR.replace("__CSS__", CSS).replace("__TITLE__", title).replace("__DETAIL__", detail),
        status_code=code,
    )


def _load_rules():
    onto = pipeline.Ontology(os.path.join(pipeline.DATA, "ontology_v0.json"))
    with open(os.path.join(pipeline.DATA, "templates_v0.json"), encoding="utf-8") as f:
        templates = json.load(f)
    with open(os.path.join(pipeline.DATA, "rules_v0.json"), encoding="utf-8") as f:
        rules = json.load(f)
    # 漏项判定的第二路证据（复合行文本证据词）。缺文件时引擎仍可跑，只是回到纯概念对齐。
    cover = os.path.join(pipeline.DATA, "coverage_v0.json")
    coverage = None
    if os.path.exists(cover):
        with open(cover, encoding="utf-8") as f:
            coverage = json.load(f)
    return onto, templates, rules, coverage


def _gate_banner(quotes, res, onto, templates, rules, coverage, template_key, scope):
    """把交付门结论放在报告最上面——之前它只活在 CLI 与测试里。

    工作流梳理发现一个真断点：`report_gate` 在 main.py 里零引用，
    Web 的 12 条判定路由全都「算出结论就直接出报告」。
    也就是说评委在页面上点开的那份报告，从来没问过自己「能不能交出去」。

    这里不阻断 Web（演示环境仍需能看），但把三档判定明明白白写在最顶上：
    block 时直接写「此报告不应交付给业主」。**不阻断 ≠ 不告知**。
    """
    def replay(label):
        def _run():
            return pipeline.audit(quotes, onto, templates, rules, coverage,
                                  template_key=template_key, scope=scope)[label]
        return _run

    marks = {"pass": ("可交付", "#1B5E20", "#E8F5E9", "#2E7D32"),
             "needs_fix": ("需修正", "#7A4B00", "#FFF8E1", "#B26A00"),
             "block": ("禁止交付", "#8E1B1B", "#FFEBEE", "#C62828")}
    cards = []
    for q in quotes:
        r = res.get(q["label"]) or {}
        try:
            g = report_gate.check(q, r, replay=replay(q["label"]))
        except Exception as e:  # noqa: BLE001
            cards.append('<div style="padding:8px 12px;background:#FFF8E1;color:#7A4B00;'
                         'font-size:13px">交付门本次未运行（%s）；这不代表通过。</div>'
                         % html_mod.escape(type(e).__name__))
            continue
        word, fg, bg, bd = marks.get(g["verdict"], marks["needs_fix"])
        failed = [c for c in g["checks"] if not c["ok"] and c.get("level") not in (None, "skip")]
        detail = "、".join("%s %s" % (c["id"], c["name"]) for c in failed[:4]) or "7 项检查全部通过"
        warn = ("<b>此报告不应交付给业主</b>；" if g["verdict"] == "block" else "")
        cards.append(
            '<div style="margin:10px 24px 0;padding:9px 14px;border-left:4px solid %s;'
            'background:%s;color:%s;font-size:13px;line-height:1.6">'
            '<b>交付门 · %s</b>｜%s %s'
            '<span style="opacity:.75">（D1–D7；失败项：%s）</span></div>'
            % (bd, bg, fg, html_mod.escape(q["label"]), word, warn, html_mod.escape(detail)))
    return "".join(cards)


def _audit(paths, template_key="old_house", scope="full"):
    """paths: [(label, csv_path)] -> (html, res, quotes)"""
    onto, templates, rules, coverage = _load_rules()
    # 解析之前先守门：合同/无关文档连解析都不该开始。否则用户看到的是
    # 「没找到『项目名称』列」这种内部字段名，而不是「这不是报价单」。
    for lb, p in paths:
        ok_f, info_f = doc_gate.gate_file(p, lb)
        if not ok_f:
            raise WrongDocTypeError(info_f["message"])
    quotes = [pipeline.build_line_index(pipeline.load_quote(p, lb), onto) for lb, p in paths]
    for q in quotes:
        if not q["rows"]:
            raise EmptyRowsError("《%s》没有解析到任何行项，请检查表头是否为 "
                                 "项目名称/规格工艺/单位/数量/单价/合价。" % q["label"])
        # 上传守门：先确认这是报价单，再允许审计（对标 RenoPit 的 classify_document，
        # 但我们给命中词/列结构证据，且判定为合同或无关文档时直接拒绝）。
        # 用原始文件名做提示，别把临时文件路径暴露给用户
        ok, info = doc_gate.gate(q["rows"], q.get("label") or q.get("file") or "")
        if not ok:
            raise WrongDocTypeError(info["message"])
        q["doc_gate"] = info
    res = pipeline.audit(quotes, onto, templates, rules, coverage,
                         template_key=template_key, scope=scope)
    html = pipeline.build_html(quotes, res)
    if "<body>" in html:
        banner = _gate_banner(quotes, res, onto, templates, rules, coverage,
                              template_key, scope)
        html = html.replace("<body>", "<body>" + NAV + banner, 1)
    return html, res, quotes


def _xlsx_response(paths, template_key, scope, name="zhuangmingbai_audit.xlsx"):
    _, res, quotes = _audit(paths, template_key=template_key, scope=scope)
    fd, tmp = tempfile.mkstemp(suffix=".xlsx")
    os.close(fd)
    try:
        from datetime import datetime
        xlsx_mod.export_audit(tmp, quotes, res, meta={
            "time": datetime.now().astimezone().isoformat(timespec="seconds"),
            "fingerprint": _fingerprint(res),
        })
        data = open(tmp, "rb").read()
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    return Response(content=data,
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="%s"' % name})


def _pdf_response(paths, template_key, scope, source, name="zhuangmingbai_report.pdf"):
    """服务端生成可分享 PDF；reportlab 缺失时明确报错，不静默降级。"""
    if not report_pdf.available():
        return _err("PDF 导出不可用", "需要安装 reportlab（pip install reportlab）；"
                    "也可使用报告页的「打印 / 另存 PDF」。", 503)
    _, res, quotes = _audit(paths, template_key=template_key, scope=scope)
    fd, tmp = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    try:
        report_pdf.export_pdf(tmp, quotes, res, source=source,
                              fingerprint=_fingerprint(res))
        data = open(tmp, "rb").read()
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    return Response(content=data, media_type="application/pdf",
                    headers={"Content-Disposition": 'attachment; filename="%s"' % name})


def _label_from_name(name: str) -> str:
    stem = os.path.splitext(os.path.basename(name or ""))[0].strip()
    return stem[:24] or "未命名报价"


# ---------------------------------------------------------------- 云原生落地
def _client_ip(request):
    xff = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    return xff or (request.client.host if request.client else "unknown")


def _rate_ok(request, bucket="audit"):
    """按 IP 限流。默认 60 次/分钟，可用 RATE_LIMIT 调整；返回 (ok, remaining)。"""
    if os.environ.get("RATE_LIMIT", "1") == "0":
        return True, 999
    try:
        limit = int(os.environ.get("RATE_LIMIT", "60"))
    except ValueError:
        limit = 60
    ok, remain, _ = cache_mod.get_cache().rate_limit(bucket + ":" + _client_ip(request),
                                                     limit=limit, window=60)
    return ok, remain


def _fingerprint(res):
    try:
        return pipeline._report_fingerprint(res)  # noqa: SLF001（报告指纹是引擎公开契约）
    except Exception:  # noqa: BLE001
        return ""


def _quality(quotes, res):
    """报告自带的质量门：对齐率 / 算术 / 模板 / 对账 / 指纹。"""
    rows = sum(len(q.get("rows") or []) for q in quotes)
    aligned = sum(1 for q in quotes for r in (q.get("rows") or []) if r.get("cid"))
    arith_rows = 0
    arith_bad = 0
    for q in quotes:
        for r in (q.get("rows") or []):
            if r.get("qty") and r.get("price") and r.get("amount"):
                arith_rows += 1
                if abs(r["qty"] * r["price"] - r["amount"]) > 1:
                    arith_bad += 1
    recon = []
    recon_exact = []
    for q in quotes:
        rec = q.get("recon") or {}
        if rec.get("grand"):
            recon.append({"label": q["label"], "grand": rec["grand"]["amount"],
                          "diff": rec.get("diff"), "ok": rec.get("ok")})
            detail = money.sum2([r["amount"] for r in (q.get("rows") or [])])
            fees = money.sum2([f["amount"] for f in (rec.get("fees") or [])])
            diff = money.diff2(rec["grand"]["amount"], money.q2(detail + fees))
            recon_exact.append({"label": q["label"], "diff": str(diff),
                                "ok": diff == money.d("0")})
    rate = round(aligned / rows, 3) if rows else 0.0
    ok = bool(rows) and rate >= 0.6 and arith_bad == 0
    return {
        "rows": rows, "aligned": aligned, "align_rate": rate,
        "arith_rows": arith_rows, "arith_bad": arith_bad,
        "missing": sum(len((res.get(q["label"]) or {}).get("missing") or []) for q in quotes),
        "duplicates": sum(len((res.get(q["label"]) or {}).get("duplicates") or []) for q in quotes),
        "risks": sum(len((res.get(q["label"]) or {}).get("risks") or []) for q in quotes),
        "recon": recon, "recon_exact": recon_exact,
        "gate": "pass" if ok else "review",
        "fingerprint": _fingerprint(res),
    }


def _quality_html(q):
    if not q:
        return ""
    ok = q.get("gate") == "pass"
    badge = "可复核" if ok else "需复核"
    color = "#0A7C42" if ok else "#B33A2B"
    recon = ""
    if q.get("recon"):
        d = q["recon"][0]
        recon = " ｜ 对账 %s，差 %s" % (fmt_money(d.get("grand")), fmt_money(d.get("diff")))
    exact = ""
    if q.get("recon_exact"):
        parts = []
        for x in q["recon_exact"][:3]:
            parts.append("%s %s" % (x.get("label"), "对平" if x.get("ok") else "差 " + str(x.get("diff"))))
        exact = " ｜ 分位对账 " + "；".join(parts)
    return ('<div class="qgate" style="max-width:1040px;margin:22px auto;padding:14px 20px;'
            'border:1px solid #E4E2DC;border-left:4px solid %s;background:#fff;font-size:13px;'
            'color:#56606C;line-height:1.8">'
            '<b style="color:%s">质量门 · %s</b> ｜ 对齐 <b>%d/%d</b>（%.1f%%）'
            ' ｜ 算术 <b>%d</b> 行，%s'
            ' ｜ 漏项 <b>%d</b> ・ 重复 <b>%d</b> ・ 风险 <b>%d</b>%s%s'
            ' ｜ 指纹 <code>%s</code></div>') % (
        color, color, badge, q["aligned"], q["rows"], q["align_rate"] * 100,
        q["arith_rows"], ("0 不符" if q["arith_bad"] == 0 else "<b style='color:#B33A2B'>%d 不符</b>" % q["arith_bad"]),
        q["missing"], q["duplicates"], q["risks"], recon, exact, q["fingerprint"])


def _persist(quotes, res, source="upload", docs=None):
    """把摘要写进 RDS/SQLite。失败不阻断报告——演示不能因为库挂了而白屏。"""
    try:
        payload = {"quality": _quality(quotes, res), "documents": [
            {"label": q["label"], "total": q.get("total"),
             "missing": [{"cid": m.get("cid"), "name": m.get("name") or m.get("concept"),
                          "amount": m.get("ref_amount"), "tier": m.get("tier")}
                         for m in (res.get(q["label"]) or {}).get("missing", [])],
             "duplicates": [{"cid": d.get("cid"), "name": d.get("name") or d.get("concept"),
                             "amount": d.get("amount"), "tier": d.get("tier")}
                            for d in (res.get(q["label"]) or {}).get("duplicates", [])],
             }
            for q in quotes]}
        record = {
            "source": source,
            "template_key": (res.get(quotes[0]["label"]) or {}).get("template_key"),
            "scope": (res.get(quotes[0]["label"]) or {}).get("scope"),
            "doc_count": len(quotes),
            "docs": docs or [q["label"] for q in quotes],
            "total": sum(float(q.get("total") or 0) for q in quotes),
            "missing_cost": sum(float((res.get(q["label"]) or {}).get("missing_cost") or 0)
                                for q in quotes),
            "duplicate_cut": sum(float((res.get(q["label"]) or {}).get("duplicate_cut") or 0)
                                 for q in quotes),
            "effective": sum(float((res.get(q["label"]) or {}).get("effective") or 0)
                             for q in quotes),
            "fingerprint": _fingerprint(res),
            "payload": payload,
        }
        return store_mod.get_store().save_audit(record)
    except Exception:  # noqa: BLE001
        return None


def _decorate(html, quotes, res, audit_id=None, source="upload",
              xlsx_url=None, pdf_url=None):
    """在报告尾部加「解读」（MaaS 或模板）与审计编号。"""
    qgate = _quality_html(_quality(quotes, res))
    try:
        facts = llm_mod.build_facts(quotes, res)
        ai = llm_mod.get_client().explain(facts, cache=cache_mod.get_cache())
        section = qgate + llm_mod.render_html(ai)
    except Exception as e:  # noqa: BLE001
        section = ('<div class="ai-note"><h2>解读</h2><p class="note">解读生成失败：%s</p></div>'
                   % e)
    aid = audit_id or "未入库"
    fb = ""
    if audit_id:
        fb = ('<div class="wrap no-print" style="padding-top:0"><div class="card">'
              '<h2>这份报告有用吗？</h2>'
              '<p class="note">真实用户的反馈会进入试点记录；不会展示给报价方。'
              '请勿填写姓名/电话。</p>'
              '<form method="post" action="/feedback">'
              '<input type="hidden" name="audit_id" value="%s">'
              '<label>你是 <select name="role">'
              '<option value="owner">业主</option><option value="family">业主家属</option>'
              '<option value="reviewer">监理/从业者</option><option value="other">其他</option>'
              '</select></label>'
              '<label><input type="radio" name="useful" value="1" checked> 有用</label>'
              '<label><input type="radio" name="useful" value="0"> 没用</label>'
              '<div><textarea name="note" rows="2" style="width:100%%;margin-top:8px" '
              'placeholder="哪一条最有用？哪一条看不懂？"></textarea></div>'
              '<button class="btn" type="submit">提交反馈</button>'
              '</form></div></div>') % aid
    xl = ('' if not xlsx_url else
          '<a class="btn ghost no-print" href="%s" style="margin-top:10px">下载 Excel</a> '
          % html_mod.escape(xlsx_url))
    xl += ('' if not pdf_url else
           '<a class="btn ghost no-print" href="%s" style="margin-top:10px">下载 PDF</a>'
           % html_mod.escape(pdf_url))
    footer = ('<div class="wrap" style="padding-top:0"><p class="note">审计编号 %s ｜ 来源 %s ｜ '
              '<a href="/history">历史记录</a> ｜ 数据仅用于本次核对，不用于训练</p>'
              '%s<button class="btn no-print" onclick="window.print()" style="margin-top:10px">'
              '打印 / 另存 PDF</button></div>' % (aid, source, xl))
    if "</body>" in html:
        html = html.replace("</body>", section + fb + footer + "</body>", 1)
    return html


def _rows_csv_text(rows):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=perception.COLUMNS)
    w.writeheader()
    for r in rows:
        w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in perception.COLUMNS})
    return buf.getvalue()


def _vision_draft_page(rows, report, csv_text, filename, template_key, scope):
    tr = []
    for r in rows:
        flags = r.get("_flags") or []
        cls = ' class="warn"' if flags else ""
        tr.append("<tr%s><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
                  "<td>%s</td><td>%s</td></tr>" % (
                      cls, html_mod.escape(str(r.get("序号") or "")),
                      html_mod.escape(str(r.get("项目名称") or "")),
                      html_mod.escape(str(r.get("单位") or "")),
                      html_mod.escape(str(r.get("数量") if r.get("数量") is not None else "")),
                      html_mod.escape(str(r.get("单价") if r.get("单价") is not None else "")),
                      html_mod.escape(str(r.get("合价") if r.get("合价") is not None else "")),
                      "".join('<span class="flag">%s</span>' % html_mod.escape(f) for f in flags)))
    body = """
<h1>识别草稿：先校对，再审计</h1>
<p class="lead">本地 OCR 只负责「看见」。以下 <b>%d</b> 行里，critic 通过 <b>%d</b> 行、
需核对 <b>%d</b> 行（%.0f%%）。<b>直接拿草稿去判漏项会误报</b>，所以这里停下来，
请你改完下面的 CSV 再点审计。</p>
<div class="card" style="margin:22px 0">
  <h2>这一次识别看到了什么</h2>
  <table class="draft"><tr><th>序号</th><th>项目</th><th>单位</th><th>数量</th>
  <th>单价</th><th>合价</th><th>critic</th></tr>%s</table>
</div>
<form method="post" action="/vision/audit">
  <input type="hidden" name="template_key" value="%s">
  <input type="hidden" name="scope" value="%s">
  <input type="hidden" name="filename" value="%s">
  <label style="display:block;margin:18px 0 8px">识别 CSV（可直接改；改完再审计）</label>
  <textarea class="csv" name="csv_text">%s</textarea>
  <button class="btn" type="submit">我核对完了，开始审计</button>
  <p class="note">建议至少确认：项目名称有没有错字、单位/数量/单价/合价是否对得上。
  量价不符的行会在报告里变成算术风险，不会静默通过。</p>
</form>
""" % (report.get("rows") or 0, report.get("accepted") or 0, report.get("review") or 0,
       (report.get("review_ratio") or 0) * 100, "".join(tr),
       html_mod.escape(template_key), html_mod.escape(scope), html_mod.escape(filename),
       html_mod.escape(csv_text))
    return _sub_page(body)


@app.post("/vision/audit", response_class=HTMLResponse)
async def vision_audit(request: Request, csv_text: str = Form(default=""),
                       template_key: str = Form(default="old_house"),
                       scope: str = Form(default="full"),
                       filename: str = Form(default="vision_draft")):
    ok, _ = _rate_ok(request, "vision")
    if not ok:
        return _err("请求过于频繁", "每个来源每分钟最多 60 次审计。", 429)
    if not (csv_text or "").strip():
        return _err("没有 CSV", "校对后的 CSV 是空的。", 400)
    tmp = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8-sig")
    try:
        tmp.write(csv_text)
        tmp.close()
        html, res, quotes = _audit([(_label_from_name(filename), tmp.name)],
                                   template_key=template_key, scope=scope)
        aid = _persist(quotes, res, source="vision_reviewed", docs=[filename])
        html = _decorate(html, quotes, res, aid, "本地 OCR + 人工校对")
    except WrongDocTypeError as e:
        return _err("文档类型不符", str(e), 400)
    except pipeline.HeaderMismatchError as e:
        return _err("表头不符", "%s 请确认为带「项目名称/数量/单价/合价」列的报价表。" % e, 400)
    except EmptyRowsError as e:
        return _err("没有解析到行项", str(e), 400)
    except Exception as e:  # noqa: BLE001
        return _err("审计失败", str(e), 500)
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
    return HTMLResponse(html)


@app.post("/feedback", response_class=HTMLResponse)
async def feedback(request: Request, audit_id: str = Form(default=""),
                   role: str = Form(default="owner"), useful: str = Form(default="1"),
                   note: str = Form(default="")):
    ok, _ = _rate_ok(request, "feedback")
    if not ok:
        return _err("请求过于频繁", "请稍后再试。", 429)
    if not audit_id:
        return _err("缺少审计编号", "反馈必须关联一次审计。", 400)
    store_mod.get_store().save_feedback(audit_id, role=role, useful=(useful == "1"), note=note)
    return _sub_page("""
<h1>谢谢，反馈已记录</h1>
<p class="lead">这条反馈会进入试点记录，用来改报告，不用来训练模型。</p>
<p><a href="/report/%s">回到这次审计</a>　·　<a href="/history">看历史</a></p>
""" % html_mod.escape(audit_id))


@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(_page())


@app.get("/health")
def health():
    store = store_mod.get_store()
    cch = cache_mod.get_cache()
    ok = store.health().get("status") == "ok"
    return JSONResponse({
        "status": "ok" if ok else "degraded",
        "service": "zhuangmingbai-audit",
        "version": app.version,
        "db": store.health(),
        "cache": cch.health(),
        "llm": {"enabled": llm_mod.get_client().available(),
                "model": llm_mod.get_client().model if llm_mod.get_client().available() else None},
        "vision": (lambda a: {"available": a[0], "detail": a[1]})(vision_ocr.available()),
        "time": __import__("datetime").datetime.now().astimezone().isoformat(timespec="seconds"),
    })


@app.get("/samples/{name}")
def sample(name: str):
    safe = os.path.basename(name)
    path = os.path.join(pipeline.DATA, "samples", safe)
    if not safe.endswith(".csv") or not os.path.isfile(path):
        return PlainTextResponse("not found", status_code=404)
    with open(path, encoding="utf-8-sig") as f:
        return PlainTextResponse(f.read(), media_type="text/csv; charset=utf-8")


@app.get("/demo", response_class=HTMLResponse)
def demo():
    paths = [(lb, os.path.join(pipeline.DATA, "samples", fn)) for lb, fn in SAMPLES]
    try:
        html, res, quotes = _audit(paths)
        aid = _persist(quotes, res, source="demo")
        html = _decorate(html, quotes, res, aid, "合成样例", xlsx_url="/demo.xlsx", pdf_url="/demo.pdf")
    except WrongDocTypeError as e:
        return _err("文档类型不符", str(e), 400)
    except pipeline.HeaderMismatchError as e:
        return _err("表头不符", "%s 请确认为带「项目名称/数量/单价/合价」列的报价表。" % e, 400)
    except EmptyRowsError as e:
        return _err("样例数据异常", str(e), 400)
    except Exception as e:  # noqa: BLE001
        return _err("样例跑失败", str(e), 500)
    return HTMLResponse(html)


@app.get("/real", response_class=HTMLResponse)
def real():
    """一份真实业主报价单（127 行，54,936 元，来源见 data/real/README）。"""
    path = os.path.join(pipeline.DATA, "real", "sh_zhuoguan_halfpack.csv")
    if not os.path.isfile(path):
        return _err("真实单据缺失", "仓库内未找到 data/real/sh_zhuoguan_halfpack.csv。", 404)
    try:
        html, res, quotes = _audit([("上海卓观 · 两房半包（真实单据）", path)])
        aid = _persist(quotes, res, source="real")
        html = _decorate(html, quotes, res, aid, "真实单据", xlsx_url="/real.xlsx", pdf_url="/real.pdf")
    except Exception as e:  # noqa: BLE001
        return _err("真实单据跑失败", str(e), 500)
    return HTMLResponse(html)




@app.get("/demo.xlsx")
def demo_xlsx():
    paths = [(lb, os.path.join(pipeline.DATA, "samples", fn)) for lb, fn in SAMPLES]
    return _xlsx_response(paths, "old_house", "full", "zhuangmingbai_demo.xlsx")


@app.get("/real.xlsx")
def real_xlsx():
    path = os.path.join(pipeline.DATA, "real", "sh_zhuoguan_halfpack.csv")
    if not os.path.isfile(path):
        return _err("真实单据缺失", "仓库内未找到真实单据。", 404)
    return _xlsx_response([("上海卓观 · 两房半包（真实单据）", path)],
                          "old_house", "full", "zhuangmingbai_real.xlsx")


@app.get("/demo.pdf")
def demo_pdf():
    paths = [(lb, os.path.join(pipeline.DATA, "samples", fn)) for lb, fn in SAMPLES]
    return _pdf_response(paths, "old_house", "full", "合成样例", "zhuangmingbai_demo.pdf")


@app.get("/real.pdf")
def real_pdf():
    path = os.path.join(pipeline.DATA, "real", "sh_zhuoguan_halfpack.csv")
    if not os.path.isfile(path):
        return _err("真实单据缺失", "仓库内未找到真实单据。", 404)
    return _pdf_response([("上海卓观 · 两房半包（真实单据）", path)],
                         "old_house", "full", "真实单据", "zhuangmingbai_real.pdf")


@app.post("/audit.xlsx")
async def audit_xlsx(request: Request, files: List[UploadFile] = File(default=None),
                     template_key: str = Form(default="old_house"),
                     scope: str = Form(default="full")):
    ok, _ = _rate_ok(request, "xlsx")
    if not ok:
        return _err("请求过于频繁", "每个来源每分钟最多 60 次导出。", 429)
    if not files or len(files) < 2:
        return _err("至少需要 2 份报价单", "单份单据无法做跨单对齐。")
    if len(files) > MAX_FILES:
        return _err("最多 4 份", "一次最多比较 4 家。")
    tmpdir = tempfile.mkdtemp(prefix="zmb_xlsx_")
    paths = []
    try:
        for i, f in enumerate(files, start=1):
            raw = await f.read()
            if not raw.strip():
                return _err("空文件", "《%s》是空文件。" % (f.filename or i))
            if len(raw) > MAX_BYTES:
                return _err("文件过大", "《%s》超过 4MB。" % (f.filename or i))
            dst = os.path.join(tmpdir, "quote_%d.csv" % i)
            with open(dst, "wb") as w:
                w.write(raw)
            paths.append((_label_from_name(f.filename or "报价%d" % i), dst))
        return _xlsx_response(paths, template_key, scope)
    except WrongDocTypeError as e:
        return _err("文档类型不符", str(e), 400)
    except pipeline.HeaderMismatchError as e:
        return _err("表头不符", "%s 请确认为带「项目名称/数量/单价/合价」列的报价表。" % e, 400)
    except EmptyRowsError as e:
        return _err("没有解析到行项", str(e), 400)
    except Exception as e:  # noqa: BLE001
        return _err("导出失败", str(e), 500)
    finally:
        for _, p in paths:
            try:
                os.remove(p)
            except OSError:
                pass
        try:
            os.rmdir(tmpdir)
        except OSError:
            pass


@app.get("/inquiry", response_class=HTMLResponse)
def inquiry_page():
    """把真实单据的审计结论渲染成「可以直接发给报价方」的逐条问询。

    为什么要单独一屏：审计报告是给业主看的结论，问询单是给业主**拿去用**的动作。
    绝大多数人拿到结论后不知道第一句该说什么，这一屏就是那第一句。
    """
    path = os.path.join(pipeline.DATA, "real", "sh_zhuoguan_halfpack.csv")
    if not os.path.isfile(path):
        return _err("真实单据缺失", "仓库内未找到 data/real/sh_zhuoguan_halfpack.csv。", 404)
    try:
        onto, templates, rules, coverage = _load_rules()
        q = pipeline.build_line_index(pipeline.load_quote(path, "上海卓观 · 两房半包（真实单据）"), onto)
        res = pipeline.audit([q], onto, templates, rules, coverage,
                             template_key="old_house")[q["label"]]
        html = inquiry_mod.to_html(inquiry_mod.build(q, res))
    except Exception as e:  # noqa: BLE001
        return _err("问询单生成失败", str(e), 500)
    return HTMLResponse(html)


@app.get("/inquiry/demo", response_class=HTMLResponse)
def inquiry_demo():
    """三份样例的对照版问询单：把「贵司按根、别家按米」写进问句里。

    单份单据的问询单（/inquiry）只能问事实——「跨单口径」要对照才存在，
    所以这一屏跑一次三份样例的审计，再给 B 公司出问询单。
    样例里正好是 B 按「根」、A/C 按「米」，业主拿这句话去问，对方只能给折算关系。
    """
    paths = [(lb, os.path.join(pipeline.DATA, "samples", fn)) for lb, fn in SAMPLES]
    try:
        onto, templates, rules, coverage = _load_rules()
        quotes = [pipeline.build_line_index(pipeline.load_quote(p, lb), onto)
                  for lb, p in paths]
        full = pipeline.audit(quotes, onto, templates, rules, coverage,
                              template_key="old_house")
        target = quotes[1]  # B 公司（低价引流型）
        html = inquiry_mod.to_html(inquiry_mod.build(
            target, full[target["label"]], cross_unit=full.get("_cross_unit")))
    except Exception as e:  # noqa: BLE001
        return _err("样例问询单生成失败", str(e), 500)
    return HTMLResponse(html)


FOLLOWUP_FORM = """<form method="post" action="/followup" enctype="multipart/form-data"
 style="margin:16px 0 18px;padding:16px 18px;border:1px solid #E4E2DC;background:#fff">
<div style="font-size:13px;font-weight:600;margin-bottom:4px">核对你自己手上的两版</div>
<div style="font-size:12px;color:#8A877F;margin-bottom:10px">
第 1 份是原版、第 2 份是对方回的新版（CSV / XLSX）。只核同一项目、同一计价口径的两版，
不做跨公司比价。下方默认是仓库内的演示结果，上传后会替换成你的两版。</div>
<input type="file" name="files" accept=".csv,.xlsx" required style="font-size:12px">
<input type="file" name="files" accept=".csv,.xlsx" required style="font-size:12px;margin-left:8px">
<label style="font-size:12px;margin-left:10px">模板
<select name="template_key" style="font-size:12px">
<option value="old_house">老房翻新（39 项）</option>
<option value="new_house">新房毛坯（28 项）</option>
</select></label>
<button type="submit" style="margin-left:10px;padding:6px 16px;background:#002FA7;color:#fff;
 border:0;border-radius:2px;font-size:13px;cursor:pointer">核对</button>
</form>"""


@app.get("/followup", response_class=HTMLResponse)
def followup_page():
    """对方回了新版：逐条核对问询单里的问题到底改了没有。

    为什么单独一屏：新版报价回过来，人第一眼看的是总价。总价降了 6 百，
    可能是因为补了问的漏项、同时把别的单价调高了——不逐条对，看不出来。
    这一屏把「问的 19 条里改了几条」和「新版新加了什么」摆在同一张表上。
    """
    before = os.path.join(pipeline.DATA, "real", "feitai_budget.csv")
    after = os.path.join(pipeline.DATA, "samples", "feitai_change_demo.csv")
    for p in (before, after):
        if not os.path.isfile(p):
            return _err("演示数据缺失", "仓库内未找到 %s。" % os.path.basename(p), 404)
    try:
        onto, templates, rules, coverage = _load_rules()

        def one(path, label):
            q = pipeline.build_line_index(pipeline.load_quote(path, label), onto)
            r = pipeline.audit([q], onto, templates, rules, coverage,
                               template_key="old_house")[q["label"]]
            return q, r

        q1, r1 = one(before, "翡翠台 · 原版")
        q2, r2 = one(after, "翡翠台 · 变更版")
        result = followup_mod.check(inquiry_mod.build(q1, r1), q2, r2, before_res=r1)
    except Exception as e:  # noqa: BLE001
        return _err("问询落实核对失败", str(e), 500)
    return HTMLResponse(followup_mod.to_html(result, top_html=FOLLOWUP_FORM))


@app.post("/followup", response_class=HTMLResponse)
async def followup_upload(files: List[UploadFile] = File(default=None),
                          template_key: str = Form("old_house")):
    """用户自己的两版：原版 → 对方回的新版，逐条核对问询落实。

    校验链路与 /diff 一致（大小 / 空文件 / 文档守门 / 表头），不另造一套。
    模板由用户选：选错模板，漏项结论全错，所以做成显式参数而不是猜。
    """
    if not files or len(files) != 2:
        return _err("需要两份报价", "请选择 2 份：第 1 份原版、第 2 份对方回的新版。", 400)
    if template_key not in ("old_house", "new_house"):
        return _err("模板不合法", "template_key 只能是 old_house 或 new_house。", 400)
    tmp = tempfile.mkdtemp(prefix="zmb_followup_")
    paths = []
    try:
        for i, f in enumerate(files, start=1):
            raw = await f.read()
            if not raw.strip():
                return _err("空文件", "《%s》是空文件。" % (f.filename or i), 400)
            if len(raw) > MAX_BYTES:
                return _err("文件过大", "《%s》超过 4MB。" % (f.filename or i), 400)
            dst = os.path.join(tmp, "v%d%s" % (
                i, os.path.splitext(f.filename or "")[1] or ".csv"))
            with open(dst, "wb") as w:
                w.write(raw)
            paths.append((_label_from_name(f.filename or "第%d份" % i), dst))
        onto, templates, rules, coverage = _load_rules()
        for lb, p in paths:
            ok_f, info_f = doc_gate.gate_file(p, lb)
            if not ok_f:
                raise WrongDocTypeError(info_f["message"])

        def one(lb, p):
            q = pipeline.build_line_index(pipeline.load_quote(p, lb), onto)
            r = pipeline.audit([q], onto, templates, rules, coverage,
                               template_key=template_key)[q["label"]]
            return q, r

        q1, r1 = one(*paths[0])
        q2, r2 = one(*paths[1])
        result = followup_mod.check(inquiry_mod.build(q1, r1), q2, r2, before_res=r1)
        return HTMLResponse(followup_mod.to_html(result, top_html=FOLLOWUP_FORM))
    except WrongDocTypeError as e:
        return _err("文档类型不符", str(e), 400)
    except pipeline.HeaderMismatchError as e:
        return _err("表头不符", "%s 请确认为带「项目名称/数量/单价/合价」列的报价表。" % e, 400)
    except Exception as e:  # noqa: BLE001
        return _err("落实核对失败", str(e), 500)
    finally:
        for _, p in paths:
            try:
                os.remove(p)
            except OSError:
                pass
        try:
            os.rmdir(tmp)
        except OSError:
            pass


DIFF_FORM = """<form method="post" action="/diff" enctype="multipart/form-data"
 style="margin:22px 0 0;padding:16px 18px;border:1px solid #E4E2DC;background:#fff">
<div style="font-size:13px;font-weight:600;margin-bottom:4px">比你自己手上的两版</div>
<div style="font-size:12px;color:#8A877F;margin-bottom:10px">
  第 1 份是原版、第 2 份是变更后（CSV / XLSX）。只比同一项目、同一计价口径的两版，不做跨公司比价。</div>
<input type="file" name="files" accept=".csv,.xlsx" required style="font-size:12px">
<input type="file" name="files" accept=".csv,.xlsx" required style="font-size:12px;margin-left:8px">
<button type="submit" style="margin-left:10px;padding:6px 16px;background:#002FA7;color:#fff;
 border:0;border-radius:2px;font-size:13px;cursor:pointer">对比</button>
</form>"""


@app.get("/diff", response_class=HTMLResponse)
def diff_page():
    """两版对比：默认演示「真实单据 → 模拟变更」，也可上传两份自己比。

    为什么单独一屏：审计回答「这一份有没有问题」，业主在签约现场问的是
    「你上次报 15 万，这次怎么 17 万，多在哪」。两个问题必须分开答。
    """
    try:
        onto, _, _, _ = _load_rules()
        q1 = pipeline.build_line_index(
            pipeline.load_quote(os.path.join(pipeline.DATA, "real", "feitai_budget.csv"), "原报价"), onto)
        q2 = pipeline.build_line_index(
            pipeline.load_quote(os.path.join(pipeline.DATA, "samples", "feitai_change_demo.csv"), "变更后"), onto)
        d = diff_mod.diff(q1, q2)
    except Exception as e:  # noqa: BLE001
        return _err("对比演示生成失败", str(e), 500)
    banner = ("演示：第 2 份是第一份真实单据（翡翠台 123 行）上构造的 5 处模拟变更——"
              "补 2 项漏项、1 项涨价、1 项增量、删 1 项重复计价。不是真实结算单。")
    return HTMLResponse(diff_mod.to_html(d, banner=banner, top_html=DIFF_FORM))


@app.post("/diff", response_class=HTMLResponse)
async def diff_upload(files: List[UploadFile] = File(default=None)):
    if not files or len(files) != 2:
        return _err("需要两份报价", "请选择 2 份：第 1 份原版、第 2 份变更后。", 400)
    tmp = tempfile.mkdtemp(prefix="zmbdiff_")
    paths = []
    try:
        for i, f in enumerate(files, start=1):
            raw = await f.read()
            if not raw.strip():
                return _err("空文件", "《%s》是空文件。" % (f.filename or i), 400)
            if len(raw) > MAX_BYTES:
                return _err("文件过大", "《%s》超过 4MB。" % (f.filename or i), 400)
            dst = os.path.join(tmp, "v%d%s" % (i, os.path.splitext(f.filename or "")[1] or ".csv"))
            with open(dst, "wb") as w:
                w.write(raw)
            paths.append((_label_from_name(f.filename or "第%d份" % i), dst))
        onto, _, _, _ = _load_rules()
        for lb, p in paths:
            ok_f, info_f = doc_gate.gate_file(p, lb)
            if not ok_f:
                raise WrongDocTypeError(info_f["message"])
        q1 = pipeline.build_line_index(pipeline.load_quote(paths[0][1], paths[0][0]), onto)
        q2 = pipeline.build_line_index(pipeline.load_quote(paths[1][1], paths[1][0]), onto)
        return HTMLResponse(diff_mod.to_html(diff_mod.diff(q1, q2), top_html=DIFF_FORM))
    except WrongDocTypeError as e:
        return _err("文档类型不符", str(e), 400)
    except pipeline.HeaderMismatchError as e:
        return _err("表头不符", "%s 请确认为带「项目名称/数量/单价/合价」列的报价表。" % e, 400)
    except Exception as e:  # noqa: BLE001
        return _err("对比失败", str(e), 500)
    finally:
        for _, p in paths:
            try:
                os.remove(p)
            except OSError:
                pass
        try:
            os.rmdir(tmp)
        except OSError:
            pass


@app.get("/perceive", response_class=HTMLResponse)
def perceive_page():
    """图片识别入口：一张单据照片 → 结构化行项 → 审计报告。"""
    has_key = bool(os.environ.get("MAAS_API_KEY"))
    has_ak = bool(os.environ.get("HW_ACCESS_KEY") and os.environ.get("HW_SECRET_KEY"))
    prov = os.environ.get("PERCEPTION_PROVIDER", "auto")
    has_vision, vision_info = vision_ocr.available()
    status = []
    status.append('<li>provider：<b>%s</b>（auto 时按 Key 选择；可设 <code>PERCEPTION_PROVIDER</code> 强制）</li>' % prov)
    status.append('<li>MaaS API Key：%s</li>' % ("已配置" if has_key else
                  '未配置 — <code>export MAAS_API_KEY=...</code>'))
    status.append('<li>华为云 AK/SK（表格识别用）：%s</li>' % ("已配置" if has_ak else
                  '未配置 — 需同时设置 <code>HW_ACCESS_KEY</code> / <code>HW_SECRET_KEY</code>'))
    status.append('<li>本地 OCR（macOS Vision）：%s</li>' % (
        "可用 — 图片不出本机，识别结果会过 critic" if has_vision else
        "不可用 — %s" % vision_info))
    html = _page("""
<h1>图片识别 → 审计</h1>
<p class="lead">上传一张报价单照片。识别只负责「看见」，判断仍走规则——两层分开，才能分开定位错误。</p>
<form method="post" action="/perceive" enctype="multipart/form-data" class="card">
  <input type="file" name="file"
         accept=".csv,.tsv,.xlsx,.xlsm,.pdf,image/jpeg,image/png" required>
  <p class="note" style="margin:6px 0 0">优先用装企给的 PDF / Excel（不需要 OCR，也不会认错字）；
  手机照片可走本地 macOS Vision（图片不出本机）或云端视觉模型。识别结果会先过 critic：
  缺字段、量价不符、低置信的行会标为「需核对」，不会被当成确定结论。</p>
  <label>模板
    <select name="template_key">
      <option value="new_house">新房毛坯（28 项）</option>
      <option value="old_house">老房翻新（39 项）</option>
    </select>
  </label>
  <label>报价范围
    <select name="scope">
      <option value="full">全包（含主材）</option>
      <option value="base">基础施工（主材另计）</option>
    </select>
  </label>
  <button class="btn" type="submit">识别并审计</button>
</form>
<h2>感知层状态</h2>
<ul class="plain">""" + "".join(status) + """</ul>
<p class="note">识别失败会<strong>明确报错</strong>，不会拿空结果往下走——空表被判成「没有漏项」是最危险的失败方式。</p>
""")
    return HTMLResponse(html)


@app.post("/perceive", response_class=HTMLResponse)
async def perceive_run(request: Request, file: UploadFile = File(default=None),
                       template_key: str = Form(default="new_house"),
                       scope: str = Form(default="full")):
    ok, _ = _rate_ok(request, "perceive")
    if not ok:
        return _err("请求过于频繁", "每个来源每分钟最多 60 次识别。请稍后再试。", 429)
    if file is None or not (file.filename or "").strip():
        return _err("没有收到文件", "请选择一张 JPG/PNG 报价单照片。", 400)
    raw = await file.read()
    if not raw:
        return _err("文件为空", "上传的图片没有内容。", 400)
    if len(raw) > 10 * 1024 * 1024:
        return _err("图片过大", "请压缩到 10MB 以内（华为云 OCR 单张上限）。", 400)

    ext = os.path.splitext(file.filename)[1].lower()
    # 结构化文件不需要任何云服务；只有图片才要视觉模型
    structured = ext in (".csv", ".tsv", ".xlsx", ".xlsm", ".pdf")
    if structured:
        provider = "file"
    else:
        provider = os.environ.get("PERCEPTION_PROVIDER", "").strip()
        if not provider or provider == "auto":
            if os.environ.get("MAAS_API_KEY"):
                provider = "maas"
            elif os.environ.get("HW_ACCESS_KEY") and os.environ.get("HW_SECRET_KEY"):
                provider = "ocr_table"
            elif vision_ocr.available()[0]:
                provider = "vision"
            else:
                provider = "maas"

    todo = tempfile.NamedTemporaryFile(suffix=ext or ".jpg", delete=False)
    try:
        todo.write(raw)
        todo.close()
        try:
            rows, meta, report = perception.perceive(todo.name, provider=provider)
        except perception.PerceptionError as e:
            hint = "" if structured else (
                "<br><br>照片识别需要开通服务，二选一：<br>"
                "① 华为云控制台开通「文字识别 OCR → 表格识别」（2 分钟，无需新 Key）<br>"
                "② ModelArts Studio 领免费额度并创建 API Key（<code>MAAS_API_KEY</code>）<br>"
                "如果手上有装企发的 PDF/Excel，直接上传那个更快也更准。")
            return _err("感知层未能完成识别", "%s%s<br><br>"
                        "失败不等于「这张单据没问题」——因此不输出任何审计结论。"
                        % (e, hint), 503)
        if not rows:
            return _err("没有识别出行项", "识别成功但行项为空，请检查图片是否清晰、是否是报价表。", 400)
        if meta.get("provider") == "vision" and (report.get("review_ratio") or 0) > 0.35:
            return _vision_draft_page(rows, report, _rows_csv_text(rows),
                                      file.filename or "vision", template_key, scope)

        csv_path = todo.name + ".csv"
        perception.write_csv(rows, csv_path)
        html, res, quotes = _audit([(_label_from_name(file.filename), csv_path)],
                                  template_key=template_key, scope=scope)
        # 横幅口径必须与审计主体一致：用 load_quote 分层后的「工序明细合计」，
        # 不能用识别层的全行求和——否则汇总行（费用总合计/总价）会被再加一遍。
        q0 = quotes[0]
        rec = q0.get("recon") or {}
        n_sum = len(q0.get("summary_rows") or [])
    except WrongDocTypeError as e:
        # /perceive 是单文件入口，守门失败必须明确报 400 并说明「为什么这不是报价单」，
        # 不能让它冒泡成 500（2026-11-05 实测发现的真实缺陷）。
        return _err("文档类型不符", str(e), 400)
    except pipeline.HeaderMismatchError as e:
        return _err("表头不符", "%s 请确认为带「项目名称/数量/单价/合价」列的报价表。" % e, 400)
    except EmptyRowsError as e:
        return _err("没有解析到行项", str(e), 400)
    finally:
        for f_ in (todo.name, todo.name + ".csv"):
            try:
                os.unlink(f_)
            except OSError:
                pass

    banner = ('<div class="banner">识别：<b>%s</b> ｜ 工序行 <b>%d</b>%s ｜ '
              '算术自校验 <b>%d</b> 行%s ｜ 明细合计 <b>%s</b>%s%s</div>') % (
        meta.get("provider"), len(q0.get("rows") or []),
        ("（另有 %d 行汇总/取费，已单独对账）" % n_sum) if n_sum else "",
        report.get("arith_checked") or 0,
        "全部通过" if not report.get("arith_bad") else
        "，<b style='color:#B33A2B'>%d 行不符（须回原图核对）</b>" % len(report["arith_bad"]),
        fmt_money(q0.get("total")),
        ("；印刷合计 %s，差 %s" % (fmt_money(rec["grand"]["amount"]), fmt_money(rec["diff"])))
        if rec.get("grand") else "",
        "<br><b style='color:#B33A2B'>注意：本次用的是固定样例（mock），不是对这张图的识别结果。</b>"
        if meta.get("fixture") else "")
    if meta.get("provider") == "vision":
        banner += ('<br><b>本地 OCR（macOS Vision）</b>：%d 个文本框 → %d 行；'
                   'critic 通过 %d 行，需核对 %d 行（%.0f%%）；量价不符 %d 行。'
                   '<br><b style="color:#F0B429">这是识别草稿：中文可能有错字，'
                   '审计结论请以校对后的 CSV 为准。</b>') % (
            report.get("boxes") or 0, report.get("rows") or 0,
            report.get("accepted") or 0, report.get("review") or 0,
            (report.get("review_ratio") or 0) * 100,
            len(report.get("arith_bad") or []))
    if "<body>" in html:
        html = html.replace("<body>", "<body>" + banner, 1)
    aid = _persist(quotes, res, source=("vision" if meta.get("provider") == "vision" else "perceive"),
                   docs=[file.filename or "photo"])
    html = _decorate(html, quotes, res, aid, "图片/文件识别")
    return HTMLResponse(html)


@app.post("/audit", response_class=HTMLResponse)
async def audit(request: Request, files: List[UploadFile] = File(default=None),
                template_key: str = Form(default="old_house"),
                scope: str = Form(default="full")):
    ok, remain = _rate_ok(request)
    if not ok:
        return _err("请求过于频繁", "每个来源每分钟最多 60 次审计。请稍后再试。", 429)
    if not files:
        return _err("没有收到文件", "请选择 2–4 份 CSV 报价单。")
    if len(files) < MIN_FILES:
        return _err("至少需要 2 份报价单",
                    "单份单据无法做跨单对齐——「漏项」和「重复」都是相对的。请再上传至少 1 份。")
    if len(files) > MAX_FILES:
        return _err("最多 4 份", "一次最多比较 4 家，超过请分批。")

    tmp = tempfile.mkdtemp(prefix="zmb_")
    paths = []
    try:
        for i, f in enumerate(files, start=1):
            raw = await f.read()
            if len(raw) > MAX_BYTES:
                return _err("文件过大", "《%s》超过 4MB。" % (f.filename or i))
            if not raw.strip():
                return _err("空文件", "《%s》是空文件。" % (f.filename or i))
            dst = os.path.join(tmp, "quote_%d.csv" % i)
            with open(dst, "wb") as w:
                w.write(raw)
            paths.append((_label_from_name(f.filename or "报价%d" % i), dst))
        html, res, quotes = _audit(paths, template_key=template_key, scope=scope)
        aid = _persist(quotes, res, source="upload",
                       docs=[f.filename or "quote_%d" % (i + 1) for i, f in enumerate(files)])
        html = _decorate(html, quotes, res, aid, "上传单据")
        return HTMLResponse(html)
    except WrongDocTypeError as e:
        return _err("文档类型不符", str(e), 400)
    except pipeline.HeaderMismatchError as e:
        return _err("表头不符", "%s 请确认为带「项目名称/数量/单价/合价」列的报价表。" % e, 400)
    except EmptyRowsError as e:
        return _err("没有解析到行项", str(e), 400)
    except Exception as e:  # noqa: BLE001
        return _err("审计失败", "解析或计算过程中出错：%s" % e, 500)
    finally:
        for _, p in paths:
            try:
                os.remove(p)
            except OSError:
                pass
        try:
            os.rmdir(tmp)
        except OSError:
            pass


@app.get("/review", response_class=HTMLResponse)
def review():
    """解析校对台：低置信度单元格标黄、可直接改、导出 CSV。

    真实链路由 MaaS Qwen-VL 返回字段级置信度；此处用样例数据展示同一套校对交互。
    """
    onto, _, _, _ = _load_rules()
    paths = [(lb, os.path.join(pipeline.DATA, "samples", fn)) for lb, fn in SAMPLES]
    quotes = [pipeline.build_line_index(pipeline.load_quote(p, lb), onto) for lb, p in paths]
    data = pipeline.build_review_data(quotes, onto)
    tpl_path = os.path.join(BASE, "web", "review.html")
    with open(tpl_path, encoding="utf-8") as f:
        html = f.read()
    inline = "<script>window.REVIEW_DATA = %s;</script>" % json.dumps(data, ensure_ascii=False)
    html = html.replace('<script src="../out/review_data.js"></script>', inline)
    if "<body>" in html:
        html = html.replace("<body>", "<body>" + NAV, 1)
    return HTMLResponse(html)


@app.get("/api/demo")
def api_demo():
    """给答辩/联调用的 JSON 版本（不含 HTML）。"""
    paths = [(lb, os.path.join(pipeline.DATA, "samples", fn)) for lb, fn in SAMPLES]
    _, res, quotes = _audit(paths)
    out = {}
    for q in quotes:
        r = res[q["label"]]
        out[q["label"]] = {
            "total": q["total"],
            "missing": len(r["missing"]),
            "missing_cost": r["missing_cost"],
            "duplicates": len(r["duplicates"]),
            "duplicate_cut": r["duplicate_cut"],
            "effective": r["effective"],
            "risks": len(r["risks"]),
            "exposure": [x["low"] for x in r["exposure"]] + [x["high"] for x in r["exposure"]],
        }
    return out


@app.get("/api/audits")
def api_audits(limit: int = 50):
    return JSONResponse({"items": store_mod.get_store().list_audits(max(1, min(limit, 200)))})


@app.get("/api/audits/{audit_id}")
def api_audit_detail(audit_id: str):
    rec = store_mod.get_store().get_audit(audit_id)
    if not rec:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(rec)


@app.get("/history", response_class=HTMLResponse)
def history():
    """审计历史：证明 RDS 真的在存业务数据，而不是只开了一台库。"""
    rows = store_mod.get_store().list_audits(100)
    tr = []
    for r in rows:
        tr.append(
            "<tr><td><a href=\"/report/%s\">%s</a></td><td>%s</td><td>%s</td>"
            "<td>%d</td><td>%s</td><td>%s</td><td><code>%s</code></td></tr>" % (
                html_mod.escape(r.get("id") or ""), html_mod.escape(r.get("id") or ""),
                html_mod.escape(str(r.get("created_at") or "")),
                html_mod.escape(str(r.get("source") or "")),
                int(r.get("doc_count") or 0),
                fmt_money(r.get("total")), fmt_money(r.get("effective")),
                html_mod.escape(str(r.get("fingerprint") or ""))))
    body = """
<h1>审计历史</h1>
<p class="lead">每一次审计的摘要都落库（PostgreSQL / SQLite）。这是「关联 RDS」的业务证据：
不是开了一台数据库，而是数据真的写进去了、能查、能回看。</p>
<table class="s"><tr><th>编号</th><th>时间</th><th>来源</th><th>单据数</th>
<th>总价</th><th>真实成本</th><th>指纹</th></tr>%s</table>
<p class="note">只存摘要与结构化判定，不存原始单据文件。数据库状态见 <a href="/health">/health</a>。</p>
""" % ("".join(tr) or "<tr><td colspan=7>还没有记录，先跑一次 <a href=\"/demo\">样例</a>。</td></tr>")
    return _sub_page(body)


@app.get("/report/{audit_id}", response_class=HTMLResponse)
def report(audit_id: str):
    rec = store_mod.get_store().get_audit(audit_id)
    if not rec:
        return _err("没有这条记录", "审计编号不存在，或数据库已重置。", 404)
    docs = rec.get("docs") or []
    payload = rec.get("payload") or {}
    fbs = store_mod.get_store().feedback_for(audit_id)
    qual = (payload or {}).get("quality") or {}
    rows = []
    for d in payload.get("documents") or []:
        ms = d.get("missing") or []
        rows.append("<tr><td>%s</td><td>%s</td><td>%d</td><td>%s</td></tr>" % (
            html_mod.escape(str(d.get("label"))), fmt_money(d.get("total")), len(ms),
            "、".join(html_mod.escape(str(m.get("name"))) for m in ms[:6])))
    body = """
<h1>审计记录 %s</h1>
<p class="lead">%s ｜ 来源 %s ｜ 模板 %s / 范围 %s ｜ 判定指纹 <code>%s</code></p>
<div class="qgate" style="border:1px solid #E4E2DC;border-left:4px solid #0A7A42;padding:12px 16px;margin:14px 0;font-size:13px;color:#56606C">
质量门：对齐 <b>%s/%s</b>（%s）｜ 算术 <b>%s</b> 行，<b>%s</b> 不符 ｜ 指纹 <code>%s</code>
</div>
<table class="s"><tr><th>单据</th><th>报价总价</th><th>漏项数</th><th>主要漏项</th></tr>
%s</table>
<h2>用户反馈（%d）</h2>
%s
<p class="note">完整可交互报告请重新跑一次；历史记录只保留摘要，避免把原始单据长期留在数据库里。</p>
""" % (html_mod.escape(audit_id), html_mod.escape(str(rec.get("created_at"))),
       html_mod.escape(str(rec.get("source"))), html_mod.escape(str(rec.get("template_key"))),
       html_mod.escape(str(rec.get("scope"))), html_mod.escape(str(rec.get("fingerprint"))),
       qual.get("aligned", "—"), qual.get("rows", "—"),
       ("%.1f%%" % (qual.get("align_rate", 0) * 100)) if qual else "—",
       qual.get("arith_rows", "—"), qual.get("arith_bad", "—"),
       html_mod.escape(str(qual.get("fingerprint", rec.get("fingerprint") or ""))),
       "".join(rows) or "<tr><td colspan=4>无单据明细</td></tr>",
       len(fbs),
       "".join("<p class=\"note\">%s ｜ %s ｜ %s</p>" % (
           html_mod.escape(str(f.get("created_at") or "")),
           html_mod.escape(str(f.get("role") or "")),
           html_mod.escape(str(f.get("note") or ""))) for f in fbs) or "<p class=\"note\">暂无</p>")
    return _sub_page(body)


@app.post("/api/audit")
async def api_audit(request: Request, files: List[UploadFile] = File(default=None),
                    template_key: str = Form(default="old_house"),
                    scope: str = Form(default="full")):
    """程序化接口：返回 JSON 摘要（供联调、批量评测、以及答辩现场 curl 复现）。"""
    ok, _ = _rate_ok(request, "api")
    if not ok:
        return JSONResponse({"error": "rate limited"}, status_code=429)
    if not files or len(files) < 2:
        return JSONResponse({"error": "need at least 2 files"}, status_code=400)
    tmp = tempfile.mkdtemp(prefix="zmb_api_")
    paths = []
    try:
        for i, f in enumerate(files, start=1):
            raw = await f.read()
            if not raw.strip() or len(raw) > MAX_BYTES:
                return JSONResponse({"error": "empty or too large: %s" % f.filename}, status_code=400)
            dst = os.path.join(tmp, "q%d.csv" % i)
            with open(dst, "wb") as w:
                w.write(raw)
            paths.append((_label_from_name(f.filename or "quote_%d" % i), dst))
        _, res, quotes = _audit(paths, template_key=template_key, scope=scope)
        aid = _persist(quotes, res, source="api")
        facts = llm_mod.build_facts(quotes, res)
        return JSONResponse({
            "audit_id": aid,
            "fingerprint": _fingerprint(res),
            "documents": facts["documents"],
            "ai": llm_mod.get_client().explain(facts, cache=cache_mod.get_cache()),
        })
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": str(e)}, status_code=500)
    finally:
        for _, p in paths:
            try:
                os.remove(p)
            except OSError:
                pass
        try:
            os.rmdir(tmp)
        except OSError:
            pass


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
