# -*- coding: utf-8 -*-
"""生成「审计回放」单文件页：评委不装环境、不跑命令，也能点开看每条结论的证据。

为什么做这个
------------
赛题要求「通过可视化 Demo 页面展示核心功能与交互流程」，但可交互服务要么跑在
本机（评委打不开），要么挂在 72h 就失效的隧道上。静态复验页解决了「看得到」，
解决不了「点得动」——评委看到的仍然是我们挑选过的十页。

这一页把 5 份真实单据的**引擎输出原样**（漏项/重复/风险/对账 + 全部行项）嵌进单文件，
点一条结论就高亮它指向的原表行号。它不重新计算，只回放：
数据由 commit 里的同一套规则生成，页面底部给复现命令。

口径：**预计算回放，不是实时引擎**。材料里必须这么写，不能包装成「在线 AI」。

用法：
    python3 scripts/build_replay.py --out ../国奖提交包/06_开源与部署/装明白_审计回放.html
"""
import argparse
import datetime
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import pipeline as P  # noqa: E402

DOCS = [
    ("feitai_budget.csv", "上海翡翠台 · 老房全案", "old_house"),
    ("guijiao_124_basic.csv", "124㎡ · 基础报价", "new_house"),
    ("sh_zhuoguan_halfpack.csv", "上海卓观 · 两房半包", "old_house"),
    ("huaxibeian_newhouse.csv", "花溪北岸 · 新房 5 页", "new_house"),
    ("szxinxin_clinic_pdf.csv", "深圳新新 · 诊所工装", "new_house"),
]


def _lines_of(x):
    """把结论里的行号解析成「纯数字、去重」的列表。

    真实单据有两个坑（都在深圳新新那份 PDF 上撞到）：
    1. 序号按分区重置，1~5 会在 19 个分区里各出现一次 —— 行号本身不唯一；
    2. R8 聚合结论可能写成「3,3,3,2,4,2…」这种**节选**，带省略号。
    所以这里只取纯数字，并把「是否节选」交给调用方标注，绝不假装精确。
    """
    raw = x.get("lines")
    if raw:
        toks = [str(v) for v in raw]
        truncated = False
    else:
        s = x.get("line")
        if s is None:
            return [], ""
        toks = list(str(s).split(","))
        truncated = ("…" in str(s)) or ("..." in str(s))
    out = []
    for p in toks:
        p = p.strip()
        if p.isdigit() and p not in out:
            out.append(p)
    note = "原结论只保留部分行号（节选）" if truncated else ""
    return out, note


def _commit():
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                              capture_output=True, text=True).stdout.strip() or "unknown"
    except OSError:
        return "unknown"


def build(docs=DOCS):
    onto = P.Ontology(os.path.join(ROOT, "data", "ontology_v0.json"))
    with open(os.path.join(ROOT, "data", "templates_v0.json"), encoding="utf-8") as f:
        tpl = json.load(f)
    with open(os.path.join(ROOT, "data", "rules_v0.json"), encoding="utf-8") as f:
        rules = json.load(f)
    with open(os.path.join(ROOT, "data", "coverage_v0.json"), encoding="utf-8") as f:
        cov = json.load(f)

    out = []
    for fn, label, tk in docs:
        q = P.build_line_index(P.load_quote(os.path.join(ROOT, "data", "real", fn), label), onto)
        r = P.audit([q], onto, tpl, rules, cov, template_key=tk)[label]

        missing = [{
            "kind": "missing", "title": m.get("concept") or "",
            "tier": m.get("tier"), "ref": m.get("ref_amount"),
            "basis": (m.get("evidence") or {}).get("basis", ""),
            "formula": (m.get("evidence") or {}).get("formula", ""),
            "lines": [], "line_note": "",
        } for m in r["missing"]]

        dups = [{
            "kind": "dup", "title": d.get("concept") or "",
            "tier": d.get("tier"), "amount": d.get("amount"),
            "basis": (d.get("evidence") or {}).get("basis", ""),
            "formula": (d.get("evidence") or {}).get("formula", ""),
            "lines": _lines_of(d)[0], "line_note": _lines_of(d)[1],
        } for d in r["duplicates"]]

        def risk_of(x):
            ln, note = _lines_of(x)
            if x.get("rule") == "R7_split_billing":
                ln, note = ln[:6], "行号为分区内序号（该单据按分区重新编号），不逐行高亮"
            return {
                "kind": "risk", "rule": x.get("rule"), "title": x.get("concept") or x.get("issue") or "",
                "issue": x.get("issue") or "", "tier": x.get("tier"), "amount": x.get("amount"),
                "basis": (x.get("evidence") or {}).get("basis", ""),
                "formula": (x.get("evidence") or {}).get("formula", ""),
                "lines": ln, "line_note": note,
                "can_highlight": x.get("rule") != "R7_split_billing",
            }
        risks = [risk_of(x) for x in r["risks"]]

        def brief(row, summary=False):
            return {
                "no": str(row.get("no") or ""), "name": row.get("name") or "",
                "spec": row.get("spec") or "", "unit": row.get("unit") or "",
                "qty": row.get("qty"), "price": row.get("price"), "amount": row.get("amount"),
                "cid": row.get("cid"), "conf": row.get("align_conf"), "summary": summary,
            }

        # 汇总/取费行单列：R13 对账指向的正是这类行，若不进表，点「指向行 130」会落空
        lines = [brief(row) for row in q["rows"]]
        lines += [brief(row, True) for row in (q.get("summary_rows") or [])]

        matched = sum(1 for row in q["rows"] if row.get("cid"))
        rec = r.get("recon") or {}
        out.append({
            "id": fn, "label": label, "file": fn, "template": tk, "scope": "full",
            "rows": len(lines), "detail_rows": len(q["rows"]),
            "summary_rows": len(q.get("summary_rows") or []),
            "total": q["total"], "matched": matched,
            "align_rate": round(matched / max(1, len(q["rows"])), 4),
            "dup_no": len({str(row.get("no") or "") for row in q["rows"]}) < len(q["rows"]),
            "missing": missing, "duplicates": dups, "risks": risks, "lines": lines,
            "recon": {k: rec.get(k) for k in ("detail_sum", "fee_sum", "grand", "diff", "ok")},
            "missing_cost": r.get("missing_cost"), "duplicate_cut": r.get("duplicate_cut"),
        })
    return {
        "commit": _commit(),
        "generated": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "docs": out,
    }


TPL = r"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>装明白 · 审计回放（5 份真实单据）</title>
<style>
:root{--ink:#0A0A0A;--ink2:#3F4650;--muted:#7B7B74;--paper:#FAFAF8;--card:#fff;
--ikb:#002FA7;--hair:#E4E2DC;--red:#B33A2B;--amber:#B07A22;--green:#2F7A4F}
*{box-sizing:border-box}
html,body{height:100%}
body{margin:0;background:var(--paper);color:var(--ink);font:14px/1.6 -apple-system,"PingFang SC",
"Hiragino Sans GB","Microsoft YaHei",sans-serif;-webkit-font-smoothing:antialiased}
.top{display:flex;justify-content:space-between;align-items:baseline;padding:14px 26px;
border-bottom:1px solid var(--hair);background:#fff}
.brand{font-weight:700;letter-spacing:.06em;font-size:14px}
.brand small{font-weight:400;color:var(--muted);letter-spacing:0;margin-left:10px;font-size:12px}
.top .r{font-size:12px;color:var(--muted)}
.note{padding:9px 26px;background:#F1F3F9;border-bottom:1px solid var(--hair);font-size:12.5px;color:#33405C}
.note b{color:var(--ikb)}
.layout{display:flex;height:calc(100vh - 116px);min-height:520px}
.side{width:268px;border-right:1px solid var(--hair);overflow:auto;background:#fff}
.side .h{font-size:11px;letter-spacing:.08em;color:var(--muted);padding:14px 18px 6px}
.doc{display:block;width:100%;text-align:left;border:0;background:none;cursor:pointer;
padding:11px 18px;border-left:3px solid transparent;font:inherit;color:inherit}
.doc:hover{background:#F7F7F3}
.doc.on{border-left-color:var(--ikb);background:#F1F3F9}
.doc .t{font-size:13.5px;font-weight:600}
.doc .m{font-size:11.5px;color:var(--muted);margin-top:2px}
.main{flex:1;overflow:auto;padding:22px 30px 60px}
.h1{font-size:22px;font-weight:600;margin:0 0 2px;letter-spacing:-.01em}
.sub{color:var(--muted);font-size:12.5px}
.stats{display:flex;gap:30px;margin:18px 0 6px;flex-wrap:wrap}
.stat .k{font-size:11px;color:var(--muted);letter-spacing:.05em}
.stat .v{font-size:25px;font-variant-numeric:tabular-nums}
.tabs{display:flex;gap:8px;margin:20px 0 8px;border-bottom:1px solid var(--hair);padding-bottom:8px}
.tab{border:1px solid var(--hair);background:#fff;padding:4px 12px;border-radius:2px;font-size:12.5px;
cursor:pointer;font-family:inherit;color:var(--ink2)}
.tab.on{background:var(--ikb);color:#fff;border-color:var(--ikb)}
.tab .n{opacity:.65;margin-left:5px}
.list{display:grid;grid-template-columns:1fr;gap:8px;margin-top:10px}
.f{border:1px solid var(--hair);background:var(--card);padding:10px 12px;cursor:pointer;
border-left:3px solid var(--hair)}
.f:hover{border-color:#C9C6BE}
.f.on{border-left-color:var(--ikb);background:#FBFCFF}
.f .row1{display:flex;justify-content:space-between;gap:12px;align-items:baseline}
.f .t{font-size:13.5px;font-weight:600}
.f .amt{font-variant-numeric:tabular-nums;color:var(--red);font-size:13px;white-space:nowrap}
.f .meta{font-size:11.5px;color:var(--muted);margin-top:3px}
.f .detail{display:none;margin-top:9px;padding-top:9px;border-top:1px dashed var(--hair);
font-size:12px;color:var(--ink2);line-height:1.75}
.f.on .detail{display:block}
.f .detail b{font-weight:600;color:var(--ink)}
.badge{display:inline-block;font-size:10.5px;padding:1px 6px;border-radius:9px;margin-right:6px;
border:1px solid var(--hair);color:var(--muted)}
.badge.auto{color:var(--green);border-color:#CFE2D6;background:#F3F8F4}
.badge.review{color:var(--amber);border-color:#EADFC8;background:#FBF7EE}
table{width:100%;border-collapse:collapse;font-size:12.5px;margin-top:8px}
th{text-align:left;font-weight:500;color:var(--muted);font-size:11px;letter-spacing:.04em;
padding:6px 7px;border-bottom:1px solid var(--hair);position:sticky;top:0;background:var(--paper);z-index:1}
td{padding:5px 7px;border-bottom:1px solid #F2F0EB;vertical-align:top}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
tr.sep td{background:#F4F3EE;color:var(--muted);font-size:11px;letter-spacing:.04em;padding:5px 7px}
tr.hl{background:#FFF9EA}
tr.sel{background:#EAF0FF!important;box-shadow:inset 3px 0 0 var(--ikb)}
.dot{display:inline-block;width:6px;height:6px;border-radius:50%;margin-right:5px;vertical-align:middle}
.dot.m{background:var(--red)} .dot.d{background:var(--amber)} .dot.r{background:var(--ikb)}
.foot{margin-top:34px;padding-top:12px;border-top:1px solid var(--hair);color:var(--muted);font-size:11.5px}
.foot code{font-family:ui-monospace,Menlo,monospace;background:#F2F1EC;padding:1px 5px}
.empty{color:var(--muted);padding:16px 4px;font-size:13px}
@media(max-width:900px){.layout{flex-direction:column;height:auto}.side{width:auto;border-right:0;
border-bottom:1px solid var(--hair)}.main{overflow:visible}}
</style></head><body>
<div class="top"><div class="brand">装明白 · 审计回放<small>5 份真实单据 / 603 行工序</small></div>
<div class="r">数据 commit <b id="commit">__COMMIT__</b> · 生成于 __DATE__</div></div>
<div class="note">这是<b>预计算回放</b>，不是实时引擎：数据由仓库同一套规则生成，点开每条结论可看到它指向的原表行号。
实时交互请本地跑 <b>python3 -m uvicorn main:app --port 8000</b>；复现本页用 <b>python3 scripts/build_replay.py</b>。</div>
<div class="layout">
  <div class="side"><div class="h">单据</div><div id="docs"></div></div>
  <div class="main" id="main"></div>
</div>
<script id="DATA" type="application/json">__DATA__</script>
<script>
const DATA = JSON.parse(document.getElementById('DATA').textContent);
const fmt = x => (x==null? '—' : Number(x).toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2}));
const esc = s => String(s==null?'':s).replace(/[&<>"]/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const KIND = {missing:['m','漏项'], dup:['d','重复计价'], risk:['r','口径风险']};
let cur = 0, tab = 'all', selF = null;

function findings(d){
  const all = [...d.missing, ...d.duplicates, ...d.risks];
  if(tab==='missing') return d.missing;
  if(tab==='dup') return d.duplicates;
  if(tab==='risk') return d.risks;
  return all;
}
function tabs(d){
  return [['all','全部结论', d.missing.length+d.duplicates.length+d.risks.length],
          ['missing','漏项', d.missing.length],
          ['dup','重复计价', d.duplicates.length],
          ['risk','口径风险', d.risks.length],
          ['lines','明细行', d.lines.length]];
}
function renderSide(){
  document.getElementById('docs').innerHTML = DATA.docs.map((d,i)=>{
    const n = d.missing.length + d.duplicates.length + d.risks.length;
    return `<button class="doc ${i===cur?'on':''}" data-i="${i}">
      <div class="t">${esc(d.label)}</div>
      <div class="m">${d.rows} 行 · ${n} 条结论 · 对齐 ${(d.align_rate*100).toFixed(1)}%</div></button>`;
  }).join('');
  document.querySelectorAll('.doc').forEach(b=>b.onclick=()=>{cur=+b.dataset.i; selF=null; tab='all'; render();});
}
function lineSet(f){ return new Set((f && f.can_highlight!==false) ? (f.lines||[]) : []); }
function render(){
  const d = DATA.docs[cur];
  const selLines = selF ? lineSet(selF) : new Set();
  const hl = new Set(); d.duplicates.forEach(x=>x.lines.forEach(v=>hl.add(v)));
  d.risks.forEach(x=>x.lines.forEach(v=>hl.add(v)));

  let body = `<div class="h1">${esc(d.label)}</div>
    <div class="sub">${esc(d.file)} · 模板 ${d.template==='old_house'?'老房':'新房'} · ${d.detail_rows} 行明细${d.summary_rows?' + '+d.summary_rows+' 行汇总':''}${d.dup_no?' · <b style="color:#B07A22">序号按分区重置，同一序号会出现多次</b>':''}</div>
    <div class="stats">
      <div class="stat"><div class="k">明细合计</div><div class="v">${fmt(d.total)}</div></div>
      <div class="stat"><div class="k">本体对齐</div><div class="v">${(d.align_rate*100).toFixed(1)}%</div></div>
      <div class="stat"><div class="k">确定漏项估算</div><div class="v">${fmt(d.missing_cost)}</div></div>
      <div class="stat"><div class="k">重复计价核减</div><div class="v">${fmt(d.duplicate_cut)}</div></div>
      <div class="stat"><div class="k">整单对账</div><div class="v">${d.recon.ok===true?'一致':(d.recon.diff!=null? '差 '+fmt(d.recon.diff):'无印刷合计')}</div></div>
    </div>
    <div class="tabs">${tabs(d).map(([k,label,n])=>
      `<button class="tab ${tab===k?'on':''}" data-k="${k}">${label}<span class="n">${n}</span></button>`).join('')}</div>`;

  if(tab==='lines'){
    body += `<table><thead><tr><th class="n">序号</th><th>项目</th><th>规格 / 分区</th><th>单位</th>
      <th class="n">数量</th><th class="n">单价</th><th class="n">合价</th></tr></thead><tbody>` +
      (()=>{ let html='', sep=false;
        d.lines.forEach(r=>{
          if(r.summary && !sep){ html += '<tr class="sep"><td colspan="7">汇总 / 取费（单列对账，不参与漏项判定）</td></tr>'; sep=true; }
          // 只有「选中」才上底色；有结论的行只点一个小点，否则整张表都是黄的，信号全糊了
          const cls = selLines.has(r.no)?'sel':'';
          const dot = selLines.has(r.no)?'<span class="dot r"></span>':(hl.has(r.no)?'<span class="dot d"></span>':'');
          html += `<tr class="${cls}"><td class="n">${esc(r.no)}</td><td>${dot}${esc(r.name)}</td>
          <td>${esc(r.spec)}</td><td>${esc(r.unit)}</td><td class="n">${r.qty??''}</td>
          <td class="n">${fmt(r.price)}</td><td class="n">${fmt(r.amount)}</td></tr>`;
        });
        return html; })() + `</tbody></table>`;
  } else {
    const fs = findings(d);
    body += fs.length? `<div class="list">` + fs.map((f,i)=>{
      const [cls,name] = KIND[f.kind];
      const badge = `<span class="badge ${f.tier==='auto'?'auto':'review'}">${f.tier==='auto'?'确定':'建议复核'}</span>`;
      const amt = f.kind==='missing' ? (f.ref? '参照 '+fmt(f.ref) : '无参照价')
                 : (f.amount!=null? fmt(f.amount)+' 元' : '');
      const head = f.kind==='missing' ? (f.ref? fmt(f.ref) : '—')
                 : (f.amount!=null? fmt(f.amount) : '');
      const lines = f.lines.length? '第 '+f.lines.join('、')+' 行' : '';
      const meta = [name, f.rule||'', lines, amt].filter(Boolean).join(' · ');
      return `<div class="f ${selF===f?'on':''}" data-i="${i}">
        <div class="row1"><div class="t">${badge}${esc(f.title)}</div><div class="amt">${head}</div></div>
        <div class="meta">${esc(meta)}</div>
        <div class="detail"><b>判据：</b>${esc(f.basis||'—')}<br><b>公式：</b>${esc(f.formula||'—')}
        ${f.issue? '<br><b>问题：</b>'+esc(f.issue):''}
        ${f.line_note? '<br><b>行号说明：</b>'+esc(f.line_note)
           : (f.lines.length? '<br><b>指向行：</b>'+esc(f.lines.join('、'))+'（点「明细行」看原文）'
           : (f.kind==='missing'? '<br><b>指向：</b>标准工序模板（本次报价中无对应行，所以没有行号）':''))}</div></div>`;
    }).join('') + `</div>` : `<div class="empty">这一类没有结论——空结果同样是结果，说明这一层没查出问题。</div>`;
  }
  body += `<div class="foot">口径：结论由 <code>src/pipeline.py</code> 的 13 条规则生成；本页只回放，不重算。
  漏项金额取参照价库同类中位，标「无参照价」的是工艺项（不单列计价，但必须施工）。
  重复计价只在「同概念 + 同分区 + 工艺包含关系」下才判，宁可不报。</div>`;

  document.getElementById('main').innerHTML = body;
  document.querySelectorAll('.tab').forEach(b=>b.onclick=()=>{tab=b.dataset.k; render();});
  document.querySelectorAll('.f').forEach(el=>el.onclick=()=>{const f=findings(d)[+el.dataset.i]; selF = selF===f? null : f; render();});
  // 切到明细行时，把第一条命中行滚到视野中间——否则用户以为「点了没反应」
  const firstSel = document.querySelector('tr.sel') || document.querySelector('tr.hl');
  if (tab==='lines' && firstSel) firstSel.scrollIntoView({block:'center'});
}
renderSide(); render();
</script></body></html>"""


def render(data):
    return (TPL.replace("__COMMIT__", data["commit"])
               .replace("__DATE__", data["generated"][:16].replace("T", " "))
               .replace("__DATA__", json.dumps(data, ensure_ascii=False, separators=(",", ":"))))


def main():
    ap = argparse.ArgumentParser(description="生成审计回放单页")
    ap.add_argument("--out", default=os.path.join(ROOT, "out", "replay.html"))
    a = ap.parse_args()
    data = build()
    html = render(data)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(html)
    total = sum(len(d["missing"]) + len(d["duplicates"]) + len(d["risks"]) for d in data["docs"])
    print("单据 %d 份｜行项 %d｜结论 %d｜%.0f KB → %s"
          % (len(data["docs"]), sum(d["rows"] for d in data["docs"]), total,
             len(html.encode("utf-8")) / 1024, a.out))


if __name__ == "__main__":
    main()
