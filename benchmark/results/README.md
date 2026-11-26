# 评测原始结果留痕（results/）

本目录存放 `benchmark/leaderboard.md` 与陈述材料点名的**原始结果 JSON**，供评委复核数字来源。

| 文件 | 对应主张 | 采集时间 | 采集方式 |
|---|---|---|---|
| `llm_ab_20261122_163735.json` | 真实 MaaS 直读 A/B：3 单据 × 3 次，漏项集合两两 Jaccard 0.00–0.17，规则侧指纹恒同 | 2026-11-22 | `python3 benchmark/run_llm_ab.py --provider maas --runs 3`，服务器实测 |
| `reliability_20261122_161113.json` | 不可能任务弃答 3/3 正确、误拒 0/3、同单 pass^3 2/2 | 2026-11-22 | `python3 benchmark/reliability.py`，服务器实测 |
| `20261121_192045_v0.json` | 基线首版跑批快照 | 2026-11-21 | `python3 benchmark/run_baselines.py` |

## 真实性说明

- JSON 由脚本直接写出（非手工编辑）：每条 `docs[]` 带 `runner_sha256` 与 `returncode`，`reliability` 带逐条 `fingerprints` 与 `summary`。
- MaaS 调用为真实网络调用（`temperature 0.7`，未压温度）；跨次差异是实测结果。
- **脱敏**：采集机的绝对路径前缀统一替换为 `$REPO`；原始证据中的判定数值、指纹、runner 哈希、时间戳均逐字保留，未改动。
- 服务器采集环境：Python 3.12.3（见 `reliability` 的 `python` 字段）。评委可在本地重跑 `run_llm_ab.py`（需自有 MaaS Key）或 `reliability.py`（零依赖、无需 Key）复现。

> 无法提供自有 MaaS Key 时，`reliability.py` 与所有 label-free 指标（确定性指纹、注入、检测下限）仍可离线复算；真 LLM A/B 的跨模型数值以本目录留痕为准。
