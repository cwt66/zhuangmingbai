# -*- coding: utf-8 -*-
"""把引擎对每份单据的判定结果「冻结」成带指纹的文件。

为什么必须冻结：评测要能回答「改了代码之后指标变好了，还是只是换了个说法」。
只有先固定输入（数据 + 模板 + 本体 + 覆盖率表）、固定输出（每个模板项的判定），
标注才有意义——否则标注的是移动靶。

用法：
    python3 scripts/snapshot_predictions.py            # 输出到 eval/predictions/
    python3 scripts/snapshot_predictions.py --check    # 只比对，不覆盖（CI 用）

产物结构（每份单据一个 JSON）：
{
  "meta": {slug, doc, template_key, git_commit, created_at, fingerprint,
           hashes: {data, template, ontology, rules, coverage}},
  "items": [{cid, concept, decision, tier, confidence, evidence}],
  "duplicates": [...], "risks": [...], "stats": {...}
}
decision 三值：present（原文覆盖）/ missing_auto（确定漏项）/ missing_review（建议复核）
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
import pipeline as P  # noqa: E402

DATA = os.path.join(ROOT, "data")
OUT = os.path.join(ROOT, "eval", "predictions")

# 每份真实单据用哪套模板：模板选错，结论全错，所以显式声明并进指纹
DOCS = [
    {"slug": "sh_zhuoguan", "doc": "上海卓观·两房半包（篱笆网公开晒单）",
     "csv": "data/real/sh_zhuoguan_halfpack.csv", "template_key": "old_house"},
    {"slug": "huaxibeian", "doc": "花溪北岸·5 页施工报价",
     "csv": "data/real/huaxibeian_newhouse.csv", "template_key": "new_house"},
    # 第一份**文本版 PDF**（7 页、19 个分区、168 行），经坐标法解析得到
    {"slug": "szxinxin_clinic", "doc": "深圳新新·诊所装饰（公开 PDF）",
     "csv": "data/real/szxinxin_clinic_pdf.csv", "template_key": "new_house"},
    # 补全到 5/5：之前只冻结了 3 份，另 2 份的判定变化不会被发现——
    # 2026-11-07 加 R9「有量无价」时，124㎡ 多出一条结论而指纹没报警，就是这个洞。
    {"slug": "feitai", "doc": "上海翡翠台·老房全案",
     "csv": "data/real/feitai_budget.csv", "template_key": "old_house"},
    {"slug": "guijiao124", "doc": "124㎡·基础报价单", "scope": "base",
     "csv": "data/real/guijiao_124_basic.csv", "template_key": "new_house"},
]


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def git_commit():
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                              capture_output=True, text=True, timeout=5).stdout.strip() or "no-git"
    except Exception:
        return "no-git"


def run_one(onto, templates, rules, coverage, spec):
    csv_path = os.path.join(ROOT, spec["csv"])
    q = P.build_line_index(P.load_quote(csv_path, spec["doc"]), onto)
    scope = spec.get("scope", "full")
    res = P.audit([q], onto, templates, rules, coverage,
                  template_key=spec["template_key"], scope=scope)[q["label"]]

    flagged = {m["cid"]: m for m in res["missing"]}
    items = []
    # 用**裁剪后**的模板：base 口径下主材项不参与判漏，
    # 若仍按完整模板遍历，被裁掉的 7 项会被误记成 missing_auto（假的漏项）。
    for cid in P.trim_template(templates[spec["template_key"]], scope):
        m = flagged.get(cid)
        if m:
            decision = "missing_auto" if m["tier"] == "auto" else "missing_review"
            items.append({"cid": cid, "concept": m["concept"], "decision": decision,
                          "tier": m["tier"], "confidence": m["confidence"],
                          "evidence": m.get("evidence", {})})
        else:
            rows = [r["no"] for r in q["rows"] if r["cid"] == cid]
            items.append({"cid": cid, "concept": onto.by_id[cid]["n"], "decision": "present",
                          "tier": "-", "confidence": None,
                          "evidence": {"matched_rows": rows}})

    hashes = {
        "data": sha256_file(csv_path),
        "template": sha256_file(os.path.join(DATA, "templates_v0.json")),
        "ontology": sha256_file(os.path.join(DATA, "ontology_v0.json")),
        "rules": sha256_file(os.path.join(DATA, "rules_v0.json")),
        "coverage": sha256_file(os.path.join(DATA, "coverage_v0.json")),
    }
    body = {"items": items,
            "duplicates": res["duplicates"],
            "risks": res["risks"],
            "stats": {"rows": len(q["rows"]),
                      "aligned": sum(1 for r in q["rows"] if r["cid"]),
                      "total": q["total"],
                      "missing_auto": sum(1 for i in items if i["decision"] == "missing_auto"),
                      "missing_review": sum(1 for i in items if i["decision"] == "missing_review"),
                      "present": sum(1 for i in items if i["decision"] == "present")}}
    fingerprint = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False)
                                 .encode()).hexdigest()[:16]
    meta = {"slug": spec["slug"], "doc": spec["doc"], "template_key": spec["template_key"],
            "scope": scope,
            "git_commit": git_commit(), "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "fingerprint": fingerprint, "hashes": hashes}
    return {"meta": meta, **body}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只比对指纹，不写文件")
    args = ap.parse_args()

    onto = P.Ontology(os.path.join(DATA, "ontology_v0.json"))
    templates = json.load(open(os.path.join(DATA, "templates_v0.json"), encoding="utf-8"))
    rules = json.load(open(os.path.join(DATA, "rules_v0.json"), encoding="utf-8"))
    coverage = json.load(open(os.path.join(DATA, "coverage_v0.json"), encoding="utf-8"))
    os.makedirs(OUT, exist_ok=True)

    drift = 0
    for spec in DOCS:
        snap = run_one(onto, templates, rules, coverage, spec)
        path = os.path.join(OUT, spec["slug"] + ".json")
        s = snap["stats"]
        print("%-14s 行%3d 对齐%3d | 漏(确定)%2d 漏(复核)%2d 已列%2d | 指纹 %s"
              % (spec["slug"], s["rows"], s["aligned"], s["missing_auto"],
                 s["missing_review"], s["present"], snap["meta"]["fingerprint"]))

        if args.check:
            if not os.path.exists(path):
                print("   ! 缺少基线文件，先跑一次不带 --check"); drift += 1
                continue
            old = json.load(open(path, encoding="utf-8"))
            if old["meta"]["fingerprint"] != snap["meta"]["fingerprint"]:
                print("   ! 判定已漂移：%s → %s"
                      % (old["meta"]["fingerprint"], snap["meta"]["fingerprint"]))
                changed = [i["cid"] for i, o in zip(snap["items"], old["items"]) if i != o]
                print("     变化项：", changed or "（仅统计/哈希变化）")
                drift += 1
            else:
                print("   = 与冻结基线一致")
        else:
            json.dump(snap, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    if not args.check:
        print("\n已写入", OUT)
        print("注意：改了模板/本体/覆盖率表会让指纹变化——这是设计，不是 bug。")
        print("     评测结论必须注明用的是哪个指纹。")
    return 1 if drift else 0


if __name__ == "__main__":
    sys.exit(main())
