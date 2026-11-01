#!/usr/bin/env bash
# 一条命令跑完全部可复现检查。评委 / CI / 换机复现都用它。
# 用法：bash scripts/check_all.sh
set -euo pipefail
cd "$(dirname "$0")/.."

PY="${PYTHON:-python3}"
echo "== 1/7 语法编译"
"$PY" -m compileall -q main.py src eval scripts tests

echo "== 2/7 单元测试"
"$PY" -m unittest discover -s tests

echo "== 3/7 本体对齐覆盖率"
"$PY" eval/eval_alignment.py >/dev/null
echo "   ok（out/alignment_report.md）"

echo "== 4/7 黄金标注评测（精确率/召回率 + Wilson CI）"
"$PY" eval/golden_eval.py >/dev/null
echo "   ok（out/golden_eval.md）"

echo "== 5/7 消融实验（L0→L3）"
"$PY" eval/ablation.py >/dev/null
echo "   ok（out/ablation.md）"

echo "== 6/7 注入实验（197 例已知缺陷形态）"
"$PY" eval/injection_test.py >/dev/null
echo "   ok（eval/injection_runs/）"

echo "== 7/7 冻结指纹 + 隐私扫描"
"$PY" scripts/snapshot_predictions.py --check
"$PY" scripts/pii_scan.py --strict

echo
echo "[ALL GREEN] 全部检查通过。"
echo "提示：公网/本地 Demo 用 python3 -m uvicorn main:app --host 0.0.0.0 --port 8000"
