# -*- coding: utf-8 -*-
"""RQAB 基线留痕：跑一遍确定性指标，把命令/退出码/时长/sha256 与目标输出写进 results。

不联网、不调用模型；LLM 直读基线由 eval/ab_llm_vs_rules.py 单独跑（需 MAAS_API_KEY）。
用法：
    python3 benchmark/run_baselines.py
    python3 benchmark/run_baselines.py --tag pre_expand
"""
import argparse
import datetime
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "benchmark" / "results"

RUNS = [
    ("golden_eval", ["python3", "eval/golden_eval.py", "--json",
                     "benchmark/results/golden_eval.json"]),
    ("ablation", ["python3", "eval/ablation.py"]),
    ("detection_floor", ["python3", "eval/detection_floor.py", "--json"]),
    ("injection_test", ["python3", "eval/injection_test.py", "--quiet"]),
]


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="")
    args = ap.parse_args()

    RESULTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    run_id = stamp + ("_" + args.tag if args.tag else "")
    record = {
        "run_id": run_id,
        "python": sys.version.split()[0],
        "cwd": str(ROOT.name),
        "runs": [],
    }

    for name, cmd in RUNS:
        script = ROOT / cmd[1]
        started = time.time()
        try:
            p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=600)
            rc, out, err = p.returncode, p.stdout, p.stderr
        except subprocess.TimeoutExpired:
            rc, out, err = 124, "", "timeout after 600s"
        dur = round(time.time() - started, 3)
        (RESULTS / f"{name}.stdout.txt").write_text(out, encoding="utf-8")
        (RESULTS / f"{name}.stderr.txt").write_text(err, encoding="utf-8")
        record["runs"].append({
            "name": name,
            "command": " ".join(cmd),
            "returncode": rc,
            "seconds": dur,
            "script_sha256": sha256(script),
            "stdout": f"benchmark/results/{name}.stdout.txt",
            "stderr": f"benchmark/results/{name}.stderr.txt",
        })
        print(f"[{name}] rc={rc} {dur}s")

    out = RESULTS / f"{run_id}.json"
    out.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"✓ {out.relative_to(ROOT)}")
    return 0 if all(r["returncode"] == 0 for r in record["runs"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
