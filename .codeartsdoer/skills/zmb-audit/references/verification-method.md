# 复验方法（Verification Method）

> 给第三方（评委 / 队友 / CI）用。不要求信任本文档的任何一句话，只要求跑命令。

## 最小复验（30 秒，零依赖）

```bash
git clone <repo> && cd zhuangmingbai
python3 scripts/verify_skill_spec.py --self-test
python3 scripts/audit_one.py data/samples/company_a.csv --label A
```

第一条验「这个 Skill 包本身合规且校验器没坏」，第二条验「它真的能审一份单」。

## 完整复验（约 1 分钟）

```bash
bash scripts/check_all.sh                  # 17 步：测试 / 评测 / 交付门 / 红队 / 台账 / 规范
python3 scripts/claim_ledger.py --json     # 材料里每句数字是否与代码一致
python3 scripts/verify_skill_spec.py --json
python3 scripts/verify_clean_clone.py --self-test   # 干净 clone 保障还在不在
python3 scripts/verify_clean_clone.py --json        # 真跑一次干净 clone（约 1 分钟）
python3 scripts/verify_package_numbers.py           # 提交包数字一致性（需本地有国奖提交包）
```

> 两条边界（都不进 ① 档台账，理由见 `docs/CLAIM-LEDGER.md` §5.1）：
> `verify_clean_clone` 塞进 `check_all.sh` 会无限递归，改由 CI 第二个 job 保证；
> `verify_package_numbers` 检查的提交包在仓库外，CI 的 checkout 里没有它，
> 所以在 CI 上只会报「待复算」，**不算通过也不算失败**，报告留在提交包里供现场重跑。

## 三条判据，缺一条不算通过

1. **基线必须放行**：干净台账、干净 Skill、合法报价单都得返回 0。
   一个永远报错的校验器，和一个永远放行的校验器一样没用。
2. **坏输入必须被拒**：`scripts/claim_ledger.py --self-test` 的 6 种故障注入、
   `scripts/gate_report.py` 的 5 个故障注入，任何一个没拦住都要非 0。
3. **同一输入必须同一输出**：判定指纹逐字节一致；不一致说明有非确定性混进了判定路径。

## 证据分档（读者据此判断把握有多大）

| 档 | 含义 | 本 Skill 里的例子 |
|---|---|---|
| ① 跑出过结果 | 命令当场跑出的值 | 判定指纹、对齐率、13 条规则、17 步自检 |
| ② 读过原文 | 值来自某个产物文件，有产生它的命令 | `out/ablation.md` 的精确率、`out/rule_liveness.md` 的被杀数 |
| ③ 只按元数据判 | 写不出命令，只能声明 | 「n=2 只作存在性证明」「鲲鹏未实测」「MaaS Key 未开通」 |

**③ 档不计入已验证**，统一列在 `out/claim_ledger.md` 的欠账清单里。
低于 ③ 的判断不进档案。

## 已知未修 / 已撤回（不删、不美化）

见 `data/claim_history_v0.json`。红队 A3「㎡ 写成 m」得手且**不打算硬修**
（`m` 是合法单位，强判会误伤延米计价），配有一条断言它拦不住的测试。
撤回档案的指纹锁在 `data/claims_v0.json`，删一条自检即红。
