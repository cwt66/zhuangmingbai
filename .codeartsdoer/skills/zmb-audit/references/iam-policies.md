# 最小权限边界（IAM Policies）

> 官方 Skill 模板要求写最小权限的 IAM 策略。
> **本 Skill 不调用任何华为云 API，也不使用 hcloud CLI**，因此不存在可授权、
> 可收紧的云侧策略。硬编一条 IAM policy 只会制造「好像接了云」的错觉。
> 这里如实写成本地权限边界，由 `scripts/verify_skill_spec.py` 的 AC-06/AC-07 检查。

## 权限矩阵

| 资源 | 需要的权限 | 实际做法 |
|---|---|---|
| 用户上传的报价单 | 只读 | `open(..., 'r')`，不写回、不移动、不删除 |
| `data/*.json`（本体 / 规则 / 模板 / 参照价） | 只读 | 加载进内存，不修改 |
| `out/`、`eval/` 产物 | 可选写入 | 仅当显式调用评测脚本时生成；`audit_one.py` 默认不落盘 |
| 网络 | **不需要** | 判定引擎零外发请求；云端模型属于主程序可选的感知/表达层，不在本 Skill 路径上 |
| 云账号 / AK / SK | **不需要** | 无凭据、无 profile、无环境变量依赖 |
| 环境变量 | 不读取 | 不因为没有某个 Key 就改变判定结果 |

## 凭据纪律

- 不在 Skill 目录、文档、日志、会话输出里写入任何凭据值。
- 不读取 `~/.hcloud` / `~/.obsutilconfig` 之类的配置。
- 主程序若使用云端 provider，凭据只从环境变量取，且失败时**如实降级**
  （`/health` 显示 `llm: disabled`，报告标「规则模板生成」），不伪装成已连接。

## 为什么这反而是更严格的边界

一个不碰云、不联网、只读本地文件的 Skill，攻击面比「最小权限的云策略」更小：
没有 IAM 策略可以配错，也没有凭据可以泄漏。
`scripts/pii_scan.py --strict` 与 `scripts/verify_skill_spec.py` 的 AC-08
分别检查「输入里有没有个人信息」和「目录里有没有硬编码凭据」。

## 核对

```bash
grep -rniE 'access.?key|secret.?key' .codeartsdoer/skills/zmb-audit/   # 无凭据值
python3 scripts/verify_skill_spec.py --json                              # AC-06 / AC-07 / AC-08
```
