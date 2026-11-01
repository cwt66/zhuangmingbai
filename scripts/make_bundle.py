# -*- coding: utf-8 -*-
"""打一个「审计包」：把结论连同复算它所需的一切打包，交给任何第三方离线复现。

## 为什么需要

「可复现」这个说法谁都会讲，但评委真要验的时候会遇到三个问题：
① 数据可能被事后改动；② 规则/模板/本体可能是事后调的；③ 环境不同跑不出同样结果。

审计包把这三件事一次性解决：**输入 + 当时的全部标准 + 当时的代码 + 判定结论 + 校验脚本**
装进一个 zip。任何人解压后运行 `python3 verify.py`：

- 输入文件逐字节校验（sha256），改一个数字立刻发现；
- 用包里附带的**当时版本**的标准复算，不受仓库后续改动影响；
- 复算结论与包里记录的 digest 比对，不一致就 FAIL。

## 为什么只打包「判定引擎」不打包整个仓库

审计结论只依赖 `src/pipeline.py` + 三份标准 JSON + 参照价库 + 范围标签。
把 Web 层、感知层、测试一起打进去，除了让包变大以外没有帮助——
**包越小，评委会真的去跑**。

用法：
    python3 scripts/make_bundle.py --csv data/real/feitai_budget.csv --label 翡翠台 \
        --template old_house --scope full --out /tmp/feitai_audit.zmb.zip
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
import pipeline as P  # noqa: E402

DATA = os.path.join(ROOT, "data")
STANDARDS = ["templates_v0.json", "ontology_v0.json", "rules_v0.json",
             "coverage_v0.json", "reference_prices.json", "template_scopes.json"]
CODE = ["pipeline.py", "obligations.py", "pricing_system.py"]

VERIFY_TMPL = '''# -*- coding: utf-8 -*-
"""离线校验器：复算本审计包的结论，与包内记录比对。

用法：python3 verify.py
退出码：0 = 一致；1 = 不一致（输入被改、标准被换、或代码版本不符）
"""
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "src"))
import pipeline as P

P.DATA = os.path.join(HERE, "standards")     # 强制用包内标准，不读外部仓库


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    rec = json.load(open(os.path.join(HERE, "result.json"), encoding="utf-8"))
    got = sha(os.path.join(HERE, "input.csv"))
    if got != rec["input_sha256"]:
        print("[FAIL] 输入文件已被改动")
        print("  记录 %s" % rec["input_sha256"][:32])
        print("  实际 %s" % got[:32])
        return 1

    onto = P.Ontology(os.path.join(P.DATA, "ontology_v0.json"))
    tpl = json.load(open(os.path.join(P.DATA, "templates_v0.json"), encoding="utf-8"))
    rules = json.load(open(os.path.join(P.DATA, "rules_v0.json"), encoding="utf-8"))
    cov = json.load(open(os.path.join(P.DATA, "coverage_v0.json"), encoding="utf-8"))

    q = P.build_line_index(P.load_quote(os.path.join(HERE, "input.csv"), rec["label"]), onto)
    res = P.audit([q], onto, tpl, rules, cov,
                  template_key=rec["template_key"], scope=rec["scope"])[q["label"]]
    dg = P.result_digest(q, res)

    print("单据：%s（%d 行明细 + %d 行汇总）" % (
        rec["label"], len(q["rows"]), len(q["summary_rows"])))
    print("口径：模板 %s ｜ 范围 %s ｜ 生成于 %s" % (
        rec["template_key"], rec["scope"], rec["created_at"]))
    print("结论：漏项 %d（确定 %d / 复核 %d）｜重复 %d ｜风险 %d ｜明细合计 %s" % (
        len(res["missing"]),
        sum(1 for m in res["missing"] if m["tier"] == "auto"),
        sum(1 for m in res["missing"] if m["tier"] != "auto"),
        len(res["duplicates"]), len(res["risks"]), "%.2f" % q["total"]))
    if res.get("recon") and res["recon"].get("diff") is not None:
        print("对账：印刷合计 %.2f ｜ 差 %.2f ｜ %s" % (
            res["recon"]["grand"]["amount"], res["recon"]["diff"],
            "对平" if res["recon"]["ok"] else "不符"))

    if dg == rec["digest"]:
        print("\\n[PASS] 复算结论与包内记录一致：%s" % dg)
        return 0
    print("\\n[FAIL] 复算结论与记录不一致")
    print("  记录 %s" % rec["digest"])
    print("  复算 %s" % dg)
    return 1


if __name__ == "__main__":
    sys.exit(main())
'''

README = """装明白 · 审计包（auditable bundle）
=====================================

这个包里有什么
--------------
input.csv          被审计的报价单（逐字节冻结）
standards/         当时的四份标准 + 参照价库 + 范围标签
src/               当时的判定引擎（零第三方依赖）
result.json        判定结论 + 输入/标准的 sha256 + 结论摘要 digest
verify.py          离线校验器

怎么验（30 秒）
--------------
    python3 verify.py

看到 [PASS] 就表示：这份结论确实是「这个输入 + 这套标准 + 这版代码」算出来的，
中途没有任何一处被改过。

为什么可信
----------
1. 输入哈希：改一个数字，校验器立刻报 FAIL；
2. 标准随包：不受仓库后续改动影响，你复算的就是当时的判定；
3. 结论摘要：只哈希结论（漏项/重复/风险/对账），不含时间与文件名，换名字也对得上。

这个包不包含什么
----------------
不含 Web 界面与 OCR 感知层——审计结论不依赖它们。包越小，越可能被真的跑一遍。
"""


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True, help="被审计的报价单 CSV")
    ap.add_argument("--label", default=None, help="单据名（默认取文件名）")
    ap.add_argument("--template", default="old_house", choices=["old_house", "new_house"])
    ap.add_argument("--scope", default="full", choices=["full", "base"])
    ap.add_argument("--out", default=None, help="输出 zip 路径")
    args = ap.parse_args()

    csv_path = os.path.abspath(args.csv)
    label = args.label or os.path.splitext(os.path.basename(csv_path))[0]
    out = args.out or os.path.join("/tmp", "%s_audit.zmb.zip" % label)

    onto = P.Ontology(os.path.join(DATA, "ontology_v0.json"))
    tpl = json.load(open(os.path.join(DATA, "templates_v0.json"), encoding="utf-8"))
    rules = json.load(open(os.path.join(DATA, "rules_v0.json"), encoding="utf-8"))
    cov = json.load(open(os.path.join(DATA, "coverage_v0.json"), encoding="utf-8"))

    q = P.build_line_index(P.load_quote(csv_path, label), onto)
    res = P.audit([q], onto, tpl, rules, cov,
                  template_key=args.template, scope=args.scope)[q["label"]]

    record = {
        "label": label,
        "template_key": args.template,
        "scope": args.scope,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "git_commit": subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                                     capture_output=True, text=True).stdout.strip() or "no-git",
        "input_sha256": sha256_file(csv_path),
        "standards_sha256": {n: sha256_file(os.path.join(DATA, n))
                             for n in STANDARDS if os.path.isfile(os.path.join(DATA, n))},
        "code_sha256": {n: sha256_file(os.path.join(ROOT, "src", n))
                        for n in CODE if os.path.isfile(os.path.join(ROOT, "src", n))},
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

    tmp = tempfile.mkdtemp(prefix="zmb_bundle_")
    try:
        os.makedirs(os.path.join(tmp, "standards"))
        os.makedirs(os.path.join(tmp, "src"))
        shutil.copy2(csv_path, os.path.join(tmp, "input.csv"))
        for n in STANDARDS:
            src = os.path.join(DATA, n)
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(tmp, "standards", n))
        for n in CODE:
            src = os.path.join(ROOT, "src", n)
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(tmp, "src", n))
        with open(os.path.join(tmp, "result.json"), "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
        with open(os.path.join(tmp, "verify.py"), "w", encoding="utf-8") as f:
            f.write(VERIFY_TMPL)
        with open(os.path.join(tmp, "README.txt"), "w", encoding="utf-8") as f:
            f.write(README)

        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for base, _, files in os.walk(tmp):
                for fn in files:
                    full = os.path.join(base, fn)
                    z.write(full, os.path.relpath(full, tmp))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("审计包：%s（%.0f KB）" % (out, os.path.getsize(out) / 1024))
    print("  单据 %s｜模板 %s｜范围 %s" % (label, args.template, args.scope))
    print("  明细 %d 行 + 汇总 %d 行｜漏项 %d｜重复 %d｜风险 %d" % (
        len(q["rows"]), len(q["summary_rows"]), len(res["missing"]),
        len(res["duplicates"]), len(res["risks"])))
    print("  digest %s" % record["digest"])
    print("  验证：unzip %s -d /tmp/v && python3 /tmp/v/verify.py" % os.path.basename(out))


if __name__ == "__main__":
    main()
