# -*- coding: utf-8 -*-
"""装明白 · 在线审计 Demo（FastAPI 外壳，引擎零依赖）

本地运行：
    uvicorn main:app --host 0.0.0.0 --port 8000 --reload

设计边界：
- 本服务只做「判断」与「表达」，不做 OCR —— 照片/PDF 的解析由上游 MaaS Qwen-VL 完成，
  解析结果落成与 data/samples/*.csv 相同的结构，因此解析链路可替换、可对照。
- 不判断贵不贵，只判断漏没漏、重没重；低置信度结论不进核减金额。
"""
import io
import json
import os
import sys
import tempfile
import zipfile
from typing import List

from fastapi import FastAPI, File, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

BASE = os.path.dirname(os.path.abspath(__file__))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

from src import pipeline  # noqa: E402

app = FastAPI(title="装明白 · 可计算审计", version="0.3.0")

MAX_FILES = 4
MIN_FILES = 2
MAX_BYTES = 4 * 1024 * 1024
SAMPLES = [
    ("A 公司（规范型）", "company_a.csv"),
    ("B 公司（低价引流型）", "company_b.csv"),
    ("C 公司（高价项全型）", "company_c.csv"),
]

CSS = """
:root{--paper:#F6F4EF;--ink:#12181F;--ink2:#56606C;--amber:#C8791E;--line:#DCD6C8;--red:#B33A2B}
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
.card{background:#fff;border:1px solid #E4DED0;border-radius:4px;padding:30px}
.card h2{font-size:20px;margin:0 0 6px}
.card p{font-size:15px;color:var(--ink2);line-height:1.7;margin:0 0 18px}
.drop{border:1.5px dashed #CFC7B4;border-radius:4px;padding:26px;text-align:center;background:#FBFAF6}
.drop input{display:block;margin:0 auto;font-size:14px;color:var(--ink2)}
.btn{display:inline-block;border:0;cursor:pointer;background:var(--ink);color:#fff;
 font-size:17px;font-weight:600;padding:14px 30px;border-radius:3px;margin-top:20px;font-family:inherit}
.btn:hover{background:#1E2A36}
.btn.ghost{background:transparent;color:var(--ink);border:1.5px solid var(--ink);padding:12px 24px}
.meta{font-size:13px;color:#8A8578;margin-top:12px;line-height:1.7}
ol.steps{padding-left:20px;margin:0;color:var(--ink2);font-size:15px;line-height:2}
table.s{margin-top:22px;border-collapse:collapse;width:100%;font-size:14px}
table.s td{border-bottom:1px solid var(--line);padding:9px 0;color:var(--ink2)}
table.s td:first-child{color:var(--ink);font-weight:600}
.err{background:#fff;border-left:4px solid var(--red);padding:22px 26px;margin-top:30px}
.err h2{margin:0 0 8px;font-size:20px}
.nav{position:sticky;top:0;z-index:9;background:rgba(246,244,239,.95);border-bottom:1px solid var(--line);
 padding:12px 32px;font-size:14px;color:var(--ink2);backdrop-filter:blur(6px)}
.nav b{color:var(--ink)}
.nav a{margin-right:18px}
.nav .right{float:right}
"""


class EmptyRowsError(ValueError):
    """CSV 解析后没有行项（只有表头或空数据）"""
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
        <button class="btn" type="submit">开始审计</button>
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
</div></body></html>"""

ERR = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>输入有误</title>
<style>__CSS__</style></head><body><div class="wrap">
<div class="kicker">装明白 · 可计算审计</div><h1 style="font-size:40px">这次没跑成</h1>
<div class="err"><h2>__TITLE__</h2><p class="lead" style="font-size:16px">__DETAIL__</p></div>
<p style="margin-top:30px"><a href="/">返回上传页</a>　·　<a href="/demo">用样例跑一次</a></p>
</div></body></html>"""

NAV = """<div class="nav"><b>装明白 · 可计算审计</b>
<a href="/" style="margin-left:18px">重新上传</a><a href="/demo">样例演示</a><a href="/real">真实单据</a><a href="/review">解析校对</a>
<span class="right">浏览器 Ctrl/⌘+P 可另存为 PDF · <a href="/health">健康检查</a></span></div>"""


def _page() -> str:
    return PAGE.replace("__CSS__", CSS)


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


def _audit(paths):
    """paths: [(label, csv_path)] -> (html, res, quotes)"""
    onto, templates, rules, coverage = _load_rules()
    quotes = [pipeline.build_line_index(pipeline.load_quote(p, lb), onto) for lb, p in paths]
    for q in quotes:
        if not q["rows"]:
            raise EmptyRowsError("《%s》没有解析到任何行项，请检查表头是否为 "
                                 "项目名称/规格工艺/单位/数量/单价/合价。" % q["label"])
    res = pipeline.audit(quotes, onto, templates, rules, coverage)
    html = pipeline.build_html(quotes, res)
    if "<body>" in html:
        html = html.replace("<body>", "<body>" + NAV, 1)
    return html, res, quotes


def _label_from_name(name: str) -> str:
    stem = os.path.splitext(os.path.basename(name or ""))[0].strip()
    return stem[:24] or "未命名报价"


@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(_page())


@app.get("/health")
def health():
    return JSONResponse({"status": "ok", "service": "zhuangmingbai-audit", "version": app.version})


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
        html, _, _ = _audit(paths)
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
        html, _, _ = _audit([("上海卓观 · 两房半包（真实单据）", path)])
    except Exception as e:  # noqa: BLE001
        return _err("真实单据跑失败", str(e), 500)
    return HTMLResponse(html)


@app.post("/audit", response_class=HTMLResponse)
async def audit(files: List[UploadFile] = File(default=None)):
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
        html, _, _ = _audit(paths)
        return HTMLResponse(html)
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
