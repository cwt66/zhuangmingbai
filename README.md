# 装明白 · 可计算审计（Computable Audit）

> 华为ICT大赛创新赛（第十一届）· 赛题1「基于华为云产品打造AI创新应用」
> 面向装修报价的可信 AI 审计：把模糊工序名映射到统一坐标轴，判断交给可解释规则，每条结论带校准过的置信度。

---

## 这是什么

签合同之前，上传 2–4 家装修公司的报价单（照片/PDF/Excel），输出一份**可核对、可溯源、带统计保证**的审计报告：谁漏了项、谁重复收了钱、谁口径混乱、补漏后真实成本是多少、拿着什么话去谈。

**边界**：不判断贵不贵（不做虚高/最低价断言），只判断漏没漏、重没重；低置信度结论不进入核减金额，只进"待确认清单"。

---

## 当前状态（2026-10-23）

| 模块 | 状态 |
|---|---|
| 审计引擎 v0（漏项/重复/口径 + 证据链 + 报告） | ✅ 已跑通（`src/pipeline.py`） |
| 工序本体 v0 | ✅ **101 概念 / 221 别名**（7 大类，含同义词/单位/房型标记） |
| 工序模板 | ✅ 老房翻新 38 项 / 新房毛坯 25 项 |
| 规则 DSL v0 | ✅ **12 条**（漏项/已知重复对/同概念多行/模糊表述/跨单单位/单位异常/拆分报价/算术校验/数量单位缺失/管理费率/规格缺失/防水高度） |
| 校准指标库 | ✅ ECE / 可靠性表 / risk-coverage / bootstrap CI（`eval/metrics.py`） |
| 可打印报告 | ✅ `out/audit_report.html`（浏览器打印为 PDF） |
| 解析校对台 | ✅ `web/review.html`（低置信度标黄、可直接改、导出 CSV） |
| VLM 提示词 + Schema | ✅ `docs/vlm_prompt_v1.md` + `data/schema_parse.json` |
| 作品信息收集表草稿 | ✅ `docs/作品信息收集表_填写草稿.md`（含 500/1000 字两段） |
| 标注规范 v0 | ✅ `eval/annotation_spec.md` |
| 采集台账 | ✅ `docs/collection_tracker.md`（8 个候选来源 + 平台分工） |
| VLM 解析链路（MaaS） | ⏳ 待接（W2） |
| 校对闭环 H5 | ✅ `web/review.html` 已可用（真实 VLM 接入后直接复用） |
| 真实单据评测集 | ⏳ 采集进行中（目标 30 份校赛 / 80–100 份初赛） |

> **注意**：`data/samples/` 下三家报价单是**按真实晒单结构合成的测试样本**（80㎡ 老房翻新，含刻意设置的漏项/重复/口径问题），用于引擎自测与演示彩排，**不是真实业主单据**，不得作为"真实数据"对外宣称。

---

## 快速开始

```bash
cd zhuangmingbai
python3 src/pipeline.py            # 跑三家样例 → out/audit_report.md/.html
python3 eval/metrics.py            # 校准指标自检（ECE / CI / risk-coverage）
# 浏览器打开 web/review.html      # 解析校对台（低置信度标黄 → 改 → 导出 CSV）
```

无第三方依赖（纯标准库）。报告输出到 `out/audit_report.md` 与 `out/audit_result.json`。

---

## 目录结构

```
zhuangmingbai/
├── data/
│   ├── ontology_v0.json      # 工序本体：97 概念 × 同义词 × 单位 × 房型标记
│   ├── templates_v0.json     # 老房/新房双模板
│   ├── rules_v0.json         # 规则 DSL（7 条）
│   └── samples/              # 三家合成样例（company_a/b/c.csv）
├── src/
│   └── pipeline.py           # 对齐 + 审计 + 证据链 + 报告生成
├── web/
│   └── review.html           # 解析校对台（演示资产）
├── eval/
│   ├── metrics.py            # ECE / 可靠性表 / risk-coverage / bootstrap
│   ├── annotation_spec.md    # 坑点标注规范 v0（L/D/U/V/M 五类）
│   └── dataset/              # 真实单据与标注（待建，raw/labels/meta）
├── docs/
│   ├── collection_tracker.md        # 采集台账（平台优先级 + 8 个候选来源）
│   ├── 校赛倒排_1115.md             # 10/22–11/15 校赛倒排（分我做/你们做）
│   ├── vlm_prompt_v1.md             # VLM 解析提示词 + 评测口径
│   └── 作品信息收集表_填写草稿.md    # 初赛官方模板填写草稿
└── out/                      # 引擎输出（报告/JSON）
```

---

## 与 v2 方案五层的对应

| v2 层 | 当前实现 | 下一步 |
|---|---|---|
| ① 感知层（VLM 结构化 + 校对闭环） | 未实现，输入为 CSV | W2 接 MaaS Qwen-VL，输出统一 JSON Schema |
| ② 坐标系层（WUA 工序轴对齐） | `Ontology.align`（精确/别名/模糊三级） | 扩充到 200 概念；统计对齐准确率与同义判等 F1 |
| ③ 判断层（规则 DSL） | `rules_v0.json` + 求值器 | 补到 12 条；规则覆盖率与单规则误报率 |
| ④ 可信层（校准审计） | `eval/metrics.py`（指标就绪） | W4 接真实标注，做 out-of-fold 校准与三档处置 |
| ⑤ 表达层（报告/话术） | Markdown 报告 | W4 出 PDF；话术模板库 |

---

## 样例跑出来的结果（引擎自测）

| 公司 | 报价总价 | 漏项补项 | 重复剔除 | 真实成本 | 风险敞口 |
|---|---:|---:|---:|---:|---|
| A 规范型 | 86,498 | 400 | 0 | 86,898 | — |
| B 低价引流型 | 59,902 | 22,164（17 项） | 0 | 82,066 | 6,100–7,300 |
| C 高价项全型 | 100,146 | 0 | 3,168（2 项） | 96,978 | — |

**演示结论**：B 表面最低，补漏后 82,066，加上水电按实结算敞口 6,100–7,300 → 88,166–89,366，反超 A 的 86,898。最低价不等于最低成本。

---

## 路线图

- **W1（10/22–10/28）**：采集 30 份；本体扩到 150 概念；校赛材料骨架
- **W2（10/29–11/4）**：接 MaaS VLM（提示词与 Schema 已就绪）+ 校对台联调；本体扩到 200；部署第一版
- **W3（11/5–11/11）**：300–500 条标注；首轮指标（对齐准确率/漏项召回）
- **W4（11/12–11/18）**：校准（ECE/CI/risk-coverage）；公网 Demo
- **W5（11/19–11/25）**：80–100 份评测集；对照实验；初赛材料
- **W6（11/26–11/30）**：报名确认；材料提交；复现 SOP

---

## 合规与数据

- 只采集公开晒单/求助帖或亲友授权单据；原始单据不进开源仓库、不进演示视频
- 演示用重制样本（改写金额与项目名，去掉个人信息）
- 审计结论定位"计费核对与信息整理"，不构成对任何机构的指控；不提供医疗/法律/鉴定意见
- 许可证：MIT（开源前补 LICENSE）

---

## 在线 Demo（云上部署）

Web 层是 FastAPI 外壳，引擎本身仍为零依赖纯标准库。

```bash
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000     # 或 PORT=8000 python3 main.py
```

| 路由 | 作用 |
|---|---|
| `GET /` | 上传页（2–4 份 CSV，附三家样例下载） |
| `POST /audit` | 上传并审计，返回可打印 HTML 报告 |
| `GET /demo` | 用 `data/samples/` 三家样例一键演示 |
| `GET /api/demo` | 同上的 JSON 版本（联调用） |
| `GET /health` | 健康检查，供部署脚本探活 |
| `GET /samples/{name}` | 下载样例 CSV，便于人工试用 |

### 华为云 ECS 一键部署（auto-deploy Skill）

```bash
# 凭证走环境变量，不落盘
export HW_ACCESS_KEY=<AK>
export HW_SECRET_KEY=<SK>

python scripts/auto_deploy.py \
  --repo /path/to/zhuangmingbai \
  --app-name zhuangmingbai \
  --region cn-southwest-2 \
  --yes
```

- 该 Skill 只负责「单机 ECS + 代码上传 + 启动 + 健康检查」，RDS / Redis / OBS 不在其范围内，需用 KooCLI 或对应 Skill 单独开通。
- `requirements.txt` 里含 `fastapi`，脚本会自动识别为 Python/FastAPI 项目并以 `uvicorn main:app` 启动。
- 部署后先打 `GET /health` 验证，再打 `GET /demo` 确认引擎在云上跑通。

## 感知层（图片 → 结构化）

引擎本身只吃 CSV。要把手机拍的报价单直接变成审计报告，用感知层：

```bash
# ① PDF / Excel / CSV —— 不需要任何云服务（优先用这条）
python3 scripts/parse_doc.py 报价单.pdf  --out /tmp/x.csv
python3 scripts/parse_doc.py 报价单.xlsx --out /tmp/x.csv

# ② 华为云「表格识别」—— AK/SK + 控制台开通服务（有免费额度），用于照片
HW_ACCESS_KEY=... HW_SECRET_KEY=... HW_PROJECT_ID=... \
  python3 scripts/parse_doc.py 报价单.jpg --provider ocr_table --out /tmp/x.csv

# ③ ModelArts Studio 视觉模型 —— 需要 API Key，用于照片（能手写/合并单元格）
MAAS_API_KEY=... MAAS_MODEL=qwen2.5-vl-72b \
  python3 scripts/parse_doc.py 报价单.jpg --out /tmp/x.csv

# 自检（不读文件，验代码路径）
python3 scripts/parse_doc.py --selftest
```

**PDF 已在真实报价单上验证**：7 页诊所装饰单，168 行、19 个分区有序、算术 168 行可验。

**为什么优先 PDF/Excel**：装企正式报价多是这两种，根本不需要 OCR。
把 AI 用在能用规则解决的地方是设计错误。往返测试已证明：经感知层解析再导出，
审计结论与直接读原件**逐字一致**。

网页版：`GET /perceive` 上传照片 → 识别 → 直接出审计报告。

**三条设计约束**（都写进代码与单测）：

1. 感知层只负责「看见」，不做任何「漏项/重复」判断；
2. 识别不出表头**直接抛异常**，绝不返回空表——空表会被当成「这份单据没问题」；
3. 每行做算术自校验（`数量 × 单价 = 合价`），不符必须报出来，由人回原图核对。

`mock` 是单测用的固定样例，返回里带 `fixture: true`，网页上会用红字标明
「这不是对这张图的识别结果」，防止演示时误用。

接入细节与踩过的坑见 `../装明白_校赛材料/感知层接入说明.md`。

## 测试

```bash
python3 -m unittest discover -s tests -v
```

- `tests/test_perception.py`：表头映射（含「单价(元)」这类变体）、算术校验、
  无凭据时给可执行提示而不是 KeyError；
- `tests/test_regression.py`：三单基线（400 / 22,164 / 3,168）、两份真实单据的对齐与漏项计数、
  以及两条不变量——**每条结论必须带 basis/formula/checked_words**、
  **低于 0.90 的结论不得进入核减金额**。

回归线是硬约束：任何改动跑一遍，全绿才算完。
