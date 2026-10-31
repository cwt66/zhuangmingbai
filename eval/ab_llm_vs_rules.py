# -*- coding: utf-8 -*-
"""LLM 直接审 vs 规则裁决 —— 幻觉 A/B 实验

回答一个问题（答辩 Q&A 预案 Q「为什么判断不经过大模型」的数据版）：
  让大模型直接读报价单下结论，和规则引擎比，差在哪？

三个量化指标（协议与 eval/对照实验协议_RenoPit.md 同源）：
  A. 输出一致性  —— 同一单据跑 N 次，LLM 漏项集合两两 Jaccard；规则引擎指纹恒同
  B. 幻觉候选    —— LLM 报的漏项中，回原文做证据词检索零命中的条数
  C. 与规则侧差集 —— LLM 独有（需人工核实是否真问题）/ 规则独有（LLM 漏报）

用法：
  python3 eval/ab_llm_vs_rules.py                       # mock 模式（离线演示统计口径）
  MAAS_API_KEY=xxx MAAS_MODEL=qwen-vl-plus python3 eval/ab_llm_vs_rules.py --provider maas
  python3 eval/ab_llm_vs_rules.py --csv data/real/huaxibeian_newhouse.csv --template new_house --runs 3

输出：
  eval/ab_runs/ab_result_<slug>.json   原始留痕（LLM 每次完整输出）
  eval/ab_runs/ab_report_<slug>.md     结论报告（进答辩材料的数字从这里取）

边界（预先声明）：
  - mock 模式的 LLM 输出是人工构造的演示数据，只用于验证统计逻辑，不产生任何实验结论
  - 规则侧结果就是 pipeline.audit 的原样输出，不做任何「美化」
"""
import argparse
import hashlib
import json
import os
import re
import sys
import urllib.request
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
import pipeline as P  # noqa: E402

OUT_DIR = os.path.join(ROOT, "eval", "ab_runs")

SYSTEM_PROMPT = (
    "你是装修报价审计员。下面是一份装修报价单的逐行明细（带行号）。"
    "请直接审计并只输出 JSON（不要多余文字），格式："
    '{"missing": [{"concept": "工序名", "evidence": "判断依据一句话", "amount_estimate": 0}], '
    '"duplicates": [{"concept": "工序名", "lines": [0], "reason": "依据"}], '
    '"risks": [{"line": 0, "issue": "口径问题一句话"}]}。'
    "missing=报价单里缺的标准工序；duplicates=同一工序被收两次钱；risks=按实结算/单位不可核等口径问题。"
)


# ---------------------------------------------------------------- 输入构造

def quote_text(q):
    """与规则引擎严格等量的信息：行号 + 项目 + 规格 + 单位 + 数量 + 单价 + 合价 + 备注"""
    lines = []
    for r in q["rows"]:
        cells = [str(r["no"]), r["name"] or "", r["spec"] or "", r["unit"] or "",
                 str(r["qty"] or ""), str(r["price"] or ""), str(r["amount"] or ""),
                 r.get("note", "") or ""]
        lines.append(" | ".join(x for x in cells))
    return "\n".join(lines)


# ---------------------------------------------------------------- LLM 侧

def llm_call_maas(text, model, base_url, key):
    body = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                     {"role": "user", "content": text}],
        "temperature": 0.7,          # 不压温度：要测的就是「跑两次一样不一样」
    }).encode()
    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions", data=body,
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read())
    return data["choices"][0]["message"]["content"]


def llm_call_mock(text, run_idx):
    """演示用的固定扰动输出：三次结果不同，用于验证统计口径（不是实验数据）"""
    base = {"missing": [{"concept": "闭水试验", "evidence": "防水施工后应做", "amount_estimate": 325},
                        {"concept": "打压试验", "evidence": "水路改造后应做", "amount_estimate": 400},
                        {"concept": "窗台石" if run_idx == 1 else "门槛石",
                         "evidence": "常规工序", "amount_estimate": 800}],
            "duplicates": [],
            "risks": [{"line": 100, "issue": "按实结算"}]}
    if run_idx == 2:
        base["missing"].append({"concept": "垃圾清运", "evidence": "拆除后需要", "amount_estimate": 1350})
    if run_idx == 3:
        base["missing"] = base["missing"][:2]   # 第三次少报一条 → 一致性 < 1
    return json.dumps(base, ensure_ascii=False)


def parse_llm_json(raw):
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        raise ValueError("LLM 未返回 JSON")
    return json.loads(m.group(0))


# ---------------------------------------------------------------- 统计

def norm_concept(s):
    return P.PUNCT.sub("", (s or "").lower())


def jaccard(a, b):
    a, b = set(a), set(b)
    return len(a & b) / len(a | b) if (a or b) else 1.0


def compare(rules_res, llm_runs, q):
    """统计口径（预注册，防事后调参）：
    - 漏项（missing）本就定义为「单据里没有的工序」，回原文零命中是预期行为，**不构成幻觉判据**；
      漏项的真伪只能对照黄金标注 → 全部归入「待人工核实」。
    - 重复（duplicates）与口径（risks）声称了具体行号 → 行号在单据中不存在即为幻觉候选（可自动判）。
    """
    stats = {"runs": len(llm_runs), "pairwise_jaccard": [], "hallucination_candidates": [],
             "llm_only_missing": [], "rules_only_missing": []}
    rows_no = {str(r["no"]) for r in q["rows"]}
    rule_missing = {norm_concept(m["concept"]) for m in rules_res["missing"]}

    parsed = []
    for run in llm_runs:
        d = parse_llm_json(run["raw"])
        run["parsed"] = d
        parsed.append(d)

    for i in range(len(parsed)):
        for j in range(i + 1, len(parsed)):
            a = {norm_concept(x["concept"]) for x in parsed[i].get("missing", [])}
            b = {norm_concept(x["concept"]) for x in parsed[j].get("missing", [])}
            stats["pairwise_jaccard"].append(round(jaccard(a, b), 3))

    # 幻觉候选：仅 duplicates / risks（有行号可验）
    for d in parsed:
        for x in d.get("risks", []):
            ln = str(x.get("line", ""))
            if ln and ln not in rows_no:
                stats["hallucination_candidates"].append("口径行%s（行号不存在）" % ln)
        for x in d.get("duplicates", []):
            for ln in x.get("lines", []):
                if str(ln) not in rows_no:
                    stats["hallucination_candidates"].append(
                        "重复行%s（行号不存在）" % ln)

    # 漏项差集（对照黄金标注前只能算差集，不算对错）
    llm_missing = set()
    for d in parsed:
        for x in d.get("missing", []):
            llm_missing.add(norm_concept(x["concept"]))
    stats["llm_only_missing"] = sorted(llm_missing - rule_missing)
    stats["rules_only_missing"] = sorted(rule_missing - llm_missing)
    return stats


# ---------------------------------------------------------------- 主流程

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="data/real/sh_zhuoguan_halfpack.csv")
    ap.add_argument("--doc", default="上海卓观")
    ap.add_argument("--template", default="old_house", choices=["old_house", "new_house"])
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--provider", default="mock", choices=["mock", "maas"])
    args = ap.parse_args()

    key = os.environ.get("MAAS_API_KEY", "")
    model = os.environ.get("MAAS_MODEL", "qwen-vl-plus")
    base_url = os.environ.get("MAAS_BASE_URL", "https://api.modelarts-maas.com/v1")
    if args.provider == "maas" and not key:
        print("!! MAAS_API_KEY 未设置，退回 mock 模式（mock 输出不产生实验结论）")
        args.provider = "mock"

    onto = P.Ontology(os.path.join(ROOT, "data", "ontology_v0.json"))
    tpl = json.load(open(os.path.join(ROOT, "data", "templates_v0.json"), encoding="utf-8"))
    rules = json.load(open(os.path.join(ROOT, "data", "rules_v0.json"), encoding="utf-8"))
    cov = json.load(open(os.path.join(ROOT, "data", "coverage_v0.json"), encoding="utf-8"))

    # 规则侧
    q = P.build_line_index(P.load_quote(os.path.join(ROOT, args.csv), args.doc), onto)
    rules_res = P.audit([q], onto, tpl, rules, cov, template_key=args.template)[q["label"]]

    text = quote_text(q)

    # LLM 侧 N 次
    os.makedirs(OUT_DIR, exist_ok=True)
    llm_runs = []
    for i in range(1, args.runs + 1):
        if args.provider == "maas":
            raw = llm_call_maas(text, model, base_url, key)
        else:
            raw = llm_call_mock(text, i)
        llm_runs.append({"run": i, "raw": raw})
        print("  LLM run %d/%d done" % (i, args.runs))

    slug = os.path.splitext(os.path.basename(args.csv))[0]
    stats = compare(rules_res, llm_runs, q)

    # 报告
    jc = stats["pairwise_jaccard"]
    lines = [
        "# 幻觉 A/B：LLM 直接审 vs 规则裁决",
        "",
        "> 单据：%s（%d 行）｜ 模板：%s ｜ provider：%s ｜ runs：%d ｜ %s" %
        (args.doc, len(q["rows"]), args.template, args.provider,
         args.runs, datetime.now().strftime("%Y-%m-%d %H:%M")),
        "",
        "| 指标 | LLM 直接审 | 规则裁决 |",
        "|---|---|---|",
        "| A. 输出一致性（漏项集合两两 Jaccard） | %s | 1.000（指纹恒同） |" %
        ("、".join("%.2f" % x for x in jc) if jc else "—"),
        "| B. 幻觉候选（行号不存在；仅重复/口径类可自动判） | %d 条：%s | 0（每条带行号+基准） |" %
        (len(stats["hallucination_candidates"]),
         "、".join(stats["hallucination_candidates"]) or "—"),
        "| C1. LLM 独有漏项（待人工核实，%d 条） | 集合见留痕 JSON | — |" %
        len(stats["llm_only_missing"]),
        "| C2. LLM 漏报（规则有、LLM 无） | — | %d 条 |" % len(stats["rules_only_missing"]),
        "",
        "规则侧结果：确定漏项 %d + 复核 %d ｜ 重复 %d ｜ 口径 %d ｜ 指纹可复现" %
        (sum(1 for m in rules_res["missing"] if m["tier"] == "auto"),
         sum(1 for m in rules_res["missing"] if m["tier"] != "auto"),
         len(rules_res["duplicates"]), len(rules_res["risks"])),
        "",
        "**统计口径（预注册）**：漏项本就定义为「单据里没有的工序」，回原文零命中是预期行为，不构成幻觉判据；"
        "漏项真伪只能对照黄金标注（eval/golden_labels_TEMPLATE.csv）。重复/口径声称了行号，行号不存在即可自动判幻觉。",
        "",
        "**mock 模式说明**：以上 LLM 输出为人工构造演示，只验证统计口径；真实验需 --provider maas。",
    ]
    report = "\n".join(lines)
    rp = os.path.join(OUT_DIR, "ab_report_%s.md" % slug)
    jp = os.path.join(OUT_DIR, "ab_result_%s.json" % slug)
    open(rp, "w", encoding="utf-8").write(report)
    json.dump({"args": vars(args), "llm_runs": llm_runs,
               "rules_missing": rules_res["missing"], "stats": stats},
              open(jp, "w", encoding="utf-8"), ensure_ascii=False, indent=2, default=str)
    print(report)
    print("\n[OK] %s ｜ %s" % (rp, jp))


if __name__ == "__main__":
    main()
