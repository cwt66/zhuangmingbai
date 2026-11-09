# 装明白 · 可计算审计（Computable Audit）

> 第十一届华为 ICT 大赛 · 中国创新赛 · 赛题 1「基于华为云产品打造 AI 创新应用」
>
> **装修报价的问题不是"看不懂"，是没有坐标系。我们给装修报价建了一套可计算的坐标系。**

[![Gitee](https://img.shields.io/badge/Gitee-Cwt66%2Fzhuangmingbai-c71d23)](https://gitee.com/Cwt66/zhuangmingbai)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](./requirements.txt)
[![Tests](https://img.shields.io/badge/tests-330-brightgreen.svg)](./tests)
[![Rules](https://img.shields.io/badge/rules-13-002FA7.svg)](./data/rules_v0.json)

签合同之前，上传 2–4 家装修公司的报价单（照片 / PDF / Excel / CSV），输出一份**可核对、可溯源、可复现**的审计报告：
谁漏了项、谁重复收了钱、谁口径混乱、补漏后真实成本是多少。

**边界**：不判断贵不贵（不做虚高/最低价断言），只判断漏没漏、重没重；低置信度结论不进核减金额，只进「待确认清单」。

![作品介绍](docs/assets/deck-cover.png)

**评委 5 分钟复验页**（单文件 HTML，双击即用、可离线）：
<https://cwt66.github.io/zhuangmingbai/verify/>（**当前入口**，随代码更新）。
OBS 上另有一份 2026-10-29 的**历史快照**——AK 于 11-07 被判失效，不能覆盖更新，
所以不再把它当主入口：[HTML](https://zmb-quotes-20261029.obs.cn-north-4.myhuaweicloud.com/verify/index.html) ·
[PDF](https://zmb-quotes-20261029.obs.cn-north-4.myhuaweicloud.com/verify/%E8%A3%85%E6%98%8E%E7%99%BD_%E8%AF%84%E5%A7%94%E5%A4%8D%E9%AA%8C%E9%A1%B5_10%E9%A1%B5.pdf)。
每页都给出该页数字的复现命令。

![架构](docs/assets/architecture.svg)
![四层架构](docs/assets/deck-architecture.png)

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

## 2. 当前状态（2026-11-07）

| 模块 | 状态 |
|---|---|
| 审计引擎（分层 / 漏项 / 重复 / 算术 / 对账 / 口径 / 义务 + 证据链） | ✅ `src/pipeline.py`（**零第三方包**：纯标准库 + 同仓 `src/` 模块；行数由台账 C10 探针查导入，不再写死） |
| 工序本体 | ✅ **119 概念 / 388 别名** / 8 大类 |
| 工序模板 | ✅ 老房翻新 39 项 / 新房毛坯 28 项；支持「报价范围声明 → 模板裁剪」 |
| 规则 DSL | ✅ **13 条**，全部可逐条审阅（`data/rules_v0.json`） |
| 感知层 | ✅ 五 provider：`file`（CSV/XLSX/PDF 零依赖）/ **`vision`（macOS Vision 本地 OCR）** / `maas`（Qwen-VL）/ `ocr_table`（华为云通用表格识别）/ `mock` |
| **云原生：RDS PostgreSQL** | ✅ 代码层已接入（`src/store.py`；psycopg 可选，失败自动降级 SQLite）；`/history`、`/api/audits` 可查 |
| **云原生：DCS Redis** | ✅ 代码层已接入（`src/cache.py`：结果缓存 / 限流 / 任务状态；失败自动降级进程内） |
| **表达层：MaaS** | ✅ `src/llm.py`：只转写规则结果；**输出接地校验**，出现事实外数字即拒收并退回模板 |
| **本地 OCR + critic** | ✅ `src/vision_ocr.py`：图片不出本机；缺字段/量价不符/低置信 → 标「需核对」，不直接进结论 |
| **用户反馈闭环** | ✅ 报告页 `/feedback` → RDS；试点访谈有结构化记录 |
| **报告质量门** | ✅ 每份报告顶部：对齐 / 算术 / **分位对账** / 漏项 / 指纹；`/report` 历史同步 |
| **可审计 Excel** | ✅ `/demo.xlsx`、`/audit.xlsx`：总览/漏项/重复/风险/对账/说明 6 个 sheet，金额是数值 |
| **分位金额** | ✅ `src/money.py`：Decimal 求和/求差；R8 仍保留 1 元容差，质量门额外报「精确到分」差额 |
| **多 sheet Excel** | ✅ `.xlsx` 解析所有 worksheet，选非空行最多的一张，避免「主表在第二个 sheet」直接报空 |
| 真实单据评测 | ✅ **5 份 / 603 行工序明细 + 11 行汇总**（合计 **614 行**原始表格数据）；两份完整黄金标注；注入实验 242 例 |
| 单元测试 | ✅ **330 个**（云原生 14 / 本地 OCR 7 / PDF 坐标 / 回归线 / 对账分层 / 四项创新 / 跨领域迁移 / 盲标与 κ 9 / 对标能力 16 / 两版对比 10 / 回放页 5 / **报告交付门 16** / **负向知识库 10** / **红队与易混词 17** / **Skill 证据校验 16** / **规则活性 15** / **Web 交付门 4** / 优惠与负号 5 / 指纹一致性 3 / 主张台账 14 例 / Skills 规范 7 例 / 隐私扫描 4 例 / 干净 clone 保障 4 例 / 提交包数字 6 例 / 失效矩阵 6 例） |
| 可打印报告 | ✅ `/demo`、`/real`（打印级 HTML，判定指纹上封面） |
| **服务端 PDF** | ✅ `/demo.pdf`、`/real.pdf`、`scripts/make_report_pdf.py`（不依赖浏览器打印） |
| **上传守门** | ✅ `src/doc_gate.py`：**解析前**就判 报价单/合同/无关（只看表头与原文），非报价单直接 400 并给合同词/列结构证据 |
| **确定性增项预测** | ✅ `src/addon_forecast.py`：金额区间来自参照价库，**不编概率**，开口条款金额不重复计入 |
| **两版对比** | ✅ `src/version_diff.py` + `/diff`：按本体概念配对，算出「增项 / 减项 / 同项调整」，且**增−减+调整 = 账面差额**（对不上账就报警）；单位变了不比金额 |
| **审计回放页** | ✅ `scripts/build_replay.py` → 单文件 HTML：5 份真实单据 / 603 行 / 86 条结论，点一条结论高亮它指向的原表行；**预计算回放，不冒充实时引擎** |
| **报告交付门** | ✅ `src/report_gate.py`：7 项检查判 `可交付/需修正/禁止交付`。**报告会拒绝交付自己**——出现无依据金额、低置信混进核减、指纹不可复现、对账差异未披露，一律 block；自带 5 个故障注入证明它真会拦 |
| **检测下限** | ✅ `eval/detection_floor.py`：不依赖标注的确定性指标——单行最小可捕获 **±1.01 元**；同时暴露并盯住一个已知盲区（单行 <1 元、累计 <对账容差的小额偏差两条规则都抓不到，实测 8 组里 7 组沉默） |
| **负向知识库** | ✅ `data/not_applicable_v0.json`：**13 条明确「不判」的情形 + 理由 + 若判了会怎样**，10 条有测试锁、1 条是「已知拦不住」。与 13 条规则互为补集——判什么写在规则里，不判什么只活在记忆里，记忆会丢 |
| **红队自测** | ✅ `eval/redteam.py`：**主动扮演想骗过系统的人**，8 招真实花招打自己——近义名重复、空格+全角括号、㎡写成 m、有量无价、表尾伪装、备注藏项、优惠拆两行、数量 0 合价非 0。**拦住 7/8**；得手那招写进负向库 NA13，并有一条**断言它拦不住**的测试 |
| **易混词表** | ✅ `data/confusable_v0.json`：15 组「只差一字、语义相反」的词对（地砖↔地漏、找平↔找坡、强电↔弱电）。修的是红队 A1 撞出的真 bug：`地砖安装` 曾被编辑距离判成 `地漏安装` |
| **Skill 证据校验** | ✅ `scripts/verify_skill_evidence.py`：码道会话里的行数/合计/可核减/判定/指纹，**逐字段与引擎实跑比对**，差 1 分钱即判不一致；同时拦「召回率 100%」这类无口径断言。证据缺失只报「待采集」，不假装通过 |
| **规则活性测试** | ✅ `eval/rule_liveness.py`：把变异打在 `rules_v0.json` 上，测「13 条可审阅的规则」到底有几条在起作用。首次 47 个变异只杀 18 个；修活 `enabled`/`min_lines`/`strength(R5)`/`scope(R5)`/`tolerance_pct` 后**35/47、判定字段存活 0**，且五份冻结指纹逐字节未变。附双向断言与静态可达性检查；未修的写进 `docs/RULE-BLIND-SPOTS.md`（诊断，不是修复日志） |
| **Web 交付门** | ✅ `main.py` 全部判定路由的报告顶部显示 D1–D7 三档判定；`block` 直接写「此报告不应交付给业主」。之前 `report_gate` 在 Web 侧零引用——接线时才发现根因是 `import pipeline` 在 `from src import ...` 下会崩 |
| **渲染层转义** | ✅ `build_html` 对单据名、工序名、备注、依据文案统一 HTML 转义。XSS 回归测试复现过：上传名字带 `<script>` 的 CSV，报告页会执行它 |
| 解析校对台 | ✅ `/review`（低置信度标黄 → 修改 → 导出 CSV） |
| 问询单 | ✅ 把结论变成可直接发给报价方的逐条问询（`src/inquiry.py`） |
| 输入兼容 | ✅ UTF-8 / **GBK** / UTF-16、逗号 / **TAB**、表头别名 |
| 审计历史 | ✅ `/history` + `/api/audits`（证明 RDS 真的在存业务数据） |
| 一键自检 | ✅ `bash scripts/check_all.sh`（20 步：测试 + 评测 + 注入 + 指纹 + 隐私 + 交付门 + 检测下限 + 负向库 + 红队 + 证据校验 + 规则活性 + **主张台账** + **华为云失效矩阵**） |
| 在线 Demo | ✅ `python3 -m uvicorn main:app --port 8000` |
| 离线演示包 | ✅ 干净目录可独立复现（含跨领域 demo） |
| RenoPit 对照实验 | 🚧 协议已预注册，跑批进行中（我们已借鉴其文档分类/增项预测，但改为确定性实现） |
| 永久公网展示页 | ✅ **GitHub Pages** `https://cwt66.github.io/zhuangmingbai/` 为当前入口；OBS 为 10-29 快照（AK 失效，不能覆盖） |
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

### 2.2 主张台账：材料里的数字，自己会说话

前面所有门禁管的都是**引擎输出对不对**。还有一类东西从来没人管：
**我们对外说的话对不对**。材料里写着「330 个测试」「119 概念 / 388 别名」
「20 步自检」，代码一改就悄悄过期，评委不会去数，我们自己也不会。

所以加了第 16 步：**每条可量化主张都必须带一条产生它的命令**
（`data/claims_v0.json`，24 条）。判定不认台账里写的 `value`，
只认 `probe` 当场从源头重算出来的 `observed`；同时用 `{v}` 模板反查文档，
「改了文档没改台账」和「改了代码没改文档」两个方向都会红。

方法直接来自华为云社区《一个会自我审计的系统：可信度是怎样被公开建造的》
可操作清单第 1、2、3、6 条。证据分三档，**别让「我看过」和「我跑过」长得一样**：

| 档 | 含义 | 条数 | 例子 |
|---|---|---:|---|
| ① 跑出过结果 | 校验器当场执行命令，从源头重算 | 17 | 测试数、本体重命名、614 行、17 步、红队 7/8、±1.01 元 |
| ② 读过原文 | 值来自命令生成的产物，本次未独立重算 | 4 | 消融精确率、规则活性被杀数 |
| ③ 只按元数据判 | 写不出命令的边界声明，**不计已验证** | 3 | n=2 只作存在性证明、鲲鹏未实测、Key 未开通 |

```bash
python3 scripts/claim_ledger.py --self-test   # 干净必须 PASS / 6 种故障必须 FAIL
python3 scripts/claim_ledger.py               # 人读的表：out/claim_ledger.md
```

`data/claim_history_v0.json` 是**撤回档案**：12 条被撤回结论、误报、假发现、
已知未修，加 3 条**预测 MISS 永久保留**；指纹锁在台账里，删一条自检即红。
第一次规则活性只杀 18/47、红队有一招得手、冻结基线曾「本地绿 / clone 红」——
这些不好看的记录一条都没删。

**台账上线当天就被自己抓了一次**：CI 跑在 x86_64，而 C14 说的是「开发机 arm64」
却让 `platform.machine()` 当场复算，于是 CI（x86_64）第 16 步报「宣称 arm64，实测 x86_64」
（[run 42372612688](https://github.com/cwt66/zhuangmingbai/actions/runs/42372612688)，`conclusion=failure`）。
处理不是把探针改成永远返回 `arm64`，而是**把 C14 从 ① 降为 ②**
（改读采集时写入的 `data/dev_environment_v0.json`），并在撤回档案记 H11 留案。
档位因此从 ① 14 / ② 2 降为 **① 13 / ② 3**（本轮 C20 又补回 ① 14 / ② 3）——数字变难看了，但它是对的。
细节见 `docs/CLAIM-LEDGER.md`。

**为什么这算一个创新点**：别人证明「我能干活」，我们证明「我说过的每句话都能被查」。
判据都由我们自己定的时代结束了——现在**连宣传语都由 CI 管**。

### 2.3 华为云 Agent Skills 规范符合性（可复算，不是一句话）

项目级 Skill `.codeartsdoer/skills/zmb-audit/` 按
[`huaweicloud/huaweicloud-skills`](https://github.com/huaweicloud/huaweicloud-skills)（MIT）
的包结构与验收项对齐：frontmatter（`name`/`description`/`tags`、**无 `version`**）、
`references/` 五件套、最小权限、无硬编码凭据、≤500 行 / ≤30 文件 / 扩展名白名单。
`scripts/verify_skill_spec.py` 把 **18 项**逐条自动核对，随 CI 每次推送重跑。

本 Skill **不调用 hcloud CLI、不碰任何云 API、不接遥测**——这三处偏离写进
`SKILL.md` 的「偏离声明」，而不是伪造一份 CLI 安装指南或 IAM policy 混过检查。
校验器查的是「偏离被写清楚」，不是「偏离不存在」。

```bash
python3 scripts/verify_skill_spec.py              # 18/18 人读的表
python3 scripts/verify_skill_spec.py --self-test  # 干净 PASS / 6 种故障 FAIL
```

### 2.4 台账管不到的两处，也补上了（并且如实说它们不是 ① 档）

- **评委拿的是仓库外的 `国奖提交包/`**：55 份 md、几十处数字、commit hash 与
  sha256 清单，台账的 `occurrences` 只反查仓库内文件，CI 里也没有这个目录。
  `scripts/verify_package_numbers.py` 查旧值残留 / 当前值覆盖 / HEAD 一致性 /
  sha256 逐条复算，报告写进提交包 `09_证据附录/`。**本地 ② 档工具，不进 CI 假装通过。**
- **「干净 clone 也是绿的」** 此前靠人工记得跑；H05 就是这么翻车的
  （本地 5/5 冻结基线、clone 只有 3/5）。`scripts/verify_clean_clone.py` 从 HEAD
  建干净 clone 跑全量自检，CI 加**第二个独立 job** `clean-clone`。
  **不能塞进 `check_all.sh`**——clone 自己再跑 check_all 会无限递归。

```bash
python3 scripts/verify_package_numbers.py --self-test  # 1 正向 + 5 故障
python3 scripts/verify_clean_clone.py    --self-test  # 5 项静态不变量
python3 scripts/verify_clean_clone.py    --json       # 真跑一次干净 clone（约 1 分钟）
```

两者都带双向断言，共 10 个单测锁住不退化。
**不进 ① 档不是免责，是把它放在真能被复算的地方，而不是换一个假绿勾。**
细节见 `docs/CLAIM-LEDGER.md` §5.1。

### 2.5 华为云失效矩阵：每一行都被真的「挂过一次」（创新点 ⑥）

`docs/华为云依赖与失效矩阵.md` 此前列了 8 个依赖挂掉会怎样，写得挺好，
**但一条都没被执行过**——「每个依赖都有降级路径」只是文档里的一句话。

`eval/cloud_failure_matrix.py` 把它做成 **9 个实验**：
RDS / DCS / MaaS 文本 / MaaS 视觉 / 华为云 OCR / OBS / ECS / CodeArts 各注入一次，
外加一次「8 个依赖同时不可用」的全挂验证。三条纪律逐条落断言：

| 纪律 | 怎么验 |
|---|---|
| 降级要承认，`/health` 不撒谎 | 注入不可达地址 → `kind=sqlite` / `memory` 且 `degraded` 有原因；再注入「把 sqlite 撒成 postgres」必须被拒 |
| 感知层不许返回空表 | 无凭据调 `perceive` 必须抛 `PerceptionError`；源码静态检查无 `return []`、且 `not subscribed` 与「签名失败」分开 |
| 表达层失败要说来源 | 无 Key 时 HTML 必须写「规则模板生成」，不伪装成 AI 生成 |

**最硬的一条**：把 8 个可选依赖同时置为不可用，再跑一次完整审计，
判定指纹 `52bf7b635204fd96` 与核减金额逐字节不变。
**云全挂了结论还是它，才配说「判定不依赖云」**——此前只是架构文档里的形容。

方法不是自己想的，参照 GitHub 上的 `chaos_toolkit`（受控故障注入验证 fallback）、
`qa-resilience`（deterministic fault injection 先于 staged chaos）、
`resilience-pattern-engineer`（**每条声明的防御都要有一个 chaos test 证明它触发**）。
接进自检第 17 步，台账 C20 锁住它；`--self-test` 另有 5 例双向断言。

```bash
python3 eval/cloud_failure_matrix.py            # 9 个实验的人读报告
python3 eval/cloud_failure_matrix.py --self-test --json
```

**第一次上 CI 就抓到一个真 bug**：CI 装了 `psycopg`，PG 初始化失败时
`store.py` 把 `kind` 置为 `down` 而非回退 SQLite——文档承诺与实现不符。
修的是实现，不是放宽断言；细节见 `docs/华为云依赖与失效矩阵.md` §5.1。

> 边界：注入的是**本地不可达地址与空凭据**，不是真把华为云实例关掉；
> 证明的是「代码在依赖不可用时的行为」，不是云服务的 SLA。

---

### 2.6 跨环境判定确定性：换台机器也不许换结论（创新点 ⑦）

⑥ 证明「判定不依赖云」，⑦ 再往下问一句：**换环境呢？**
`docs/华为云IaC实测与硬件边界.md` 里「鲲鹏 ARM ECS 未实测」是我们主动交代的欠账
（台账 C18，③ 档声明）。不能把「ARM64 理论上能跑」包装成「已适配鲲鹏」，
但可以把这句推论收敛成一条**谁都能复算的证据**。

`eval/determinism_matrix.py` 用同一份 `data/real/feitai_budget.csv` 跑
**12 个环境变体**：三个 `PYTHONHASHSEED`（0 / 1 / random）、UTC 与上海时区、
C 与中文 locale、关掉 UTF-8 mode、`-O` 优化级别、注入假 `psycopg`/`redis`、
以及 `python -S`（完全不加载 site-packages）。
每个变体比对 6 个结论字段（digest / verdict / 核减 / 重复 / 行数 / 总额），
共 144 次逐字段比对——**全部与基准 `52bf7b635204fd96` 一致**。

```bash
python3 eval/determinism_matrix.py                 # 12 个变体的人读报告
python3 eval/determinism_matrix.py --self-test --json
```

方法参照 GitHub 上两个可核对的项目：
`FlorianStuettgen/SOC_Replay` 的 *Reproducible Builds*（干净源树两次独立执行、
固定哈希种子、比对完整校验和、**主动写明可复现构建证明不了什么**），
以及 MEP-51 的 *Cross-target byte-equal differential gate*
（跨 x86_64 / aarch64 / macOS-arm64 比对输出 SHA-256，并逐条列出
历史上真正导致过分歧的原因：哈希种子、set 迭代顺序、locale 排序、时区）。
上面那些变体就是照着这份「分歧原因清单」逐项注入的。

**跨指令集这半边不在本机证明。** `data/determinism_arm64_v0.json` 是这台
arm64 开发机采集的判定指纹存档；GitHub Actions 第三个 job `determinism`
在 **x86_64** runner 上、用 Python 3.10 / 3.11 / 3.12 / 3.13 四个版本的
**空 venv（刻意不装 `requirements.txt`）** 当场重算并比对。
本机跑绿只算「同架构复算」，CI 跑绿才让「跨指令集 + 跨 Python 版本确定性」成立——
报告里会如实写 `跨指令集比对：是 / 否(同架构复算)`，不给自己放水。

接进自检第 18 步，台账 **C21** 锁住结果，`--self-test` 有 **8 例双向断言**：
删掉存档、抹掉「鲲鹏实例未实测」边界声明、拿 x86_64 结果冒充 arm64 存档、
某个种子下指纹漂移、少跑一个维度——都必须 FAIL。

> **三条不许越过的线**：
> ① C18「鲲鹏实例未实测、昇腾未涉及」**不因这条升级**，仍是 ③ 档声明；
> ② 比对的是**判定结论**，不是构建产物字节，不是 reproducible build；
> ③ 存档来自 macOS arm64 开发机，不是华为云 aarch64 实例，环境变量扰动
> 也不等于真换了一台国产服务器。要补那条，仍需在真鲲鹏 ECS 上跑
> `uname -m` + `bash scripts/check_all.sh` 并存证（见硬件边界文档 §4）。

---

### 2.7 展示面一致性：评委第一眼看到的那一页，也进 CI

前四轮把「代码里的数字」「提交包里的数字」「别人 clone 下来的结果」
「云依赖挂了会怎样」「跨环境是否确定」全部接进了自检。
2026-11-09 重读工作流时发现**最后一环没人管**：

线上 `https://cwt66.github.io/zhuangmingbai/verify/` 与仓库外
`国奖提交包/02_作品介绍PPT/装明白_评委复验页.html` 字节完全一致，
内容却停在 **11-05**：`243 测试 / 15 步 / 写死 commit c56b706`。
而当时的真实状态是 `298 测试 / 18 步 / 2aa018f`。
README、台账、提交包、CI 全绿，**只有评委手里那一份悄悄过期**。

`scripts/verify_showcase.py` 把当前版展示页（`deliverables/装明白_评委复验页.html`）
与 `data/claims_v0.json` 绑定，接进自检第 19 步、台账 **C22**：

| 设计 | 为什么 |
|---|---|
| 当前值全部从台账读，不硬编码 | 测试数 / 步数 / 台账条数 / 证据档位改一处即可，判据不会和当前口径打架 |
| **禁止写死 commit hash** | hash 每次提交都变，写进展示页必然过期；口径统一指向 `git log -1` |
| 华为关联与诚实边界是必检项 | 删掉第 10 页、`鲲鹏未实测`、`③ 档不计已验证` 都必须 FAIL，防止为了好看删边界 |
| 页码一致性 | `section` 数必须等于页脚最大编号，加页忘改页码直接红 |
| 与提交包副本逐字节比对 | 本机存在才查；CI 上没有就如实跳过，不伪装成检查过 |

展示页同时更新为 **11 页**：新增第 10 页「华为关联 · 可复算证据」
（四层硬件边界 + 四条评委可当场跑的命令），并去掉所有写死的 commit。
11 页 PDF 由 `render_verify_pdf.py` 用 Playwright 逐页渲染，
**11 张截图互不相同**才落盘；旧 10 页 PDF 按纪律保留为历史快照、不回填。

```bash
python3 scripts/verify_showcase.py                    # 人读报告
python3 scripts/verify_showcase.py --self-test --json  # 7 例双向断言
```

### 2.8 Linux aarch64 原生实测：从「同指令集」到「同 OS 家族」

⑦ 证明的是「macOS arm64 与 x86_64 Linux 判定一致」。但鲲鹏跑的是
**Linux + glibc**，openEuler 也是——macOS 的 arm64 不是它的运行时环境。

GitHub 从 2026-10 起对公开仓库免费提供**原生 arm64 Linux runner**
（`ubuntu-24.04-arm`，4C/16G，不是 QEMU）。所以加了一个 job
`aarch64-linux`，在真的 aarch64 Linux 上跑完判定链路：

```
arch=aarch64  system=Linux  libc=glibc_2.39  python=3.12.16
digest=52bf7b635204fd96  verdict=pass  missing_cost=618.4
```

**与 macOS arm64 存档逐字节一致**。这条证据写在 job annotation 里
（`check-runs/<id>/annotations` 公开只读，不需要登录），
存档在 `data/aarch64_linux_v0.json`，台账 **C23**（② 档读档）。

```bash
python3 scripts/verify_aarch64.py --check --json    # 联网核对 run（本地）
python3 scripts/verify_aarch64.py --archive --json  # 离线只校验存档（CI/台账）
```

> **边界必须一起读**：这是 GitHub 的 aarch64 Linux runner，
> **不是鲲鹏物理机，不是 openEuler 发行版，也不涉及昇腾**。
> 台账 C18 仍是 ③ 档声明，没有因为这条升级。
> 它做到的是把「同为 ARM64 理论上能跑」变成「Linux aarch64 + glibc 上真的跑过，
> 且结论与 macOS 一致」——距离鲲鹏更近一步，但不是鲲鹏。

> **边界**：判据只读仓库内文件、不联网（CI 上不依赖网络）。
> 线上 Pages 由**本仓库的 `gh-pages` 分支**发布，逐字节比对交给
> `python3 scripts/publish_pages.py --check`（本地 ② 档工具）。
> 2026-11-09 已同步：线上与本地 sha256 一致（`290bbd38…`）。
>
> **这里有过一次误判，留案**：上一版写「部署源不在本仓库、无法覆盖」，
> 依据是 `git branch -a` 只显示 main、Pages API 返回 404。实际 `gh-pages`
> 就在同一仓库，只是本地从未 fetch。**「没查清」不该写成「管不了」**——
> 这条误判进了撤回档案 H12。

### 2.9 哈希链账本：结论被事后改写，要留下痕迹

③ 一直说「判定指纹可离线复算」，但 2026-11-09 我把攻击真跑了一遍：

```
从 templates_v0.json 删掉 3 个必做项 → 漏项 12 掉到 10
用包里的引擎重算新 digest → 写回 result.json
python3 verify.py → [PASS] 复算结论与包内记录一致：0c8e2a741819f593
```

**上一版 `verify.py` 只校验输入哈希**，`result.json` 里记的
`standards_sha256` / `code_sha256` 从来没被用过。这是「文档写了、代码没做」。

修法不是补一个 `if`，是把「记录真相的东西」从被审的容器里拿出来：

- `data/ledger.jsonl`：6 份真实单据串成一条哈希链，
  `hash = sha256(canonical(seq, ts, kind, payload) + "|" + prev)`；
- `data/ledger_anchor.json`：链头落在链外，随 git 提交、被台账 C24 反查；
- 审计包里现在带 `ledger.jsonl` + `ledger_entry.json` + `anchor_snapshot.json`，
  `verify.py` 升成三层：**文件完整性 → 结论一致 → 链内自洽**；
- `scripts/verify_bundle.py` 是 L2：拿包里的 entry hash 去仓库账本、
  锚点、git 历史三处对照。

```bash
python3 scripts/ledger.py seed && python3 scripts/ledger.py anchor
python3 eval/ledger_chain.py --json   # 链级 8 招 + 包级 3 招
python3 scripts/verify_bundle.py --zip /tmp/x.zmb.zip --run-l1
```

**链级 8 招拦住 7 招，包级 3 招全拦住**；第 8 招（把链和锚点一起重写）
离线拦不住，只有 L2 能发现。**这一招留在报告里，不删**——
删掉它，这份材料就变成「我们什么都防住了」，那才是真的不可信。

> **边界**：账本现在是本地 JSONL + 仓库锚点。RDS 的 append-only 表结构
> 与 OBS 对象锁的代码/DDL 都已写好，但实例与 AK 都不可用，
> 所以状态是 `not_run`——材料里不写「账本已落库到 RDS」。
> 详见 `docs/HASH-CHAIN-LEDGER.md` 第六节。

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

### 3.4 标注凭什么可信（当前只有一个人标，这是已知短板）

黄金标注目前是**单一标注者 A1** 做的两份，n=2。它只能证明「存在性」，不能证明「标注本身可靠」。
要补的是**第二个人独立标一遍、算一致性**——工具已做好，不需要装任何环境：

```bash
python3 eval/make_annotator.py     # 生成两个自包含 HTML（离线、不联网、不含 A1 答案）
# 双击打开 → 逐项点「有 / 没有 / 本单不适用」→ 导出 CSV
python3 eval/cohen_kappa.py --dir eval/annotator/回传
# 输出：观察一致率 + Cohen's κ + 分歧清单；入库门槛 0.80 / 0.60
```

- 盲标页只由模板/本体/单据生成，**不读 `golden_labels`**（有测试锁死这一点）；
- 未判完不允许导出；同一份单据出现多个回传文件直接报错（防止拿错版本对比）；
- **在第二标注完成前，仓库与材料只写「单一标注者、结果可复算」，不写「标注可靠」。**

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
bash scripts/check_all.sh             # 测试 + 评测 + 注入 + 指纹 + 隐私 + 交付门 + 检测下限 + 负向库 + 红队 + 证据校验 + 规则活性 + 主张台账 + 华为云失效矩阵（20 步）
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
# /diff 两版对比（原版 vs 变更后，回答「多在哪」）
# /perceive 图片识别 · /review 校对台
```

### 5.2.1 两版对比：回答「你上次报 15 万，这次怎么 17 万」

```bash
python3 scripts/diff_quotes.py 原版.csv 变更后.csv --md out/diff.md
# 或浏览器打开 http://127.0.0.1:8000/diff （内置一份「真实单据 vs 模拟变更」演示）
```

配对靠本体概念（不是字面）：`拆墙` 与 `墙体拆除` 算同一项；`m` 换 `㎡` 这种单位变化
不进金额差，单列待核对。输出必须满足 `增项 − 减项 + 同项调整 = 账面差额`，
对不上就报警——这条对账是单测锁住的。

> 口径：只比同一项目、同一计价口径的两版；**不做跨公司比价**（那是「贵不贵」，我们不做）。
> 演示用的第二份是在真实单据上构造的 5 处模拟变更，不是真实结算单。

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
main.py                 FastAPI：/ · /demo · /real · /review · /inquiry · /diff · /perceive
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
src/version_diff.py     两版对比（本体配对 + 差额对账，单位变化不比金额）
src/doc_gate.py         上传守门（解析前判 报价单/合同/无关）
src/addon_forecast.py   增项预测（区间来自参照价库，不给概率）
src/report_gate.py      报告交付门（7 项检查 → 可交付/需修正/禁止交付）
src/obligations.py      条款 → 义务 → 验收证据
src/pricing_system.py   计价体系识别
data/ontology_v0.json   工序本体 119 概念 / 388 别名
data/templates_v0.json  老房 39 项 / 新房 28 项
data/rules_v0.json      13 条审计规则
data/coverage_v0.json   证据词 + 父类吸收表
data/not_applicable_v0.json  负向知识库：13 条「不判」+ 理由 + 若判了的后果
docs/RULE-BLIND-SPOTS.md 规则盲点清单：诊断，不是修复日志
data/confusable_v0.json 易混词对：15 组只差一字但语义相反的词，fuzzy 匹配必须跳过
data/claims_v0.json     主张台账：24 条公开主张 + 证据档位 + 文档出现位置
data/claim_history_v0.json 撤回档案：10 条撤回/误报/已知未修 + 3 条预测 MISS
data/real/              5 份真实单据（公开晒单/公开 PDF 的人工转录，无个人信息）
eval/                   黄金标注评测 / 消融 / 注入 / 对照实验 / 冻结指纹 / 指标库
tests/                  单元测试（含主张台账 14 例、Skills 规范 7 例、隐私扫描 4 例）
scripts/                感知层 CLI / 本地 OCR / 指纹快照 / 离线包 / 隐私扫描 / 一键自检
                        diff_quotes.py（两版对比）· build_replay.py（审计回放页）
                        gate_report.py（交付门：7 检查 + 5 故障注入）· na_report.py（负向库）
                        redteam.py（在 eval/：红队自测 8 招）· verify_skill_evidence.py（证据复算）
                        rule_liveness.py（在 eval/：规则变异测试）
                        claim_ledger.py（主张复算 + 双向断言）· verify_skill_spec.py（Skills 规范）
docs/                   架构 / 评测 / 部署 / 安全 / 判断正确性 / 迁移实验
                        CLAIM-LEDGER.md（方法论）· 华为云IaC实测与硬件边界.md
.codeartsdoer/skills/zmb-audit/  项目级 Skill（对齐华为云官方规范，references/ 五件套）
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
- **③ 档声明不算「已验证」**：黄金标注 n=2 只作存在性证明、鲲鹏实例未实测、
  MaaS Key / OCR 服务未开通——这三条写不出复算命令，只作声明，单列在
  `out/claim_ledger.md` 的欠账清单里。

---

## 12. 数据来源与许可

- 真实单据来自公开晒单 / 公开 PDF 或业主授权；原始照片不入库，只保留脱敏转录 CSV。
- 合成样例明确标注，不写成真实数据。
- 代码 MIT（见 `LICENSE`）；`data/` 仅用于评测与研究，转载原始单据需遵守来源平台条款。
- 相关：[RenoPit / 装闭](https://github.com/fthux/RenoPit)（LLM 直读对照路线）·
  [Jessmatth/invoice-auditor](https://github.com/Jessmatth/invoice-auditor)（模型提取 + 代码审算术）·
  [huaweicloud/huaweicloud-skills](https://github.com/huaweicloud/huaweicloud-skills)（官方 Agent Skills 规范，MIT）·
  [simin-yuan/self-auditing-agent](https://github.com/simin-yuan/self-auditing-agent)（gatecheck 变异测试，MIT）。
- 方法论：华为云社区《[CI 全绿，但你的质量门禁可能已经悄悄少了一条规则](https://bbs.huaweicloud.com/blogs/491618)》
  与《[一个会自我审计的系统：可信度是怎样被公开建造的](https://bbs.huaweicloud.cn/blogs/494747)》。

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
