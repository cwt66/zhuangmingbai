# 运行环境与 CLI 安装（偏离说明）

> 官方 `hcloud` CLI 类 Skill 要求写 KooCLI 安装与 `hcloud ... --help` 前置流程。
> **本 Skill 没有任何 CLI 命令，也不调用 hcloud。** 这份文档的作用是
> 把这个偏离写清楚，而不是伪造一份安装指南。

## 需要装什么

| 依赖 | 版本 | 是否必须 |
|---|---|---|
| Python | ≥ 3.10 | 必须 |
| `hcloud` / KooCLI | — | **不需要** |
| 第三方 Python 包 | — | **不需要**（判定引擎只用标准库） |
| 华为云账号 / AK / SK | — | **不需要** |
| 网络 | — | **不需要**（离线可跑完全部判定） |

```bash
python3 --version
python3 scripts/audit_one.py data/samples/company_a.csv --label A
```

主程序的可选能力（服务端 PDF、Excel 解析、Web Demo）才需要 `requirements.txt`；
那些属于**主程序**，不属于这个 Skill，且失败时会明确报错，不返回空结果。

## 为什么没有 CLI

Skill 的全部动作就是一条本地只读命令：

```bash
python3 scripts/audit_one.py <报价单.csv> --label "<名字>" [--template ...] [--scope ...] [--json]
```

没有远程资源要创建、删除、查询，也没有高危操作需要 `preview + confirm`。
官方模板里的「破坏性操作二次确认」「`--help` 参数交叉核对」在这里
没有适用对象；`SKILL.md` 的「参数与确认」一节写明了**哪些参数需要用户确认**
（改判定口径的 `--template` / `--scope`），以及为什么不需要二次确认。

## 质量上报的偏离

官方 Skill 通过 `skill-quality-cli` 做统一遥测上报。本 Skill **未接入**：
离线可运行、零外发请求是它的一条设计约束，为了过检查发一条假上报
会直接破坏这条约束。偏离已写进 `SKILL.md`，并由 `DEV-01` 验收项
检查「偏离被声明」而不是「偏离不存在」。

## 核对

```bash
python3 scripts/verify_skill_spec.py --json       # AC-07 / DEV-01
python3 scripts/audit_one.py --help               # 唯一一条命令的参数说明
```
