# RQAB 双人标注 Round 1 执行计划

制定：2026-11-26。目标：12 月 15 日初赛提交前，把 RQAB 从「2 份单人标注（存在性证明）」升级到「7 份双人盲标 + κ 报告」。

## 0. 现状（2026-11-26 核对）

- 已有金标 2 份，单一标注者：feitai（老房模板，39 项）、guijiao124（新房模板，28 项）；auto 档 P/R 0.833/0.833，Wilson 95%CI 0.552–0.953（区间很宽，这是 n=2 的事实）。
- 空白标注表已生成 5 份：huaxibeian（新房，28 项）、sh_zhuoguan / company_a / company_b / company_c（老房，各 39 项），共 184 项。
- 门槛（splits.json gates）：双人盲标、三元组一致率 ≥0.80、Cohen's κ ≥0.60；v1 要求 12 份。
- 工具就绪：make_annotation_sheet.py、eval/cohen_kappa.py、eval/golden_eval.py、benchmark/run_baselines.py。

## 1. 本轮范围：7 份

| 单据 | 模板 | 项数 | A1 | A2 |
|---|---|---|---|---|
| feitai | 老房 | 39 | 已有金标 | 盲标 B 表（新建） |
| guijiao124 | 新房 | 28 | 已有金标 | 盲标 B 表（新建） |
| huaxibeian | 新房 | 28 | A1 表 | A2 表 |
| sh_zhuoguan | 老房 | 39 | A1 表 | A2 表 |
| company_a | 老房 | 39 | A1 表 | A2 表 |
| company_b | 老房 | 39 | A1 表 | A2 表 |
| company_c | 老房 | 39 | A1 表 | A2 表 |

工作量：A1 标 184 项（5 份新表）；A2 标 251 项（5 份新表 + 2 份补标）。按每项 1–2 分钟，A1 约 3–6 小时，A2 约 4–8 小时，分 4 天做完。

## 2. 分工（建议，可调）

- 标注人 A1：Wzy。已标过前 2 份，口径锚点。
- 标注人 A2：高恒。盲标，此前没看过任何金标。
- 仲裁人：占怡晨。分歧裁决，写 arbitrated 列；重大分歧可请段鹏飞复核。
- A2 与仲裁人可对调（高恒↔占怡晨），但一人不能既当 A2 又当仲裁。

## 3. 盲法规则（违反即作废重标）

1. 各自只打开自己的文件（*.A1.* / *.B.* / *.A2.*），标注期间不看对方文件、不讨论、不对答案。
2. A2 尤其不能打开 eval/golden_labels_feitai.csv 与 eval/golden_labels_guijiao124.csv。
3. 每条结论落到单据行号 + 工序 cid；不确定的写「待定」，在 note 里写疑问，不要猜。
4. 枚举只用 present / missing / not_applicable；not_applicable 不计 FP。
5. 分歧不私下协商，一律进仲裁记录，由第三人裁决。

## 4. 时间表（11-26 → 12-15）

| 日期 | 事项 |
|---|---|
| 11-27–11-30 | 两人独立标注（每天约 1–2 小时） |
| 12-01 | 跑一致率：cohen_kappa.py（见 §5） |
| 12-02–12-03 | 仲裁分歧，定稿金标 |
| 12-04 | golden_eval.py + run_baselines.py 重跑，更新 leaderboard.md 与 splits.json |
| 12-05 | 写入初赛材料：自评表应用价值/创新性、PPT 一页 |
| 12-15 18:00 | 初赛提交截止（缓冲 10 天） |

## 5. 跑批命令

```bash
# 逐份比对（每份都要过门槛）
python3 eval/cohen_kappa.py --a benchmark/annotations/huaxibeian.A1.labels.csv --b benchmark/annotations/huaxibeian.A2.labels.csv
# 仲裁定稿后重评
python3 eval/golden_eval.py
python3 benchmark/run_baselines.py
```

## 6. 完成标准

- 7 份全部双人标注，一致率 ≥0.80 且 κ ≥0.60，否则不入库、不写成结论（只报「未达标，正在仲裁/重标」）。
- leaderboard 更新：n=2 单标 → n=7 双人盲标，P/R + Wilson 区间重算。
- splits.json 更新：7 份状态改为 complete_double_blind，annotators=2。
- 初赛材料里只写已完成的部分，不提前写 v1。

## 7. 诚实边界

- 本轮做完是 7/12，v1 的 12 份门槛还差 5 份。缺口来源：试点家庭真实报价单（招募进行中，见 2026-11-26 的试点招募计划）或再补样本单据；data/real/taocan_standard_32.csv 未进 splits.json，需先核实是否为独立报价单再决定。
- sh_zhuoguan 已在 11-22 跑过 LLM A/B（Jaccard 0.00），但「LLM 独有 / 规则独有」至今无人工真值裁定；本轮双人标注后才能判对错，在此之前不写谁对谁错。
- company_a/b/c 是样本单据（金额重制），不是真实业主单据；写材料时如实区分「真实单据」与「样本单据」。
