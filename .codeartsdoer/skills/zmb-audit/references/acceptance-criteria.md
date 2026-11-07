# 验收标准（Acceptance Criteria）

> 对齐 `huaweicloud/huaweicloud-skills` 官方 Skill 的验收口径。
> 每一项都给一条**当场能跑**的核对命令；跑不动的项不算通过。
> 本文件是「诊断」，不是「修复日志」：偏离项单独列，不混进通过项。

## 结构合规

| AC | 判据 | 核对命令 / 方法 |
|---|---|---|
| AC-01 | `SKILL.md` 有 YAML frontmatter，含 `name` / `description` / `tags`，**无 `version`** | `sed -n '1,6p' .codeartsdoer/skills/zmb-audit/SKILL.md` |
| AC-02 | `name` 与目录名一致（`zmb-audit`） | `grep '^name:' .codeartsdoer/skills/zmb-audit/SKILL.md` |
| AC-03 | `description` 含功能摘要 + 触发词，≥40 字 | 同上，人工读一遍触发条件 |
| AC-04 | `tags` ≤ 5 个 | `python3 scripts/verify_skill_spec.py` → `AC-01c` |
| AC-05 | 必需章节齐全：Overview / Prerequisites / Workflow / Core Commands / Parameter Confirmation / Reference Documents | `grep '^## ' .codeartsdoer/skills/zmb-audit/SKILL.md` |
| AC-06 | `references/iam-policies.md` 存在且写明最小权限 | `ls .codeartsdoer/skills/zmb-audit/references/` |
| AC-07 | `references/cli-installation-guide.md` 存在；质量上报要么实现、要么声明偏离 | 同上 + `grep '偏离声明' SKILL.md` |
| AC-08 | 全目录无硬编码凭据 | `grep -rniE 'access.?key|secret.?key' .codeartsdoer/skills/zmb-audit/` 人工确认无值 |
| AC-09 | 无跨 Skill 直接调用 | `python3 scripts/verify_skill_spec.py` → `AC-09` |
| AC-16 | `SKILL.md` ≤ 500 行；文件数 ≤ 30；总体积 ≤ 40 MB | `wc -l SKILL.md`；`find ... -type f \| wc -l` |
| AC-17 | 扩展名在白名单（`.md/.py/.json/.sh/.yaml/.yml/.txt`） | `python3 scripts/verify_skill_spec.py` → `AC-17` |

## 功能验收

| 场景 | 期望 | 复现 |
|---|---|---|
| 正常报价单 | 退出码 0，输出交付判定 / 可核减 / 判定指纹 | `python3 scripts/audit_one.py data/samples/company_a.csv --label A` |
| 上传装修合同 | 退出码 2，明确报「不是报价单」并给证据 | `python3 scripts/audit_one.py <合同文本.csv>` |
| 同一单跑两次 | 判定指纹逐字节相同 | 连续跑两次，比对输出里的 16 位哈希 |
| 报告该拒绝时 | 交付门判 `block` | `python3 scripts/gate_report.py`（5 个故障注入全拦） |
| 低置信结论 | 只进待确认清单，不进核减金额 | `tests/test_report_gate.py` |

## 偏离项（DEV）

| DEV | 判据 | 为什么允许 |
|---|---|---|
| DEV-01 | 非 CLI Skill 的偏离被明确声明（含 `skill-quality-cli` 的说明） | 本 Skill 不调用 `hcloud`、不碰云 API、无遥测外发；硬凑一份假 CLI 指南比声明偏离更不诚实 |
| DEV-02 | 验收项在 `acceptance-criteria.md` 里逐条可核对 | 规范符合性本身也要能被第三方复算 |

## 一键核对与双向断言

```bash
python3 scripts/verify_skill_spec.py              # 人读的表
python3 scripts/verify_skill_spec.py --self-test  # 干净必须 PASS，6 种故障必须 FAIL
python3 scripts/verify_skill_spec.py --json       # CI 用
```

双向断言的六种故障：加 `version`、改 `name`、删 IAM 文档、塞硬编码凭据、
抹掉偏离声明、把 `SKILL.md` 撑过 500 行。
**一个永远通过的校验器和一个永远失败的校验器一样没用**——
所以「干净必须 PASS」和「故障必须 FAIL」两条都要在。
