---
name: zmb-audit
description: 审计装修报价单，判出漏项、重复计价、算术与对账问题，每条结论带原表行号与计算公式。当用户上传或提到装修报价单、报价表、装修预算、要核对报价、检查漏项/重复收费/隐形增项，或者问「这份报价有没有问题」「最低价是不是划算」时使用。不适用于装修合同、施工图、验收单。
tags: [huawei-cloud, codearts, audit, quotation, deterministic]
---

# 装明白 · 报价单审计

## 这个技能解决什么（Overview）

装修报价单的问题不是「看不懂」，是**没有坐标系**：
同一道工序有五种叫法（墙固 / 界面剂 / 基层处理）、三种计价单位（㎡ / m / 项），
业主没法把几家公司放到同一张表上比。

本技能把报价单拆到一根统一的坐标轴上（119 个标准工序概念 / 388 个别名），
用 13 条可审阅的规则判出：**谁漏了项、谁重复收了钱、谁的口径对不上账**。
判断不经过大模型——同一份单据跑两次，判定指纹逐字节一致。

**不做**：不判断「贵不贵」（那需要地区价格库，没有可信数据源就不做），
不做施工验收，不做法律意见。

## 前置条件（Prerequisites）

- Python 3.10+，**判定引擎零第三方依赖**（只用标准库）；无需云账号、无需 API Key
- 输入是带「项目名称 / 单位 / 数量 / 单价 / 合价」列的表格
- 工作目录为仓库根目录；规则库 `data/rules_v0.json`、本体 `data/ontology_v0.json`、
  模板 `data/templates_v0.json` 必须存在
- 可选：`bash scripts/check_all.sh` 先跑一遍 19 步自检，确认引擎没被改坏

## 什么时候用

- 用户给了一份或几份装修报价单（CSV / Excel / PDF 转录），问「有没有坑」「帮我看看」
- 用户想比较 2–4 家报价，问「哪家实在」
- 用户问「这个价格合理吗」——先做漏项与重复审计，再解释为什么我们不判贵不贵

## 不适用

- 装修合同、施工图、材料清单、验收单 → 本技能会先做**上传守门**，不是报价单直接返回 400 并说明理由
- 结算单 → 用 `scripts/diff_quotes.py` 做两版对比，而不是单份审计

## 怎么做（Workflow / Core Commands）

### 第一步：确认输入

文件必须是带「项目名称 / 单位 / 数量 / 单价 / 合价」列的表格。
照片需要先转 CSV（感知层 `file` provider 支持 CSV / XLSX / PDF 文本层）。

### 第二步：跑审计

```bash
python3 scripts/audit_one.py <报价单.csv> --label "<公司或单据名>"
```

可选参数：

| 参数 | 何时用 |
|---|---|
| `--template old_house` | 老房翻新（39 项标准工序） |
| `--template new_house` | 新房毛坯（28 项） |
| `--template auto` | 默认，按行数猜 |
| `--scope base` | 对方明确说「基础报价 / 不含主材」时 |
| `--json` | 需要程序化处理时 |

**退出码**：`0` 正常；`2` 不是报价单（守门拦下）；`1` 其他错误。

### 第三步：读结果，先讲三件事

1. **交付判定**：`pass` / `needs_fix` / `block`。`block` 时不要给业主结论，先说为什么不能出报告。
2. **可核减金额**：漏项 + 重复，这两个数只算 `auto` 档（低置信结论不进钱）。
3. **每条结论的行号**：报告里每条都指向原表第几行，业主可以自己翻。

## 参数与确认（Parameter Confirmation）

- **改判定口径的参数**（`--template` / `--scope`）必须由用户明确给出或确认；
  不要替用户猜「这是老房还是新房」，猜错会让整份漏项结论失效。
- **不确认就不改**：本 Skill 全部是只读操作，不写回用户原始文件、不上传任何数据、
  不调用任何云 API。唯一输出是 stdout 与（可选）`--json`。
- **高风险操作不存在**：没有删除、没有覆盖、没有外部副作用，因此不需要二次确认；
  这一点写清楚，比套一个假的「preview + confirm」流程诚实。

## 输出怎么读

```
| 可核减漏项 | 618.40 元 |      ← 只算 auto 档
| 可核减重复 | 182.00 元 |
| 交付判定   | pass      |
| 判定指纹   | 52bf7b635204fd96 |  ← 同一输入永远同一个值
```

漏项表里 `tier` 有两档：

- `auto`（证据强度 ≥0.90）：进核减金额，可以直接拿去问对方
- `review` / `manual`：只进「待确认清单」，**不进钱**——宁可少报，不可错报

## 转述给用户时的三条纪律

1. **数字带口径**：说「auto 档 618.40 元」，不说「能省 618」。
2. **不替对方定罪**：说「这 12 项在标准模板里有、这份单据里没有」，
   不说「他们故意漏项」——漏项可能是范围不同，也可能是模板不适配。
3. **主动说边界**：样本只有 5 份真实单据、2 份黄金标注，n=2 只能证明「机制可行」，
   不能外推「准确率多少」。

## 想自己复核

```bash
python3 scripts/gate_report.py        # 7 项交付门 + 5 个故障注入（证明它真会拦）
python3 eval/redteam.py               # 红队 8 招，看它能被哪一招骗过
python3 eval/detection_floor.py       # 最小可捕获误差（单行 ±1.01 元）
python3 scripts/verify_skill_spec.py --self-test   # 本 Skill 的规范符合性 + 双向断言
python3 scripts/claim_ledger.py --self-test        # 材料里每句数字的复算台账
python3 scripts/verify_package_numbers.py          # 提交包数字一致性（仓库外，本地 ② 档）
python3 scripts/verify_clean_clone.py --self-test  # 干净 clone 保障的静态不变量
bash scripts/check_all.sh             # 19 步全量自检
```

## 偏离声明（对华为云官方 Agent Skills 规范）

本 Skill 对齐 `huaweicloud/huaweicloud-skills` 的包结构与验收项，
但**它不是 hcloud CLI Skill**，有三处必须主动声明的偏离，不假装做了：

| 官方要求 | 本 Skill 的实际情况 | 处理 |
|---|---|---|
| `hcloud <Service> <Operation> --help` 前置、CLI 安装指南 | 不调用任何云 API，无 CLI 命令 | `references/cli-installation-guide.md` 写明「无 CLI，只需 Python 3.10+」 |
| `references/iam-policies.md` 最小权限策略 | 无云账号、不读云资源 | 文档写明最小权限 = 本地只读 + 单文件输出，而非编造一条 IAM policy |
| `skill-quality-cli` 统一质量上报 | 未接入遥测（离线可运行、无外发请求） | 明确写为偏离；不为了过检查而发一个假上报 |

其余验收项（frontmatter 字段、name 与目录名一致、章节齐全、
无硬编码凭据、无跨 Skill 调用、≤500 行 / ≤30 文件 / 扩展名白名单）
由 `scripts/verify_skill_spec.py` 逐条自动核对，随 CI 每次推送重跑。
**规范符合性不是一句话，是可以被别人复算的一张表。**

## Reference Documents

| 文档 | 内容 |
|---|---|
| `references/acceptance-criteria.md` | 逐条验收项与核对命令（AC-01 ~ AC-17 + 偏离项） |
| `references/verification-method.md` | 第三方怎么独立复验这个 Skill |
| `references/iam-policies.md` | 最小权限边界：本地只读、无云 API、无凭据 |
| `references/dataflow-diagram.md` | 输入 → 守门 → 引擎 → 结论 → 报告的数据流 |
| `references/cli-installation-guide.md` | 运行环境与「无 CLI」的偏离说明 |

## 数据与隐私

- 真实单据全部来自公开晒单 / 公开 PDF 或业主授权；仓库内 CSV 已扫描确认无个人信息
- 本地 OCR（macOS Vision）在本机完成，图片不出机器
- 调用云端模型前会脱敏（手机号 / 身份证 / 邮箱 / 银行卡 / 姓名地址）
- 报告页明确写「数据仅用于本次核对，不用于训练」

## 许可

MIT。规则库（`data/rules_v0.json`）、本体（`data/ontology_v0.json`）、
模板（`data/templates_v0.json`）全部是可读 JSON，欢迎逐条审阅。
