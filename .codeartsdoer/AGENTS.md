## Engineering Context

```json
{
  "Language Context": [
    "Python"
  ]
}
```

## 这个仓库是什么

「装明白 · 可计算审计」——把装修报价单拆到统一的工序坐标系上，
用可审阅的规则判出漏项、重复计价、算术与对账问题，每条结论带原表行号与公式。

## 动手前必须知道的五条约定

1. **判定不经过大模型。** 模型只做两件事：看（照片→结构化行项）和说（结论→人话）。
   判断全部由 `data/rules_v0.json` 的 13 条规则完成。任何把判断交给 PROMPT 的改动都算倒退。
2. **判定引擎零第三方依赖。** `src/pipeline.py` 只用标准库。加依赖要说明为什么不能用标准库实现。
3. **宁可少报，不可错报。** 置信度低于 0.90 的结论只进「待确认清单」，不进核减金额。
   这条由 `src/report_gate.py` 的 D5 每次校验，破坏它会直接 block。
4. **改了判定就要重算指纹。** `python3 scripts/snapshot_predictions.py --check` 必须仍与基线一致；
   不一致要先判断是「修正」还是「破坏」，不允许直接把基线覆盖掉。
5. **新能力要有测试锁。** 尤其是「不判什么」——正向改坏有人发现，负向改坏是静默多出假阳性。

## 常用命令

```bash
bash scripts/check_all.sh                       # 13 步全量自检（提交前必跑）
python3 scripts/audit_one.py <报价单.csv>        # 审一份单据
python3 scripts/gate_report.py                  # 交付门 7 检查 + 5 故障注入
python3 eval/redteam.py                         # 红队 8 招
python3 eval/detection_floor.py                 # 最小可捕获误差
python3 scripts/na_report.py                    # 负向知识库（13 条「不判」）
python3 -m uvicorn main:app --port 8000         # 本地 Web
```

## 已有 Skill

`.codeartsdoer/skills/zmb-audit/SKILL.md` —— 报价单审计技能，随仓库分发。
改动审计入口（`scripts/audit_one.py`）时，要同步检查 SKILL.md 里的命令是否仍然正确。
`tests/test_skill.py` 会做这件事。

## 目录约定

| 路径 | 放什么 | 不放什么 |
|---|---|---|
| `src/` | 引擎与各能力模块 | 领域数据 |
| `data/` | 本体 / 模板 / 规则 / 覆盖率 / 易混词 / 负向库 | 代码 |
| `eval/` | 评测脚本与产物 | 线上逻辑 |
| `scripts/` | CLI 入口与运维脚本 | 可被 import 的业务逻辑 |
| `tests/` | 单测（196 个，全部可离线跑） | 需要网络的用例 |
