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
- 固定 split 与排除规则见 [splits.json](./splits.json)：同源 OCR 变体与模拟变更版不得当独立样本。
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

真实模型跑批建议先做前置检查，脚本会明确拒绝在缺 Key 时运行，也不会拿 mock 冒充实验：

```bash
python3 benchmark/run_llm_ab.py --check
MAAS_API_KEY=... python3 benchmark/run_llm_ab.py
```

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

## 6. 可靠性指标：弃答与 pass^k

借鉴 FinTrace（不可能任务的正确答案是「不知道」）与 Tessera / Claw-Eval（严格 pass^k）：

```bash
python3 benchmark/reliability.py
```

- **弃答**：3 个不可能任务（合同文本、购物清单、只有表头的 CSV）必须被拒绝；
  3 个正常单据不得误拒。服务器实测 **正确弃答 3/3、误拒 0/3**。
- **pass^k**：同一单据跑 3 次，判定指纹逐字节一致才算过。服务器实测 **2/2 文档 pass^3**。

> 这两项都不调用模型：拒绝是守门的结构行为，一致性来自确定性引擎。
> 不把它们写成「模型很稳」，而是写成「架构不允许它乱说」。

## 7. MCP 接口：让智能体直接调用审计

`mcp_server.py` 是零第三方依赖的 MCP server（stdio / JSON-RPC 2.0），
对标 `modelcontextprotocol/python-sdk`（24.9k★，MIT），协议帧与其 stdio transport 兼容：

```bash
python3 mcp_server.py --self-test     # 自握手 + 列工具 + 跑一次审计 + 读资源
python3 mcp_server.py                 # 作为 MCP server 启动
```

暴露 3 个工具（`classify_quote` / `audit_quote` / `list_samples`）、3 个资源与 1 个标准审查 prompt。
路径限制在项目目录内，所有日志只写 stderr——stdout 专供协议帧。

## 8. 提交与复现

- 提交格式：`benchmark/results/<run_id>.json`，含命令、退出码、时长、脚本 sha256 与原始输出路径。
- 所有结果由 `run_baselines.py` 自动生成，不允许手写数字进 leaderboard。
- 隐私：只发布脱敏转录与重制金额；不发布业主姓名、电话、地址或原始照片。

## 9. 当前结果

见 [leaderboard.md](./leaderboard.md)。n=2 的区间很宽，这是事实，扩容后按同一口径重跑即可。

## 10. 方法参考（2026）

| 项目 | 借鉴点 |
|---|---|
| [FinTrace](https://github.com/FredXing1/FinTrace)（Apache-2.0, v1.0） | auditpack：claims + evidence chains + execution log + sign-off；不可能任务正确答案是「unknown」；结构门禁比提示词有效 |
| [Tessera](https://github.com/rinaldofesta/tessera) | 严格 pass^k 作 headline；按 agent 真实 MCP 流量评 provenance；「拒绝」作为可评分行为 |
| [Claw-Eval](https://github.com/claw-eval/claw-eval) | 三次试验全过才算过；轨迹级审计（Completion / Safety / Robustness） |
| [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)（MIT, 24.9k★） | stdio / JSON-RPC 帧格式、tools/resources/prompts 三原语；本项目用标准库实现最小兼容子集 |
