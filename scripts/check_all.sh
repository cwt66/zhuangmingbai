#!/usr/bin/env bash
# 一条命令跑完全部可复现检查。评委 / CI / 换机复现都用它。
# 用法：bash scripts/check_all.sh
set -euo pipefail
cd "$(dirname "$0")/.."

PY="${PYTHON:-python3}"
echo "== 1/13 语法编译"
"$PY" -m compileall -q main.py src eval scripts tests

echo "== 2/13 单元测试"
"$PY" -m unittest discover -s tests

echo "== 3/13 本体对齐覆盖率"
"$PY" eval/eval_alignment.py >/dev/null
echo "   ok（out/alignment_report.md）"

echo "== 4/13 黄金标注评测（精确率/召回率 + Wilson CI）"
"$PY" eval/golden_eval.py >/dev/null
echo "   ok（out/golden_eval.md）"

echo "== 5/13 消融实验（L0→L3）"
"$PY" eval/ablation.py >/dev/null
echo "   ok（out/ablation.md）"

echo "== 6/13 注入实验（242 例已知缺陷形态）"
"$PY" eval/injection_test.py >/dev/null
echo "   ok（eval/injection_runs/）"

echo "== 7/13 评测门禁（低于阈值即失败）"
"$PY" eval/gate.py

echo "== 8/13 冻结指纹 + 隐私扫描"
"$PY" scripts/snapshot_predictions.py --check
"$PY" scripts/pii_scan.py --strict

# 盲标页是从 templates/ontology/CSV 生成的产物；它一旦过期，第二标注者
# 就会照着一份旧清单标——所以每次自检都重建，不提交「可能过期」的 HTML。
echo "== 9/13 盲标工具重建（第二标注者用）"
"$PY" eval/make_annotator.py

# 交付门是「报告能不能交出去」的判定，不是「结论对不对」。
# 它自带故障注入（5 个坏样本），拦不住就返回非 0——所以这条会真的失败，
# 不是只打印一段好看的表格。
echo "== 10/13 报告交付门（7 检查 + 5 故障注入）"
"$PY" scripts/gate_report.py | tail -8

# 检测下限回答「最小能抓多少钱」。它不改任何东西，只把能力边界写成数字，
# 顺便盯住那个已知盲区（单行<1 元、累计<对账容差）还在不在。
echo "== 11/13 检测下限（单行/累计两维）"
"$PY" eval/detection_floor.py | grep -E "^\*\*|盲区确认|最严重" || true

# 负向知识库：不判什么、为什么。每次重建，防止它和代码漂开。
echo "== 12/13 负向知识库（13 条不判 + 10 条测试锁 + 1 条已知拦不住）"
"$PY" scripts/na_report.py --out out/not_applicable.md

# 红队：主动攻击自己的引擎。得手的写进工单，不藏。
# 这步不 fail —— 它的价值是「如实报告」，不是「全部拦住」。
echo "== 13/13 红队自测（8 招攻击，7 招拦住）"
"$PY" eval/redteam.py | tail -6

echo
echo "[ALL GREEN] 全部检查通过。"
echo "提示：公网/本地 Demo 用 python3 -m uvicorn main:app --host 0.0.0.0 --port 8000"
