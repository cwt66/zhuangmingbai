# -*- coding: utf-8 -*-
"""装明白 MCP Server（零第三方依赖，stdio / JSON-RPC 2.0）。

为什么自己实现而不是 pip install mcp
------------------------------------
赛题要求「基于已有智能体和 Skills」；评委在断网、无第三方包的机器上也应能
调用审计能力。核心引擎本来就是零依赖，这一层保持同一约束：
只做 MCP 协议的必要子集（initialize / ping / tools / resources / prompts），
用标准库处理 newline-delimited JSON-RPC。所有日志只写 stderr——stdout 专供协议帧。

对标：modelcontextprotocol/python-sdk（24.9k★，MIT）的 FastMCP 写法；
协议帧格式与其 stdio transport 兼容，可用 MCP Inspector / 任意 MCP host 连接。

用法：
    python3 mcp_server.py                # 作为 MCP server 运行
    python3 mcp_server.py --self-test    # 自握手 + 列工具 + 调一次审计 + 读资源
"""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
import doc_gate  # noqa: E402
import pipeline  # noqa: E402

SERVER_INFO = {"name": "zhuangmingbai-audit", "version": "0.6.0"}
PROTOCOL_VERSION = "2025-06-18"
ALLOWED_SUFFIX = {".csv", ".xlsx", ".txt", ".tsv"}
MAX_FINDINGS = 40


def log(msg):
    print("[zmb-mcp] " + str(msg), file=sys.stderr, flush=True)


def safe_path(raw):
    """只许读仓库内的单据/文本，防目录穿越。"""
    p = (ROOT / raw).resolve()
    if ROOT not in p.parents and p != ROOT:
        raise ValueError("路径必须在项目目录内")
    if p.suffix.lower() not in ALLOWED_SUFFIX:
        raise ValueError("只接受 CSV / XLSX / TSV / TXT")
    if not p.is_file():
        raise ValueError("文件不存在: " + raw)
    return p


def load_rules():
    onto = pipeline.Ontology(os.path.join(pipeline.DATA, "ontology_v0.json"))
    with open(os.path.join(pipeline.DATA, "templates_v0.json"), encoding="utf-8") as f:
        templates = json.load(f)
    with open(os.path.join(pipeline.DATA, "rules_v0.json"), encoding="utf-8") as f:
        rules = json.load(f)
    cover = os.path.join(pipeline.DATA, "coverage_v0.json")
    coverage = json.load(open(cover, encoding="utf-8")) if os.path.exists(cover) else None
    return onto, templates, rules, coverage


TOOLS = [
    {
        "name": "classify_quote",
        "description": "解析前守门：判断这份文件是不是报价单。返回类型、置信度与命中证据；合同/无关文档直接拒绝，不给审计结论。",
        "inputSchema": {"type": "object", "properties": {
            "path": {"type": "string", "description": "仓库内相对路径，如 data/real/feitai_budget.csv"}},
            "required": ["path"]},
    },
    {
        "name": "audit_quote",
        "description": "对一份报价单跑确定性审计：漏项/重复/口径/算术/对账。每条结论带原表行、规则 ID、档位与证据分；低分结论只进待确认，不进核减金额。",
        "inputSchema": {"type": "object", "properties": {
            "path": {"type": "string", "description": "仓库内相对路径"},
            "template": {"type": "string", "enum": ["old_house", "new_house"], "default": "old_house"},
            "scope": {"type": "string", "enum": ["full", "base"], "default": "full"}},
            "required": ["path"]},
    },
    {
        "name": "list_samples",
        "description": "列出可直接试跑的样例与真实单据，供 agent 选择输入。",
        "inputSchema": {"type": "object", "properties": {}},
    },
]

RESOURCES = [
    {"uri": "zmb://rules", "name": "13 条审计规则", "mimeType": "application/json",
     "description": "可逐条审阅的规则清单（YAML 同源 JSON）"},
    {"uri": "zmb://ontology", "name": "工序坐标系", "mimeType": "application/json",
     "description": "概念数与别名覆盖摘要"},
    {"uri": "zmb://datasets", "name": "单据清单", "mimeType": "application/json",
     "description": "样例与真实单据路径"},
]

PROMPTS = [
    {"name": "audit_checklist",
     "description": "先守门、再审计、最后核对指纹与边界的标准流程",
     "arguments": [{"name": "path", "description": "待审单据路径", "required": True}]},
]


def compact_findings(res, label):
    r = res.get(label, {})
    out = []
    for kind, key in (("missing", "missing"), ("duplicate", "duplicates"), ("risk", "risks")):
        for x in r.get(key, []) or []:
            if len(out) >= MAX_FINDINGS:
                break
            out.append({
                "kind": kind,
                "cid": x.get("cid"),
                "row": x.get("no") or x.get("row") or x.get("line"),
                "tier": x.get("tier"),
                "strength": x.get("strength"),
                "basis": str(x.get("basis") or x.get("why") or "")[:160],
            })
    return out


def do_tool(name, args):
    if name == "list_samples":
        items = []
        for folder in ("data/samples", "data/real"):
            d = ROOT / folder
            if d.is_dir():
                for p in sorted(d.glob("*.csv")):
                    items.append({"path": p.relative_to(ROOT).as_posix(), "bytes": p.stat().st_size})
        return {"content": [{"type": "text", "text": json.dumps(items, ensure_ascii=False)}]}

    path = args.get("path", "")
    if not path:
        raise ValueError("缺少 path")
    p = safe_path(path)

    if name == "classify_quote":
        ok, info = doc_gate.gate_file(str(p), p.name)
        return {"content": [{"type": "text", "text": json.dumps(
            {"ok": ok, "info": info}, ensure_ascii=False, default=str)}]}

    if name == "audit_quote":
        template = args.get("template", "old_house")
        scope = args.get("scope", "full")
        if template not in ("old_house", "new_house") or scope not in ("full", "base"):
            raise ValueError("template/scope 取值不合法")
        ok, info = doc_gate.gate_file(str(p), p.name)
        if not ok:
            return {"content": [{"type": "text", "text": json.dumps(
                {"refused": True, "reason": info.get("message"), "evidence": info},
                ensure_ascii=False, default=str)}], "isError": False}
        onto, templates, rules, coverage = load_rules()
        label = p.stem
        q = [pipeline.build_line_index(pipeline.load_quote(str(p), label), onto)]
        if not q[0]["rows"]:
            return {"content": [{"type": "text", "text": "没有解析到行项，拒绝出结论"}], "isError": True}
        res = pipeline.audit(q, onto, templates, rules, coverage,
                             template_key=template, scope=scope)
        r = res.get(label, {})
        result = {
            "label": label,
            "template": template,
            "scope": scope,
            "rows": len(q[0]["rows"]),
            "fingerprint": pipeline._report_fingerprint(res),
            "total": r.get("total"),
            "effective": r.get("effective"),
            "counts": {k: len(r.get(k) or []) for k in
                       ("missing", "duplicates", "risks", "exposure")},
            "findings": compact_findings(res, label),
            "findings_truncated": sum(len(r.get(k) or []) for k in
                                      ("missing", "duplicates", "risks")) > MAX_FINDINGS,
            "boundary": "只判断漏没漏/重没重，不判断贵不贵；低强度结论不进核减金额。",
        }
        return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, default=str)}]}

    raise ValueError("未知工具: " + name)


def do_resource(uri):
    if uri == "zmb://rules":
        data = json.load(open(ROOT / "data/rules_v0.json", encoding="utf-8"))
        return [{"uri": uri, "mimeType": "application/json", "text": json.dumps(
            [{"id": r.get("id"), "name": r.get("name"), "enabled": r.get("enabled", True)}
             for r in data.get("rules", [])], ensure_ascii=False)}]
    if uri == "zmb://ontology":
        onto, _, _, _ = load_rules()
        return [{"uri": uri, "mimeType": "application/json", "text": json.dumps(
            {"concepts": len(getattr(onto, "items", {}) or {}),
             "note": "119 概念 / 388 别名以 data/ontology_v0.json 为准"}, ensure_ascii=False)}]
    if uri == "zmb://datasets":
        return do_tool("list_samples", {})["content"]
    raise ValueError("未知资源: " + uri)


def handle(msg):
    method = msg.get("method")
    mid = msg.get("id")
    params = msg.get("params") or {}
    if method == "notifications/initialized" or method.startswith("notifications/"):
        return None
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": params.get("protocolVersion", PROTOCOL_VERSION),
            "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
            "serverInfo": SERVER_INFO}}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        try:
            result = do_tool(params.get("name"), params.get("arguments") or {})
            return {"jsonrpc": "2.0", "id": mid, "result": result}
        except Exception as e:
            return {"jsonrpc": "2.0", "id": mid, "result": {
                "content": [{"type": "text", "text": "拒绝执行: " + str(e)}], "isError": True}}
    if method == "resources/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"resources": RESOURCES}}
    if method == "resources/read":
        try:
            return {"jsonrpc": "2.0", "id": mid, "result": {"contents": do_resource(params.get("uri"))}}
        except Exception as e:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": str(e)}}
    if method == "prompts/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"prompts": PROMPTS}}
    if method == "prompts/get":
        if params.get("name") != "audit_checklist":
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": "未知 prompt"}}
        path = (params.get("arguments") or {}).get("path", "<待审单据路径>")
        text = ("先调用 classify_quote(%s) 确认是报价单；若被拒绝，停止并把理由告诉用户，"
                "不要硬出报告。确认后调用 audit_quote(%s)；只读 fingerprint 与 counts，"
                "逐条引用 findings 的 row/cid/basis；低强度结论标为待确认，不进核减金额；"
                "最后说明边界：不判断贵不贵。") % (path, path)
        return {"jsonrpc": "2.0", "id": mid, "result": {"messages": [
            {"role": "user", "content": {"type": "text", "text": text}}]}}
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "未知方法: " + str(method)}}


def self_test():
    import subprocess
    import time
    proc = subprocess.Popen([sys.executable, str(Path(__file__))],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, bufsize=1)
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": PROTOCOL_VERSION}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        {"jsonrpc": "2.0", "id": 3, "method": "resources/read", "params": {"uri": "zmb://rules"}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {
            "name": "audit_quote",
            "arguments": {"path": "data/samples/company_a.csv", "template": "old_house"}}},
    ]
    for r in requests:
        proc.stdin.write(json.dumps(r, ensure_ascii=False) + "\n")
    proc.stdin.flush()
    time.sleep(3)
    proc.stdin.close()
    lines = proc.stdout.read().splitlines()
    proc.wait(timeout=10)
    results = {}
    for line in lines:
        try:
            o = json.loads(line)
            results[o.get("id")] = o
        except Exception:
            log("非法协议帧: " + line[:120])
    ok = (1 in results and 2 in results and 3 in results and 4 in results)
    tools = len((results.get(2, {}).get("result") or {}).get("tools", []))
    text = ((results.get(4, {}).get("result") or {}).get("content") or [{}])[0].get("text", "")
    try:
        audit = json.loads(text)
    except Exception:
        audit = {}
    print(json.dumps({
        "handshake": 1 in results, "tools": tools,
        "fingerprint": audit.get("fingerprint"),
        "rows": audit.get("rows"), "counts": audit.get("counts"),
        "refused": audit.get("refused", False),
        "frames": len(lines), "exitcode": proc.returncode,
    }, ensure_ascii=False, indent=2))
    return 0 if ok and tools == len(TOOLS) and audit.get("fingerprint") else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception as e:
            log("解析失败: " + str(e))
            continue
        out = handle(msg)
        if out is not None:
            sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
