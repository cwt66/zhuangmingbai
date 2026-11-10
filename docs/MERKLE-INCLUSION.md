# Merkle 包含证明：从「下载整本账」到「一个根哈希」

> 口径：2026-11-10 ｜ 账本 6 条 ｜ 方法依据 RFC 6962 / Sigstore Rekor / Trillian
> 复现：`python3 eval/ledger_chain.py --json` · `python3 -m unittest tests.test_merkle`

---

## 一、上一版的公开锚点有什么问题

2026-11-09 把链头 + **全部 6 条摘要**发布到 `verify/anchor.json`，
L2 得把整张表拉下来，才能确认「我手里这一条在不在账本里」。

6 条时无所谓；账本长到几千条，这就是「为了查一条记录，把整本账交给评委」。
更麻烦的是 **L2 与账本规模线性相关**——评委不下载全表就没法验。

当天还留了两个真实漏洞（都是 2026-11-10 实测复现后修的）：

| 编号 | 洞 | 复现 |
|---|---|---|
| D2 | `publish_pages.py --check` 只比 `seq/head/entries`，**摘要表被改也判 PASS** | 改第 1 条 `result_digest`，旧逻辑返回 `True` |
| D3 | `verify_bundle.py` 的 L2-c 只比第 seq 条摘要，没检查 `head` 是否等于末条、`merkle_root` 是否由摘要算出 | 一份自相矛盾的公开锚点会被当成有效证据 |

---

## 二、方法依据（GitHub 上可核对）

- **RFC 6962 Certificate Transparency §2.1**：叶子 `SHA-256(0x00 || d)`，
  内部节点 `SHA-256(0x01 || left || right)`；树形由叶子数唯一决定。
  0x00/0x01 是域分隔，防止把内部节点冒充叶子构造两棵同根不同内容的树。
- **Sigstore Rekor / Google Trillian**：公开 append-only 日志只发布一个
  `Signed Tree Head`（树大小 + 根），每个持有者带一条
  `ceil(log2(n))` 长的 audit path 自证包含，**不需要其他条目**。

`src/merkle.py` 是这两条规则的纯标准库实现（零依赖、不联网）。

**叶子定义**：账本第 i 条 `hash` 的 32 个原始字节。条目全文由审计包里的
`ledger_entry.json` 携带，其 hash 必须等于叶子。Merkle 根承诺的是
「条目 hash 的有序序列」；链上的 `prev` 承诺顺序，两者互补。

---

## 三、四层校验现在长这样

| 层 | 查什么 | 需要什么 |
|---|---|---|
| L1-a/b/c | 输入+标准+代码逐字节、结论复算、哈希链自洽 | 审计包本身 |
| **L1-d（新）** | 包内 `inclusion_proof.json` 对 `anchor_snapshot.merkle_root` 成立；包内账本算出的根必须等于该 `merkle_root` | 审计包 + 32 字节根 |
| L2-a/b | 包内条目 hash 去仓库账本同 seq / 锚点对照 | clone 仓库 |
| **L2-c/d（新）** | 公开锚点自洽 + **包含证明对公开摘要的前缀树根成立** | `curl` 一个 URL |

关键在于 L2-d：验证端**不需要下载整张摘要表的内容**，
只需要根 + 一条对数长度的路径。账本后来增长时，用当前摘要的前 `n` 条
重算前缀根即可；前缀没被改写，旧证明仍成立——这同时是一次 append-only 检查。

---

## 四、两个真实的算法坑（单测抓到的）

1. **审计路径的方向**：RFC 6962 的 `PATH(m, D[n]) = PATH(内层) : MTH(兄弟)`
   是**自底向上**的。我第一版按「自顶向下」收集，n=3 起算出的根就错了。
   修正：生成时反转路径；验证用 RFC 6962-bis §2.1.3.2 的 `fn/sn` 位运算算法
   （不能简单按 split 自顶向下消费，非 2 的幂树会把左右顺序算反）。
2. **`tree_size` 不由证明单独绑定**：实测把 6 叶证明的 `tree_size` 改成 5，
   用同一条路径仍能算出同一个根。RFC 里树大小是由**签名的 STH**承诺的；
   我们没签名，所以必须由外部锚点的 `seq/entries` 显式约束。
   `verify_proof(..., expect_tree_size=...)` 就是这条约束，
   包内 `verify.py` 用 `len(ledger.jsonl)`，L2 用公开摘要的前缀。
   「不给外部条数就能蒙混过去」这一招**留在攻击套件里，没删**。

`tests/test_merkle.py` 对 n=1..33 的每一个索引都生成并验证证明，
覆盖所有非 2 的幂树形。

---

## 五、攻击套件（第 20 步自检每次跑）

| 组 | 用例 | 结果 |
|---|---|---|
| 链级 | 8 招 | 拦 7 招，第 8 招「链+锚点一起重写」离线已知盲点 |
| 包级 | 4 招（含改包内 Merkle 证明、整包重算包括证明） | 4 招全拦；整包重写离线 PASS、L2 FAIL |
| **Merkle / 锚点自洽** | **12 招** | **12 招全拦** |

Merkle 组包括：改路径、改叶子、改索引、改 tree_size、改根、
外部根不符、head 与末条不符、merkle_root 与摘要不符、摘要 hash 被换、
摘要 seq 断号、前缀被重写、以及 `--check` 全字段比对拦住业务字段篡改。

**留在表里的两条边界**：
- 「摘要业务字段被改」结构自洽查不出（Merkle 只承诺 hash 序列），
  靠 `publish_pages.py --check` 的全字段逐条比对补上；
- 「改 tree_size 但不给外部条数」证明本身会过，
  靠锚点的 `seq` 补上。

---

## 六、我们**没有**做到什么

| Rekor / RFC 6962 有 | 我们 | 为什么 |
|---|---|---|
| 签名的 STH（ECDSA/RSA） | 没有签名，根由仓库文件 + git + 公开 URL 背书 | 密钥管理是另一个工程；对标清单里明确不补 |
| 紧凑一致性证明（SUBPROOF，对数长度） | `verify_append_only`：完整账本下的前缀根校验 | 不是对数长度证明，不冒充 |
| 独立 monitor / gossip 防「同一根给不同人不同视图」 | 没有 | 场景规模不需要，但边界必须写清 |
| 非对称签名回执 | 没有 | 与 Provenant 的差距，写在对标文档里 |

**包含证明只证明「这个叶子在这棵树里」，不证明树本身诚实。**
根仍需外部锚点；根能被改，但改它需要一次可见的 push，
与悄悄改审计包不同。

---

## 七、怎么用

```bash
# 本地：整条链 + 攻击套件（含 12 招 Merkle）
python3 eval/ledger_chain.py --json

# 打一个带包含证明的审计包
python3 scripts/make_bundle.py --csv data/real/feitai_budget.csv \
    --label 翡翠台 --out /tmp/x.zmb.zip

# 离线 L1（含 L1-d）
unzip /tmp/x.zmb.zip -d /tmp/v && python3 /tmp/v/verify.py

# L2：仓库账本 + 公开锚点 + 包含证明
python3 scripts/verify_bundle.py --zip /tmp/x.zmb.zip --run-l1 \
    --anchor-url https://cwt66.github.io/zhuangmingbai/verify/anchor.json

# 公开锚点是否与仓库一致（含摘要表全字段）
python3 scripts/publish_pages.py --check
```
