# RQAB — Renovation Quote Audit Benchmark（装明白 · 家装报价可计算审计基准）

> 状态：**v0 协议与工具就绪**，样本扩充中。当前只发布 2 份完整双人可复核标注，绝不以 v0 冒充完整基准。
> 目标：把「AI 审装修报价」从演示变成可比较、可复算、可公布的测量任务。

## 1. 为什么需要一个基准

同类开源方案（RenoPit / ai-smart-audit / OpenConstructionERP）大多以「上传文档 → 模型生成报告」为产品形态，
结论质量难以横向比较。RQAB 把任务定义成可测量的三件事：

1. **漏项判定**：给定房型模板与一份报价单，逐项判定 `present / missing / not_applicable`；
2. **重复计价与口径**：给出行号、概念 ID 与依据；
3. **证据可追溯性**：每条结论必须能回到原表行、规则 ID 与计算公式。

评价的不是「报告读起来像不像专家」，而是「结论能不能被逐条核实」。

## 2. 数据与标注协议

- 单据：`data/real/`（公开装修日记脱敏转录）与 `data/samples/`（结构取自真实晒单、金额重制）。
- 模板：`data/templates_v0.json`（老房 39 项 / 新房 28 项）。
- 标注字段：`cid, quote_doc, golden_state, first_annotator, second_annotator, arbitrated, note`。
- 枚举：`present`（含别名/备注证据词）、`missing`（真漏项候选）、`not_applicable`（口径不适用，不计 FP）。
- 流程：**双人盲标 → 三元组一致率 ≥0.80 入库 → 第三人仲裁分歧 → 版本化回刷**。
- 工具：`eval/cohen_kappa.py`（观察一致率 + Cohen's κ）、`eval/golden_eval.py`（auto/all 两档 P/R + Wilson 95%CI）。

> 当前真实标注：2 份（feitai、guijiao124），单一标注者，只能作存在性证明，不能外推总体召回率。

## 3. 评测口径（固定，不许事后换尺子）

| 档位 | 定义 | 是否进核减金额 |
|---|---|---|
| `auto` | 只算 tier=auto 的确定性漏项 | 是 |
| `all` | auto + review（进待确认清单） | 否 |

同时报两档，避免选择性展示。小样本用 Wilson 区间，不用正态近似。
除 P/R 外，另报三个**不依赖标注**的确定性指标：检测下限（`detection_floor.py`）、
注入检出（`injection_test.py`，下限校验非召回）、判定指纹跨环境一致性（`determinism_matrix.py`）。

## 4. 基线

| 基线 | 命令 | 说明 |
|---|---|---|
| 规则 + 坐标系（本作品） | `python3 benchmark/run_baselines.py` | 确定性，离线可跑 |
| LLM 直读 | `MAAS_API_KEY=… python3 eval/ab_llm_vs_rules.py --provider maas` | 报跨次一致性 Jaccard、幻觉候选、与规则差集 |
| LLM 直读（mock） | `python3 eval/ab_llm_vs_rules.py` | **仅验证统计逻辑，不产生实验结论** |

真实模型 A/B 需要 MaaS Key；没有 Key 时脚本明确拒绝，不用 mock 数字冒充实验结果。

## 5. 扩充标注

```bash
# 为一份新单据生成空白标注表（按房型模板逐项列 cid）
python3 benchmark/make_annotation_sheet.py \
  --csv data/real/huaxibeian_newhouse.csv \
  --doc huaxibeian --template new_house \
  --out benchmark/annotations/huaxibeian.labels.csv

# 两位标注人各自填写后比对
python3 eval/cohen_kappa.py \
  --a benchmark/annotations/huaxibeian.A1.labels.csv \
  --b benchmark/annotations/huaxibeian.A2.labels.csv
```

生成好的空白表在 `benchmark/annotations/`，已是 `cid` 逐项齐全、枚举值固定的可填写模板。

## 6. 提交与复现

- 提交格式：`benchmark/results/<run_id>.json`，含命令、退出码、时长、脚本 sha256 与原始输出路径。
- 所有结果由 `run_baselines.py` 自动生成，不允许手写数字进 leaderboard。
- 隐私：只发布脱敏转录与重制金额；不发布业主姓名、电话、地址或原始照片。

## 7. 当前结果

见 [leaderboard.md](./leaderboard.md)。n=2 的区间很宽，这是事实，扩容后按同一口径重跑即可。
