# -*- coding: utf-8 -*-
"""LLM 直读 vs 规则 A/B 的前置包装：无 Key 明确拒绝，绝不拿 mock 当实验。

用法：
    python3 benchmark/run_llm_ab.py --check          # 只检查前置条件
    MAAS_API_KEY=... python3 benchmark/run_llm_ab.py  # 真实跑批（3 份 × 3 次）

产物：eval/ab_runs/ 下的原始 JSON 与报告；本脚本只负责前置检查、留痕与汇总，
不修改 eval/ab_llm_vs_rules.py 的统计口径。
"""
import argparse
import datetime
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNNER = ROOT / "eval" / "ab_llm_vs_rules.py"
DOCS = [
    ("data/real/feitai_budget.csv", "old_house"),
    ("data/real/guijiao_124_basic.csv", "new_house"),
    ("data/real/sh_zhuoguan_halfpack.csv", "old_house"),
]


def check():
    missing = []
    if not os.environ.get("MAAS_API_KEY"):
        missing.append("MAAS_API_KEY 未设置（真实模型跑批必须）")
    if not RUNNER.is_file():
        missing.append(f"找不到 {RUNNER.relative_to(ROOT)}")
    for csv, _ in DOCS:
        if not (ROOT / csv).is_file():
            missing.append(f"找不到单据 {csv}")
    return missing


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只做前置检查，不跑批")
    ap.add_argument("--runs", type=int, default=3)
    args = ap.parse_args()

    missing = check()
    if missing:
        print("前置条件不满足，未执行：")
        for m in missing:
            print(" -", m)
        print("\nmock 模式只能验证统计逻辑，不产生实验结论；不会用它替代真实 A/B。")
        return 2
    if args.check:
        print("[OK] 前置条件齐备，可跑真实 LLM A/B")
        return 0

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    record = {"run_id": stamp, "provider": "maas", "runs_per_doc": args.runs, "docs": []}
    for csv, template in DOCS:
        cmd = [sys.executable, "eval/ab_llm_vs_rules.py", "--provider", "maas",
               "--csv", csv, "--template", template, "--runs", str(args.runs)]
        p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=1800)
        record["docs"].append({
            "csv": csv, "template": template, "returncode": p.returncode,
            "stdout_tail": p.stdout[-1200:], "stderr_tail": p.stderr[-600:],
            "runner_sha256": hashlib.sha256(RUNNER.read_bytes()).hexdigest(),
        })
        print(f"[{csv}] rc={p.returncode}")
    out = ROOT / "benchmark" / "results" / f"llm_ab_{stamp}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"✓ {out.relative_to(ROOT)}")
    return 0 if all(d["returncode"] == 0 for d in record["docs"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
