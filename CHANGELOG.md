# 变更记录

## v0.5.0 — 2026-11-02（本地感知与质量门）

### 新增
- **macOS Vision 本地 OCR**（`scripts/vision_ocr.m` + `src/vision_ocr.py`）：图片不出本机；
  表头推断列、数量/单价锚点聚行、critic 标记缺字段/量价不符/低置信。
- **识别草稿 → 人工校对 → 审计**：`/perceive` 在需核对比例 >35% 时返回校对页，
  改完 CSV 再 `POST /vision/audit`。
- **用户反馈闭环**：`/feedback` 写入 RDS/SQLite，`/report/{id}` 展示。
- **评测门禁**：`eval/gate.py`，auto P/R <0.75、all R <0.90、注入 <230、消融 L2 <0.75 即失败。
- **一键打印/PDF**：报告页 `window.print()`，打印样式隐藏反馈/导航。

### 修复
- 注入实验真值：`water_pipe` 被 `mep` 吸收时删除不产生漏项，原用例把测试构造问题记成引擎漏检；
  已排除并重跑 **242/242**（原 197 为旧口径/旧上限）。
- 本地 OCR 金额框重复进备注；单位列漂移（数量占位/单位并入项目名）加修复。

### 测试
- 90 → **97**；新增 `tests/test_vision.py` 7 例。

## v0.4.0 — 2026-11-01（国奖冲刺版）

### 新增
- **RDS PostgreSQL 持久化**：`src/store.py`，审计摘要落库，`/history`、`/report/{id}`、`/api/audits`。
- **DCS Redis 能力层**：`src/cache.py`，结果缓存 / IP 限流 / 任务状态；无 Redis 自动降级。
- **MaaS 表达层**：`src/llm.py`，只转写规则事实；输出接地校验，事实外数字拒收；模板兜底并标注来源。
- **隐私与注入防护**：`src/privacy.py`，发送前脱敏、不可信文本隔离、注入识别。
- **黄金标注评测**：`eval/golden_eval.py`，auto/all 两档 + Wilson 95% CI。
- **消融实验**：`eval/ablation.py`，L0→L3 逐级证明坐标系/证据词/范围裁剪的贡献。
- **隐私扫描**：`scripts/pii_scan.py`，CI 硬门槛。
- **一键自检**：`scripts/check_all.sh`；GitHub Actions CI。
- **本地云原生栈**：`docker-compose.yml`（PostgreSQL + Redis + App）。
- **文档**：`docs/ARCHITECTURE.md`、`docs/BENCHMARK.md`、`SECURITY.md`、`CONTRIBUTING.md`、`docs/ROADMAP.md`。

### 修复
- `/perceive` 页面曾渲染成首页（`__BODY__` 占位符未替换）——真实 Demo 缺陷，已修并加导航。
- 缓存内存实现的 `Lock` 自锁死（`rate_limit` 持锁调用 `_mem_get`）——测试抓出，已改 `RLock`。
- 错把 `missing["name"]` 当字段（实际是 `concept`），导致解读出现 `None`——已修。
- PDF 用例在无 `pypdf` 环境下误报：改为 skip 并加入 requirements。

### 口径
- 真实单据 5 份 / **603 行工序明细 + 11 行汇总**；翡翠台 123 行（+8 汇总），124㎡ 65 行（+3 汇总）。
- 测试 76 → **90**；三份冻结指纹不变；三单回归 400 / 22,164 / 3,168 不变。

## v0.3.0 — 2026-11-01

- 四项创新落地：范围声明裁剪 / 审计包+离线校验 / 计价体系识别 / 条款→义务→验收证据。
- 感知层闭环量化、RenoPit 对照协议 v1.1。
- 测试 37 → 76。

## v0.2.0 — 2026-10-31

- 汇总行分层、文档级对账 R13、同工序参照价库。
- 真实单据 5 份；not_applicable 三分类。
- 测试 26 → 37。

## v0.1.0 — 2026-10-30

- 审计引擎、本体、模板、四 provider 感知层、在线 Demo、离线包。
- 三份合成样例回归线 400 / 22,164 / 3,168。
