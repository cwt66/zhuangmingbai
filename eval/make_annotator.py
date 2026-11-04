# -*- coding: utf-8 -*-
"""生成「第二标注者」用的盲标工具（单个 HTML，双击即用，不需要装环境）。

为什么要有这个东西
------------------
黄金标注现在只有 A1 一个人做的两份。评委/评委式提问一定会问：
「你怎么知道标注是对的？」——唯一的答案是**第二个人独立标一遍，算一致性**。
但让一个同学装 Python、读 CSV、手写标注文件，实际执行率接近零。

所以把它压成：打开一个网页 → 逐项点「有 / 无 / 不适用」→ 导出 CSV → 发回。
全程 30 分钟，不需要命令行，也不需要看到任何引擎输出（盲标）。

设计约束（都是必须的）
--------------------
1. **绝不内嵌 A1 的答案**——否则第二标注者会被锚定，κ 失去意义。
2. 不联网、不依赖任何 CDN——离线可用，图片/数据都不出本机。
3. 导出的 CSV 列与 `eval/golden_labels_*.csv` 兼容，可直接喂给 `cohen_kappa.py`。
4. 单据原文可见（行项全文 + 搜索），否则标注者只能瞎猜。

用法：
    python3 eval/make_annotator.py                    # 生成两份
    python3 eval/make_annotator.py --doc feitai       # 只生成一份
"""
import argparse
import csv
import json
import os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE, "data")

DOCS = {
    "feitai": {
        "csv": "feitai_budget.csv",
        "template": "old_house",
        "title": "上海翡翠台 · 老房全案（123 行工序 + 8 行汇总）",
        "hint": "这是一份老房翻新全包报价（含主材）。",
    },
    "guijiao124": {
        "csv": "guijiao_124_basic.csv",
        "template": "new_house",
        "title": "124㎡ · 基础报价单（65 行工序 + 3 行汇总）",
        "hint": "这是一份新房基础施工报价，主材另计（不含门/洁具/灯具等）。",
    },
}

TPL = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>装明白 · 盲标工具 · __TITLE__</title>
<style>
:root{--ikb:#002FA7;--paper:#FAFAF8;--ink:#0A0A0A;--muted:#6B6B66;--line:#DCDCD6;
      --yes:#0F7B4F;--no:#B33A2B;--na:#6B6B66}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);
     font-family:"PingFang SC","Hiragino Sans GB","Microsoft YaHei",system-ui,sans-serif;
     -webkit-font-smoothing:antialiased}
header{border-bottom:2px solid var(--ink);padding:16px 24px;display:flex;
       align-items:flex-end;justify-content:space-between;gap:16px;flex-wrap:wrap}
h1{font-size:17px;margin:0;font-weight:700;letter-spacing:.01em}
.sub{font-size:12px;color:var(--muted);margin-top:4px;line-height:1.6}
.wrap{display:grid;grid-template-columns:280px 1fr;gap:0;height:calc(100vh - 78px)}
aside{border-right:1px solid var(--line);overflow-y:auto;padding:12px 0}
.item{padding:9px 18px;font-size:13px;cursor:pointer;display:flex;gap:8px;
      align-items:baseline;border-left:3px solid transparent}
.item:hover{background:#F1F1EC}
.item.on{background:#EDF0FB;border-left-color:var(--ikb);font-weight:600}
.item .mark{font-size:11px;width:14px;flex:none;color:var(--muted)}
.item .mark.y{color:var(--yes)} .item .mark.n{color:var(--no)}
main{overflow-y:auto;padding:22px 28px 80px}
.card{border:1px solid var(--line);background:#fff;padding:20px 22px}
.q{font-size:22px;font-weight:700;margin:0 0 6px}
.cid{font-family:ui-monospace,Menlo,monospace;font-size:11px;color:var(--muted)}
.aliases{font-size:12.5px;color:var(--muted);margin:10px 0 0;line-height:1.8}
.aliases b{color:var(--ink);font-weight:600}
.ask{font-size:13px;margin:14px 0 0;padding:10px 12px;background:#F4F4EF;
     border-left:3px solid var(--ikb);line-height:1.7}
.btns{display:flex;gap:10px;margin:20px 0 0;flex-wrap:wrap}
button{font:inherit;font-size:14px;padding:11px 20px;border:1px solid var(--line);
       background:#fff;cursor:pointer;border-radius:2px}
button:hover{border-color:var(--ink)}
button.sel-y{background:var(--yes);color:#fff;border-color:var(--yes)}
button.sel-n{background:var(--no);color:#fff;border-color:var(--no)}
button.sel-a{background:var(--na);color:#fff;border-color:var(--na)}
.note{width:100%;margin-top:14px;font:inherit;font-size:13px;padding:9px 11px;
      border:1px solid var(--line);border-radius:2px;resize:vertical;min-height:52px}
.search{margin:26px 0 8px;display:flex;gap:8px;align-items:center}
.search input{flex:1;font:inherit;font-size:13px;padding:8px 10px;
              border:1px solid var(--line);border-radius:2px;background:#fff}
.count{font-size:12px;color:var(--muted);white-space:nowrap}
table{border-collapse:collapse;width:100%;font-size:12.5px;margin-top:6px}
th,td{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}
th{font-weight:600;background:#F6F6F1;position:sticky;top:0}
td.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
tr.hit{background:#FFF9E6}
.bar{position:fixed;bottom:0;left:0;right:0;background:#fff;border-top:2px solid var(--ink);
     padding:10px 24px;display:flex;align-items:center;gap:14px;font-size:13px}
.bar .prog{flex:1;height:6px;background:#EDEDE7;position:relative}
.bar .prog i{position:absolute;left:0;top:0;bottom:0;background:var(--ikb)}
.bar b{font-variant-numeric:tabular-nums}
.export{background:var(--ikb);color:#fff;border-color:var(--ikb);padding:9px 18px}
.warn{font-size:12px;color:var(--no);margin-top:12px;display:none}
@media print{aside,.btns,.bar,.search{display:none}.wrap{display:block;height:auto}}
</style>
</head>
<body>
<header>
  <div>
    <h1>装明白 · 模板逐项核对（盲标）</h1>
    <div class="sub">__TITLE__<br>__HINT__ 请逐项判断：这份报价单里<b>有没有</b>这一项。</div>
  </div>
  <div class="sub">标注人：<input id="who" placeholder="填你的名字/代号"
       style="font:inherit;font-size:13px;padding:5px 8px;border:1px solid var(--line);width:130px"></div>
</header>

<div class="wrap">
  <aside id="list"></aside>
  <main>
    <div class="card">
      <div class="q" id="q">—</div>
      <div class="cid" id="cid"></div>
      <div class="aliases" id="aliases"></div>
      <div class="ask" id="ask"></div>
      <div class="btns">
        <button id="b-y" onclick="setState('present')">有（1）</button>
        <button id="b-n" onclick="setState('missing')">没有（2）</button>
        <button id="b-a" onclick="setState('not_applicable')">本单不适用（3）</button>
      </div>
      <textarea class="note" id="note" placeholder="依据（选填，但建议写）：在哪一行看到的？或为什么判没有？"></textarea>
      <div class="warn" id="warn">⚠ 还有项目没判，导出会被拦下。</div>
    </div>

    <div class="search">
      <input id="find" placeholder="搜单据原文（项目名/工艺/备注），例如：防水、找平、门槛石">
      <span class="count" id="count"></span>
    </div>
    <table>
      <thead><tr><th>#</th><th>项目名称</th><th>工艺说明</th><th>单位</th><th class="num">数量</th>
      <th class="num">单价</th><th class="num">合价</th><th>备注</th></tr></thead>
      <tbody id="rows"></tbody>
    </table>
  </main>
</div>

<div class="bar">
  <span>进度 <b id="done">0</b>/<b id="total">0</b></span>
  <span class="prog"><i id="pbar" style="width:0"></i></span>
  <button class="export" onclick="exportCSV()">导出标注 CSV</button>
</div>

<script>
const CONCEPTS = __CONCEPTS__;
const ROWS = __ROWS__;
const DOC = "__DOC__";
const state = {};      // cid -> {state, note}
let cur = 0;

function esc(s){return (s==null?'':String(s)).replace(/[&<>"]/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}

function renderList(){
  document.getElementById('list').innerHTML = CONCEPTS.map((c,i)=>{
    const st = state[c.cid] ? state[c.cid].state : '';
    const mk = st==='present'?'y':(st==='missing'?'n':'');
    const sym = st==='present'?'✓':(st==='missing'?'✕':(st==='not_applicable'?'–':'·'));
    return `<div class="item ${i===cur?'on':''}" onclick="goto(${i})">
      <span class="mark ${mk}">${sym}</span><span>${esc(c.name)}</span></div>`;
  }).join('');
  const done = CONCEPTS.filter(c=>state[c.cid]).length;
  document.getElementById('done').textContent = done;
  document.getElementById('total').textContent = CONCEPTS.length;
  document.getElementById('pbar').style.width = (done/CONCEPTS.length*100)+'%';
}

function renderCard(){
  const c = CONCEPTS[cur];
  document.getElementById('q').textContent = c.name;
  document.getElementById('cid').textContent = c.cid + (c.category? '　·　'+c.category : '');
  document.getElementById('aliases').innerHTML = c.aliases.length
    ? '<b>常见叫法：</b>' + c.aliases.map(esc).join('、')
    : '<b>常见叫法：</b>（无别名，按标准名称判断）';
  document.getElementById('ask').innerHTML =
    '判断标准：这份单据里<b>有没有做到这一项</b>（写成别的叫法也算有）。<br>'+
    '「本单不适用」只在<b>报价范围根本不包含</b>时用（例：只报基础施工，主材另计 → 门/洁具/灯具不适用）。';
  const st = state[c.cid] ? state[c.cid].state : '';
  document.getElementById('b-y').className = st==='present'?'sel-y':'';
  document.getElementById('b-n').className = st==='missing'?'sel-n':'';
  document.getElementById('b-a').className = st==='not_applicable'?'sel-a':'';
  document.getElementById('note').value = state[c.cid] ? (state[c.cid].note||'') : '';
  // 自动用标准名+别名高亮
  const kw = c.aliases.concat([c.name]);
  document.getElementById('find').value = '';
  renderRows(kw);
}

function renderRows(kw){
  const find = document.getElementById('find').value.trim();
  const needles = (find ? [find] : kw).filter(Boolean);
  let hit = 0;
  const body = ROWS.map(r=>{
    const hay = (r.name+' '+r.spec+' '+r.note).toLowerCase();
    const isHit = needles.some(k=>k && hay.includes(String(k).toLowerCase()));
    if (isHit) hit++;
    return `<tr class="${isHit?'hit':''}"><td class="num">${esc(r.no)}</td><td>${esc(r.name)}</td>
      <td>${esc(r.spec)}</td><td>${esc(r.unit)}</td><td class="num">${esc(r.qty)}</td>
      <td class="num">${esc(r.price)}</td><td class="num">${esc(r.amount)}</td><td>${esc(r.note)}</td></tr>`;
  }).join('');
  document.getElementById('rows').innerHTML = body;
  document.getElementById('count').textContent = `单据共 ${ROWS.length} 行｜命中 ${hit} 行（黄底）`;
}

function goto(i){ cur = Math.max(0, Math.min(CONCEPTS.length-1, i)); renderList(); renderCard(); }

function setState(s){
  const c = CONCEPTS[cur];
  const note = document.getElementById('note').value;
  state[c.cid] = {state:s, note:note};
  renderList();
  // 自动跳到下一个未判项
  const next = CONCEPTS.findIndex((x,i)=> i>cur && !state[x.cid]);
  goto(next >= 0 ? next : Math.min(CONCEPTS.length-1, cur+1));
}

function exportCSV(){
  const who = (document.getElementById('who').value || 'A2').trim();
  const miss = CONCEPTS.filter(c=>!state[c.cid]);
  if (miss.length){
    document.getElementById('warn').style.display='block';
    alert('还有 '+miss.length+' 项没判，先判完再导出（已跳到第一项未判的）。');
    goto(CONCEPTS.findIndex(c=>!state[c.cid]));
    return;
  }
  const head = 'cid,quote_doc,golden_state,annotator,note';
  const lines = CONCEPTS.map(c=>{
    const s = state[c.cid];
    const note = (s.note||'').replace(/"/g,'""').replace(/\n/g,' ');
    return `${c.cid},${DOC},${s.state},${who},"${note}"`;
  });
  const blob = new Blob(['\ufeff'+head+'\n'+lines.join('\n')], {type:'text/csv;charset=utf-8'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `${DOC}.${who}.labels.csv`;
  a.click();
}

document.getElementById('note').addEventListener('input', e=>{
  const c = CONCEPTS[cur];
  if (state[c.cid]) state[c.cid].note = e.target.value;
});
document.getElementById('find').addEventListener('input', ()=>{
  const c = CONCEPTS[cur];
  renderRows(c.aliases.concat([c.name]));
});
document.addEventListener('keydown', e=>{
  if (e.target.tagName==='INPUT' || e.target.tagName==='TEXTAREA') return;
  if (e.key==='1') setState('present');
  else if (e.key==='2') setState('missing');
  else if (e.key==='3') setState('not_applicable');
  else if (e.key==='ArrowRight') goto(cur+1);
  else if (e.key==='ArrowLeft') goto(cur-1);
});

goto(0);
</script>
</body></html>
"""


def load_concepts(template_key):
    with open(os.path.join(DATA, "templates_v0.json"), encoding="utf-8") as f:
        templates = json.load(f)
    with open(os.path.join(DATA, "ontology_v0.json"), encoding="utf-8") as f:
        onto = json.load(f)
    by_id = {c["id"]: c for c in onto}
    out = []
    for cid in templates[template_key]:
        c = by_id[cid]
        out.append({
            "cid": cid,
            "name": c["n"],
            "category": c.get("c", ""),
            "aliases": c.get("a", []),
        })
    return out


def load_rows(csv_name):
    path = os.path.join(DATA, "real", csv_name)
    with open(path, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    return [{
        "no": r.get("序号", ""), "name": r.get("项目名称", ""),
        "spec": r.get("规格工艺", ""), "unit": r.get("单位", ""),
        "qty": r.get("数量", ""), "price": r.get("单价", ""),
        "amount": r.get("合价", ""), "note": r.get("备注", ""),
    } for r in rows]


def _safe_json(obj):
    """内嵌进 <script> 的 JSON 必须转义 </，否则单据里出现 </script> 会截断页面。"""
    return json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")


def build(doc, outdir):
    meta = DOCS[doc]
    concepts = load_concepts(meta["template"])
    rows = load_rows(meta["csv"])
    html = (TPL.replace("__TITLE__", meta["title"])
              .replace("__HINT__", meta["hint"])
              .replace("__DOC__", doc)
              .replace("__CONCEPTS__", _safe_json(concepts))
              .replace("__ROWS__", _safe_json(rows)))
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, "annotate_%s.html" % doc)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path, len(concepts), len(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--doc", default=None, choices=list(DOCS))
    ap.add_argument("--out", default=os.path.join(BASE, "eval", "annotator"))
    args = ap.parse_args()
    docs = [args.doc] if args.doc else list(DOCS)
    for d in docs:
        path, n_c, n_r = build(d, args.out)
        print("生成 %s ｜ %d 项概念 ｜ %d 行单据 ｜ %.0f KB"
              % (os.path.relpath(path, BASE), n_c, n_r, os.path.getsize(path) / 1024))


if __name__ == "__main__":
    main()
