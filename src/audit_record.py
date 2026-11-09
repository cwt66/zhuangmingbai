# -*- coding: utf-8 -*-
"""一次审计的「可复算记录」：打包和记账共用同一份构造逻辑。

## 为什么单独抽出来

2026-11-05 踩过一次：新增 `addon_forecast.py` 后忘了加进打包清单，
审计包里的 `verify.py` 直接 ModuleNotFoundError。
如果「要打哪些文件」在打包脚本和账本脚本里各写一份，这类 bug 会翻倍。

所以文件清单、哈希口径、记录结构只在这里定义一次：
- `scripts/make_bundle.py` 用它生成 `result.json`；
- `scripts/ledger.py` 用它生成账本 payload。

## 记录里放什么、不放什么

放：输入哈希、**每份标准**的哈希、**每个引擎文件**的哈希、判定摘要 digest、
结论摘要（供人读）。
不放：绝对路径、时间戳进入 digest、文件名进入 digest——
否则同一份单据换个名字就对不上，校验器会变成噪声源。
"""
import hashlib
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")

sys.path.insert(0, os.path.join(ROOT, "src"))
import pipeline as P  # noqa: E402

STANDARDS = ["templates_v0.json", "ontology_v0.json", "rules_v0.json",
             "coverage_v0.json", "reference_prices.json", "template_scopes.json"]
CODE = ["pipeline.py", "obligations.py", "pricing_system.py",
        "addon_forecast.py", "doc_gate.py"]


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def git_commit(cwd=None):
    try:
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                           cwd=cwd or ROOT, capture_output=True, text=True)
        return r.stdout.strip() or "no-git"
    except OSError:
        return "no-git"


def build_record(csv_path, label, template, scope, created_at,
                 data_dir=None, src_dir=None):
    """返回 (record, q, res)。record 里的哈希全部当场重算。"""
    data_dir = data_dir or DATA
    src_dir = src_dir or os.path.join(ROOT, "src")

    onto = P.Ontology(os.path.join(data_dir, "ontology_v0.json"))
    tpl = json.load(open(os.path.join(data_dir, "templates_v0.json"), encoding="utf-8"))
    rules = json.load(open(os.path.join(data_dir, "rules_v0.json"), encoding="utf-8"))
    cov = json.load(open(os.path.join(data_dir, "coverage_v0.json"), encoding="utf-8"))

    q = P.build_line_index(P.load_quote(csv_path, label), onto)
    res = P.audit([q], onto, tpl, rules, cov,
                  template_key=template, scope=scope)[q["label"]]

    record = {
        "label": label,
        "template_key": template,
        "scope": scope,
        "created_at": created_at,
        "git_commit": git_commit(),
        "input_sha256": sha256_file(csv_path),
        "standards_sha256": {n: sha256_file(os.path.join(data_dir, n))
                             for n in STANDARDS
                             if os.path.isfile(os.path.join(data_dir, n))},
        "code_sha256": {n: sha256_file(os.path.join(src_dir, n))
                        for n in CODE
                        if os.path.isfile(os.path.join(src_dir, n))},
        "digest": P.result_digest(q, res),
        "summary": {
            "rows": len(q["rows"]), "summary_rows": len(q["summary_rows"]),
            "total": round(q["total"], 2),
            "missing": [{"concept": m["concept"], "tier": m["tier"],
                         "ref_amount": m.get("ref_amount")} for m in res["missing"]],
            "duplicates": [{"concept": d["concept"], "lines": d["lines"],
                            "amount": d["amount"]} for d in res["duplicates"]],
            "risks": [{"rule": x["rule"], "concept": x["concept"],
                       "issue": x["issue"], "line": x.get("line")} for x in res["risks"]],
            "recon": res.get("recon"),
            "pricing": {k: res["pricing"][k] for k in ("system", "label", "confidence")},
            "obligations": [{"id": o["id"], "title": o["title"],
                             "severity": o["severity"], "amount": o["amount"]}
                            for o in res.get("obligations") or []],
        },
    }
    return record, q, res


def ledger_payload(record):
    """账本里存什么：只存「能定位到这份记录」的最小充分集。

    刻意不存整份结论——账本是日志，不是数据库。
    要复算就按 record 里的哈希去还原输入、标准、代码。
    """
    return {
        "input_sha256": record["input_sha256"],
        "standards_digest": _map_digest(record["standards_sha256"]),
        "code_digest": _map_digest(record["code_sha256"]),
        "result_digest": record["digest"],
        "label": record["label"],
        "template_key": record["template_key"],
        "scope": record["scope"],
    }


def ledger_identity(record):
    """链上身份：由**内容**决定，不含显示名。

    同一份 CSV 标签换成「翡翠台」或「feitai_budget」，判定是同一个，
    链上也应该是同一条——否则换个名字就能造出一条新记录。
    """
    return "|".join([
        record["input_sha256"],
        _map_digest(record["standards_sha256"]),
        _map_digest(record["code_sha256"]),
        record["digest"],
    ])


def _map_digest(m):
    body = json.dumps(m, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()
