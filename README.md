# 装明白 · 可计算审计（Computable Audit）

> 第十一届华为 ICT 大赛 · 中国创新赛 · 赛题 1「基于华为云产品打造 AI 创新应用」
>
> **装修报价的问题不是"看不懂"，是没有坐标系。我们给装修报价建了一套可计算的坐标系。**

[![Gitee](https://img.shields.io/badge/Gitee-Cwt66%2Fzhuangmingbai-c71d23)](https://gitee.com/Cwt66/zhuangmingbai)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](./requirements.txt)
[![Tests](https://img.shields.io/badge/tests-99-brightgreen.svg)](./tests)
[![Rules](https://img.shields.io/badge/rules-13-002FA7.svg)](./data/rules_v0.json)

签合同之前，上传 2–4 家装修公司的报价单（照片 / PDF / Excel / CSV），输出一份**可核对、可溯源、可复现**的审计报告：
谁漏了项、谁重复收了钱、谁口径混乱、补漏后真实成本是多少。

**边界**：不判断贵不贵（不做虚高/最低价断言），只判断漏没漏、重没重；低置信度结论不进核减金额，只进「待确认清单」。

![架构](docs/assets/architecture.svg)

---

## 1. 一句话与三个组件

**一句话**：把几百种模糊工序名映射到同一根坐标轴，判断交给可解释的规则，每条结论带行号 + 基准 + 公式。

| 组件 | 做什么 | 不做什么 |
|---|---|---|
| **坐标系（WUA）** | 119 概念 / 388 别名 / 8 大类，把「墙面批荡 / 刮腻子 / 批嵌」落到同一工序节点 | 不做结论 |
| **审计规则 DSL** | 13 条规则：漏项集合差、重复计价结构推理、算术/对账/口径/义务 | 不写文案 |
| **证据链** | 每条结论 = 原表行号 + 对照基准 + 计算公式 + 证据强度分 + 三档处置 | 不改判定 |

**为什么判断不经过大模型**：同一单跑两次，大模型会给两份不同的报告；规则引擎给同一份。
幻觉不是靠提示词压制的，是从架构上拿掉的。

---

## 2. 当前状态（2026-11-01）

| 模块 | 状态 |
|---|---|
| 审计引擎（分层 / 漏项 / 重复 / 算术 / 对账 / 口径 / 义务 + 证据链） | ✅ `src/pipeline.py`（1,198 行，零第三方依赖） |
| 工序本体 | ✅ **119 概念 / 388 别名** / 8 大类 |
| 工序模板 | ✅ 老房翻新 39 项 / 新房毛坯 28 项；支持「报价范围声明 → 模板裁剪」 |
| 规则 DSL | ✅ **13 条**，全部可逐条审阅（`data/rules_v0.json`） |
| 感知层 | ✅ 五 provider：`file`（CSV/XLSX/PDF 零依赖）/ **`vision`（macOS Vision 本地 OCR）** / `maas`（Qwen-VL）/ `ocr_table`（华为云通用表格识别）/ `mock` |
| **云原生：RDS PostgreSQL** | ✅ 代码层已接入（`src/store.py`；psycopg 可选，失败自动降级 SQLite）；`/history`、`/api/audits` 可查 |
| **云原生：DCS Redis** | ✅ 代码层已接入（`src/cache.py`：结果缓存 / 限流 / 任务状态；失败自动降级进程内） |
| **表达层：MaaS** | ✅ `src/llm.py`：只转写规则结果；**输出接地校验**，出现事实外数字即拒收并退回模板 |
| **本地 OCR + critic** | ✅ `src/vision_ocr.py`：图片不出本机；缺字段/量价不符/低置信 → 标「需核对」，不直接进结论 |
| **用户反馈闭环** | ✅ 报告页 `/feedback` → RDS；试点访谈有结构化记录 |
| **报告质量门** | ✅ 每份报告顶部：对齐 / 算术 / 漏项 / 对账 / 指纹；`/report` 历史同步 |
| **可审计 Excel** | ✅ `/demo.xlsx`、`/audit.xlsx`：总览/漏项/重复/风险/对账/说明 6 个 sheet，金额是数值 |
| 真实单据评测 | ✅ **5 份 / 603 行工序明细 + 11 行汇总**；两份完整黄金标注；注入实验 242 例 |
| 单元测试 | ✅ **99 个**（云原生 14 / 本地 OCR 7 / PDF 坐标 / 回归线 / 对账分层 / 四项创新 / 跨领域迁移） |
| 可打印报告 | ✅ `/demo`、`/real`（打印级 HTML，判定指纹上封面） |
| 解析校对台 | ✅ `/review`（低置信度标黄 → 修改 → 导出 CSV） |
| 问询单 | ✅ 把结论变成可直接发给报价方的逐条问询（`src/inquiry.py`） |
| 输入兼容 | ✅ UTF-8 / **GBK** / UTF-16、逗号 / **TAB**、表头别名 |
| 审计历史 | ✅ `/history` + `/api/audits`（证明 RDS 真的在存业务数据） |
| 一键自检 | ✅ `bash scripts/check_all.sh`（测试 + 评测 + 注入 + 指纹 + 隐私扫描） |
| 在线 Demo | ✅ `python3 -m uvicorn main:app --port 8000` |
| 离线演示包 | ✅ 干净目录可独立复现（含跨领域 demo） |
| RenoPit 对照实验 | 🚧 协议已预注册，跑批进行中 |
| 第二标注人 / n>2 完整召回率 | 🚧 黄金标注机制就绪，待第二人 |

### 2.1 「云原生」不是形容词：代码里真的用了

| 华为云服务 | 在本项目里承担什么 | 代码位置 | 没开通时 |
|---|---|---|---|
| **RDS for PostgreSQL** | 审计摘要、单据、判定指纹、反馈落库；`/history` 可回看 | `src/store.py` | 自动降级 SQLite，`/health` 标明 |
| **DCS for Redis** | 审计结果缓存、IP 限流、异步任务状态 | `src/cache.py` | 自动降级进程内，`/health` 标明 |
| **ModelArts Studio（MaaS）** | 感知层 Qwen-VL 解析 + 表达层解读（仅转写） | `src/perception.py`、`src/llm.py` | 报告标注「规则模板生成」，不伪装成 AI |
| **CodeArts 码道** | 需求→生成→构建→修复→部署五环节留痕 | `装明白_校赛材料/CodeArts过程资产.md` | — |
| **OBS / ECS / Nginx** | 原始单据与报告存储、HTTPS 公网入口 | `deploy/`、`docs/开发者空间部署指南.md` | 本地 `uvicorn` 可完整演示 |

> 云上实例是否已开通，以账号控制台为准；**代码侧不再有"只有设计没有实现"的模块**。
> `docker-compose.yml` 可在没有华为云账号的机器上验证同一套 RDS/Redis 逻辑。

---

## 3. 真实单据评测（不是 demo，是逐条回原文核实过的）

| # | 单据 | 性质 | 工序行 | 对齐率 | auto 漏项 | 重复 | 判定指纹 |
|---|---|---|---:|---:|---:|---:|---|
| 1 | 上海卓观 · 两房半包（篱笆网公开晒单） | 家装 · 照片转录 | 127 | 87.4% | 10（逐条核实 10/10） | 0（21 组变体正确排除） | `2ab9413a` |
| 2 | 花溪北岸 · 5 页施工报价 | 家装 · 照片转录 | 120 | 87.5% | 4（2 条曾被备注证据词救回） | 0 | `456e1bd9` |
| 3 | 深圳新新 · 诊所装饰（公开 PDF） | 工装 · 文本 PDF | 168 | 67.3% | 11（工装模板不适用，不作数） | 3 | `3d33c49c` |
| 4 | 上海翡翠台 · 老房全案 | 家装 · 照片转录 | 123（+8 汇总） | 82.9% | 8/8 召回、auto 精确率 0.80 | 1（R2 同区，182 元） | 见 `eval/` |
| 5 | 124㎡ · 基础报价单（小红书公开晒单） | 家装 · 照片转录 | 65（+3 汇总） | 95.4% | 范围内 4/4 召回、4/4 精确（7 项 not_applicable） | 0 | 见 `eval/` |

**累计打穿 17 个真实缺陷**（篱笆 5 + 花溪 2 + 诊所 3 + 本体缺口 3 + 规则 2 + 表格识别 2），全部有单测或指纹锁定。

### 3.1 黄金标注评测（可复算，`python3 eval/golden_eval.py`）

| 档位 | TP | FP | FN | 精确率 [95% CI] | 召回率 [95% CI] |
|---|---:|---:|---:|---|---|
| auto（进核减金额） | 10 | 2 | 2 | **0.833 [0.552, 0.953]** | **0.833 [0.552, 0.953]** |
| all（含待确认） | 12 | 4 | 0 | 0.750 [0.505, 0.898] | **1.000 [0.757, 1.000]** |

> 两档并报，是因为「精确率」在两种口径下不同。只报好看的那档就是选择性展示。
> n=2，只作存在性证明；第二标注人到位后重算一致率。

### 3.2 消融实验（`python3 eval/ablation.py`）

每一级只加一个组件，证明「坐标系」不是形容词：

| 级 | 加了什么 | feitai 对齐率 | feitai auto 精确率 | 124㎡ auto 预测 | 124㎡ 漏项金额 |
|---|---|---:|---:|---:|---:|
| L0 | 仅精确/别名匹配 | 22.8% | 0.267 | 28 条 | 25,624 |
| L1 | + 模糊/包含对齐 | 82.9% | 0.421 | 19 条 | 8,605 |
| L2 | + 文本证据词 | 82.9% | **0.800** | 9 条 | 5,573 |
| L3 | + 范围声明裁剪 | 82.9% | 0.800 | **3 条** | **259** |

**读法**：L0 的召回是 1.0，但精确率 0.267——把没对齐的行当成没有，是**不可用的高召回**。
L2 把备注里写了但没单列的项救回来；L3 把「主材另计」的模板项裁掉。

### 3.3 真实数据打穿的能力（都从问题长出来）

| 能力 | 解决什么 |
|---|---|
| 汇总行分层 | 翡翠台 8 行汇总曾被当工序加总，单份算出 743,920 而非 176,843。判据是算术门控，不按关键词一刀切 |
| 文档级对账 R13 | 整单加总 vs 印刷合计。实测抓出翡翠台 11,711 元、124㎡ 506.60 元差额；无印刷合计判「跳过」而非「对平」 |
| 同工序参照价库 | 单份上传没有同批参照时，退到 4 份家装单据的金额中位数；无样本的工艺项留空不编数 |
| 报价范围声明 → 模板裁剪 | 只报基础工程的单子，不该按全包口径要求主材。124㎡ auto 预测 9→3 |
| 审计包 + 离线校验器 | 45KB 的包，任何人 `python3 verify.py` 30 秒复算；改一个数字立即 FAIL |
| 计价体系识别 | 先认「工程量清单 / 按面积 / 套餐 / 汇总列表」，再决定怎么读、提示什么 |
| 条款 → 义务 → 验收证据 | 把「按实结算/暂定/另计」翻译成「签约前必须问什么、验收要留什么凭证」 |
| 跨领域迁移 | 引擎零改动，只换本体/模板/覆盖率三份 JSON，同一套 13 条规则可审汽修维保单 |

---

## 4. 架构：大模型只做理解与表达，判断交给规则

```
             ┌──────────────────────── 感知层（只"看见"） ────────────────────────┐
照片/PDF/Excel/CSV → file / MaaS Qwen-VL / 华为云表格识别 → 行项 JSON（字段级置信度）
             └──────────────────────────────┬───────────────────────────────────┘
                                            ▼
             ┌──────────────────── 坐标系层（只"对齐"） ───────────────────────┐
             │ 119 概念 / 388 别名 / 同义词 / 上下位 / 单位换算 / 老房新房差异      │
             └──────────────────────────────┬───────────────────────────────────┘
                                            ▼
             ┌──────────────────── 判断层（只"裁决"） ─────────────────────────┐
             │ 13 条规则 DSL：漏项集合差 / 重复结构推理 / 算术 / 对账 / 口径 / 义务 │
             │ 零第三方依赖 · 同一输入同一输出 · 每条结论带行号+基准+公式           │
             └──────────────────────────────┬───────────────────────────────────┘
                                            ▼
             ┌──────────── 可信层：证据强度分 + 三档处置（低分不进钱） ────────────┐
             └──────────────────────────────┬───────────────────────────────────┘
                                            ▼
             ┌──────────────────── 表达层（MaaS 或模板） ──────────────────────┐
             │ 报告 / 问询单 / 审计包 / 校对台；模型只转写，接地校验失败即退回模板   │
             └─────────────────────────────────────────────────────────────────┘

运行底座（华为云）：CodeArts 码道（开发五环节）· RDS PostgreSQL（审计落库）
                  · DCS Redis（缓存/限流/任务）· OBS（原始单据/报告）· ECS+Nginx（HTTPS）
```

**四层职责单一，可以分段验证**：感知层错了看对齐率，对齐错了看未匹配清单，
判断错了看规则单测，表达错了看接地校验。**任何一层出错，都不会被另一层掩盖。**

---

## 5. 快速开始

### 5.1 零依赖离线跑（不需要云服务 / 网络）

```bash
git clone https://gitee.com/Cwt66/zhuangmingbai.git
cd zhuangmingbai
bash run_offline.sh                   # 合成三单 → out/audit_report.html
bash scripts/check_all.sh             # 99 测试 + 评测门禁 + 注入 + 指纹 + 隐私扫描
```

### 5.1.1 本地 OCR（macOS，可选）

```bash
bash scripts/build_vision_ocr.sh          # 编译 macOS Vision 小工具
python3 scripts/ocr_vision.py 报价单.jpg --out out/quote_vision.csv
# 识别草稿会标出「缺字段 / 量价不符 / 低置信」；/perceive 会先让你校对，再审计
```

### 5.2 在线 Demo

```bash
python3 -m pip install -r requirements.txt
python3 -m uvicorn main:app --host 0.0.0.0 --port 8000
# http://127.0.0.1:8000  → 上传 2-4 份 CSV/XLSX → 审计
# /demo 合成样例 · /real 真实单据 · /review 校对台 · /inquiry 问询单 · /history 历史
# /demo.xlsx 审计工作簿（6 sheet，可直接给业主/监理）
```

### 5.3 本地验证「RDS + Redis 真的被用到」

```bash
cp .env.example .env      # 可不填 Key
docker compose up --build
curl -s localhost:8000/health | python3 -m json.tool
# 期望：db.kind = postgres，cache.kind = redis
```

### 5.4 云上（华为云）

```bash
# 1) RDS PostgreSQL / DCS Redis 控制台开通，拿到内网地址
export DATABASE_URL='postgresql://user:pass@rds-host:5432/zmb'
export REDIS_URL='redis://redis-host:6379/0'
# 2) MaaS（可选，但强烈建议，用于照片解析与解读）
export MAAS_API_KEY='...'
export MAAS_BASE_URL='https://api.modelarts-maas.com/v1'   # 以控制台为准
# 3) 启动
uvicorn main:app --host 0.0.0.0 --port 8000
```

部署细节见 [`deploy/DEPLOY.md`](deploy/DEPLOY.md) 与 [`docs/开发者空间部署指南.md`](docs/开发者空间部署指南.md)。

### 5.5 程序化接口

```bash
# 摘要 JSON（含审计编号与 AI 解读）
curl -s -X POST localhost:8000/api/audit \
  -F "files=@data/samples/company_a.csv" \
  -F "files=@data/samples/company_b.csv" | python3 -m json.tool

# 历史（证明 RDS 在存）
curl -s localhost:8000/api/audits | python3 -m json.tool
```

---

## 6. 安全、隐私与合规

- **发送前脱敏**：`src/privacy.py` 在调用 MaaS 之前替换手机号/身份证/邮箱/银行卡/明确标注的姓名与地址；
  金额与工序不动。脱敏发生在本地。
- **提示词注入防护**：单据文本是「不可信输入」，进入模型前用定界符隔离，并识别
  「忽略以上指令」类注入；表达层输出做**接地校验**，出现事实外数字即拒收。
- **数据最小化**：真实单据全部来自公开晒单 / 公开 PDF 或业主授权；仓库内 CSV 已扫描确认
  无手机号、身份证、姓名称谓（`python3 scripts/pii_scan.py --strict`）。
- **不训练**：报告页明确写「数据仅用于本次核对，不用于训练」。
- **定位**：计费核对与信息整理工具；**非定价鉴定、非法律意见、非施工验收**。
- **本地优先**：macOS Vision 在本机完成 OCR，图片不出机器；云端 MaaS 是可选项，不是前提。
- **失败不装懂**：感知层失败明确报错，不返回空表；模型失败退回模板并标注来源；
  数据库/Redis 失败降级但 `/health` 说实话。

---

## 7. 仓库结构

```
main.py                 FastAPI：/ 上传 · /demo · /real · /review · /inquiry · /perceive
                        /history · /report/{id} · /api/audit · /api/audits · /health
src/pipeline.py         审计引擎（本体对齐 / 13 规则 / 证据链 / 报告）
src/perception.py       感知层五 provider（file / vision / maas / ocr_table / mock）
src/vision_ocr.py       本地 OCR + 表格重建 + 确定性 critic（macOS Vision）
src/store.py            RDS PostgreSQL / SQLite 持久化（自动降级）
src/cache.py            Redis 缓存 / 限流 / 任务状态（自动降级）
src/llm.py              MaaS 表达层（接地校验 + 模板兜底）
src/xlsx.py             零依赖 XLSX 导出（6 sheet，金额为数值）
scripts/vision_ocr.m    macOS Vision OCR 小工具（本地编译）
src/privacy.py          脱敏与提示词注入防护
src/inquiry.py          问询单
src/obligations.py      条款 → 义务 → 验收证据
src/pricing_system.py   计价体系识别
data/ontology_v0.json   工序本体 119 概念 / 388 别名
data/templates_v0.json  老房 39 项 / 新房 28 项
data/rules_v0.json      13 条审计规则
data/coverage_v0.json   证据词 + 父类吸收表
data/real/              5 份真实单据（公开晒单/公开 PDF 的人工转录，无个人信息）
eval/                   黄金标注评测 / 消融 / 注入 / 对照实验 / 冻结指纹 / 指标库
tests/                  99 个单元测试
scripts/                感知层 CLI / 本地 OCR / 指纹快照 / 离线包 / 隐私扫描 / 一键自检
docs/                   架构 / 评测 / 部署 / 安全 / 判断正确性 / 迁移实验
deploy/                 Dockerfile · docker-compose · systemd · Nginx · DEPLOY
```

---

## 8. 样例跑出来的结果（合成三单，仅链路验证）

| 公司 | 报价总价 | 漏项补项 | 重复剔除 | 真实成本 | 风险敞口 |
|---|---:|---:|---:|---:|---|
| A 规范型 | 86,498 | 400 | 0 | 86,898 | — |
| B 低价引流型 | 59,902 | 22,164（17 项） | 0 | 82,066 | 6,100–7,300 |
| C 高价项全型 | 100,146 | 0 | 3,168（2 项） | 96,978 | — |

**演示结论**：B 表面最低，补漏后 82,066，加上水电按实结算敞口 6,100–7,300 → 88,166–89,366，反超 A 的 86,898。
**最低价不等于最低成本。**

> 合成样例（`data/samples/`）按真实晒单结构合成，**不是真实业主单据**，不得作为"真实数据"对外宣称。

---

## 9. 换个行业还能用吗（迁移实验）

能，而且是**跑出来的**：

```bash
python3 scripts/audit_with.py \
  --ontology data/repair/ontology_repair.json \
  --templates data/repair/templates_repair.json --template major_service \
  --coverage data/repair/coverage_repair.json \
  --csv data/repair/sample_major_service.csv --out out/repair
```

| 换什么 | 装修 | 汽修 |
|---|---|---|
| 本体 | 119 概念 / 388 别名 | 40 概念 / 97 别名 |
| 模板 | 老房 39 / 新房 28 | 大保养 18 / 小保养 8 |
| 规则 | 13 条 | **同一份，原样复用** |
| 引擎代码 | `src/pipeline.py` | **同一个文件，零改动** |

实测：对齐 19/19、漏项 4、重复 1、算术 1、对账差 30 元、开口条款 2 类。
迁移顺带抓出两处「只在自己领域成立」的假设（R6 直接取 `onto["u"]`、报告函数假设
`_cross_unit` 存在），已修并锁进 `tests/test_migration.py`。

---

## 10. 与同类方案的区别

| 维度 | RenoPit 类（LLM 直读） | 人工第三方监理 | 装明白 |
|---|---|---|---|
| 单据结构化 | VLM | 人工 | VLM + 人工校对闭环 |
| 跨单对齐 | 无 | 人工 | 本体库归一到工序轴 |
| 判断依据 | 大模型陈述 | 经验 | 规则 + 计算，可复现 |
| 结论可溯源 | 无 | 口头 | 行号 + 基准 + 公式 + 证据强度 |
| 同一单两次结果 | 可能不同 | 人不同 | **相同（冻结指纹）** |
| 效果验证 | 无 | 无 | 黄金标注 + 消融 + 注入 + 对照协议 |
| 价格依赖 | 无 | 有 | **P0 不依赖**（不做虚高） |

**标准答案**：它做"问 AI"，我们做"可计算审计"；同一批单据的对照实验数字在材料里。

---

## 11. 诚实的边界

- n=2 份完整黄金标注，只作**存在性证明**；第 4、5 份为单一标注者 A1。
- 照片单据为人工转录（另有 OCR 产物对照），**真实照片 → 审计**的端到端还需 MaaS/OCR 额度。
- 工序本体没有官方标准背书，权威性靠覆盖度 + 标注 + 与定额/监理实践对标，不靠自称。
- 置信度是**证据强度分，不是概率**；不做 ECE 声称。
- Redis / RDS 代码已接入，但云上实例是否开通以账号控制台为准；未开通时自动降级并如实显示。

---

## 12. 数据来源与许可

- 真实单据来自公开晒单 / 公开 PDF 或业主授权；原始照片不入库，只保留脱敏转录 CSV。
- 合成样例明确标注，不写成真实数据。
- 代码 MIT（见 `LICENSE`）；`data/` 仅用于评测与研究，转载原始单据需遵守来源平台条款。
- 相关：[RenoPit / 装闭](https://github.com/fthux/RenoPit)（LLM 直读对照路线）· 华为云 CodeArts / MaaS / RDS / Redis / OBS。

---

## 13. 参与贡献

```bash
bash scripts/check_all.sh   # 提交前必须全绿
python3 scripts/pii_scan.py --strict   # 不能带入真实个人信息
```

- 只接受能落到「可判定的命题」上的功能建议（漏/重/算术/对账/口径）。
- 改判定结果必须同步更新冻结指纹，并在 PR 里说明为什么。
- 不接收"把注入实验 100% 写成召回率"这类表述。

---

## 14. English summary

**Zhuangmingbai** is a computable audit engine for renovation quotations. It normalizes
hundreds of colloquial trade terms into a single work-item ontology, then applies 13
deterministic rules (missing items, duplicate billing, arithmetic, document reconciliation,
vague terms) to produce a traceable report: every finding carries source row, baseline,
formula, and an evidence score. LLMs are used only for perception and phrasing — never for
judgement — so the same quote always produces the same report. Built on Huawei Cloud
(CodeArts, ModelArts Studio, RDS PostgreSQL, DCS Redis, OBS, ECS) with graceful local
fallbacks. MIT licensed.
