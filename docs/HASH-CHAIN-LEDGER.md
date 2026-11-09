# 哈希链审计账本：让「事后改结论」留下痕迹

> 口径：2026-11-09 ｜ 账本 6 条（6 份真实单据）｜ 链级 8 招拦 7 招、包级 3 招全拦
> 复现：`python3 eval/ledger_chain.py` ｜ `python3 scripts/make_bundle.py --csv ...`

---

## 一、为什么做：一次真实的失守

上一版的审计包 `verify.py` 只校验**输入文件**的 sha256。
`result.json` 里其实记了 `standards_sha256` 和 `code_sha256`，
但**从来没被用过**——这是一处「文档写了、代码没做」的空承诺。

我把攻击真的跑了一遍：

```bash
# 1. 从模板里删掉 3 个必做项（业主因此少被提醒两处漏项）
templates["old_house"] -= {"protect_door", "water_test", "material_lift"}

# 2. 用包里的引擎重算，拿到新 digest
# 3. 把新 digest 写回 result.json

python3 verify.py
# → [PASS] 复算结论与包内记录一致：0c8e2a741819f593
```

漏项从 12 掉到 10，`verify.py` 照样盖 PASS。
README 那句「看到 PASS 就表示中途没有任何一处被改过」当时是不成立的。

根因不是「忘了加一个 if」，而是：**记录真相的账本和被审的东西，放在同一个可改容器里。**
只要攻击者能同时改「标准」和「结论记录」，任何自洽性校验都会被一起说服。

---

## 二、结构：一条改不动的链

`data/ledger.jsonl`，一行一条：

```json
{"seq":1,"ts":"2026-11-09T18:52:43+08:00","kind":"audit",
 "payload":{"input_sha256":"44c8…","standards_digest":"9f3a…",
            "code_digest":"b71c…","result_digest":"52bf7b635204fd96",
            "label":"feitai_budget","template_key":"old_house","scope":"full"},
 "prev":"0000000000000000000000000000000000000000000000000000000000000000",
 "hash":"7c5c8078a596…"}
```

```
hash = sha256( canonical(seq, ts, kind, payload) + "|" + prev )
```

于是：

| 攻击 | 后果 |
|---|---|
| 改任一条 payload | 该条 hash 变 → 与下一条 `prev` 断开 |
| 删中间一条 | `seq` 断号 + `prev` 断链 |
| 换顺序 | `prev` 断链 |
| 截尾 | 链内自洽 → **只有锚点能发现** |
| 整条重写 | 链内自洽 → **只有锚点能发现** |

`payload` 只存「能定位到这份记录」的最小充分集：输入哈希、标准哈希表的摘要、
代码哈希表的摘要、判定指纹、口径标签。**不存整份结论**——账本是日志，不是数据库。

**身份由内容决定，不由单据名决定**：同一份 CSV 叫「翡翠台」还是
`feitai_budget`，判定是同一个，链上也该是同一条。第一版没做这件事，
同一份单据在链上重复出现了两次（seq=1 和 seq=7），已修。

---

## 三、锚点：链外的真相

`data/ledger_anchor.json`：

```json
{"schema":"zmb-ledger-anchor/v1","seq":6,
 "head":"<第 6 条的 hash，每次 seed 都会变>…","anchored_at":"2026-11-09T…",
 "git_commit":"<锚定时的 HEAD>",
 "mirrors":{"rds_table":{"status":"not_run"},"obs_object_lock":{"status":"not_run"}}}
```

校验三件事：

1. 账本条数 ≥ `anchor.seq`——否则是**截尾**；
2. 第 `seq` 条的 hash == `anchor.head`——否则是**整条重写**；
3. 条数 > `anchor.seq`——**新增未锚定，尾部还不具备防篡改能力**（这条也算不通过）。

第 3 条会让「追加完忘记 anchor」直接红。这不是找麻烦：
没锚定的尾巴等于没有保护，与其假装绿，不如逼一次 `python3 scripts/ledger.py anchor`。

---

## 四、离线包的三层校验

`make_bundle.py` 生成的包里现在有 `result.json` / `ledger.jsonl` /
`ledger_entry.json` / `anchor_snapshot.json`，`verify.py` 分三层：

| 层 | 查什么 | 拦住什么 |
|---|---|---|
| **L1-a 文件完整性** | input + **每份标准** + **每个引擎文件** 逐字节比对 | 改标准、改代码、删标准 |
| **L1-b 结论一致** | 用包内标准与代码重算，digest 必须等于记录值 | 结论被换、口径被调 |
| **L1-c 链内自洽** | 重算 `ledger.jsonl`，链头必须等于锚点快照，本包必须是链上第 N 条 | 改一条、删一条、换序、截尾、整条重写链 |
| **L2（需仓库）** | entry hash 去 `data/ledger.jsonl` 同 seq、`anchor.json` head、git 历史三处对照 | **链 + 锚点一起重写** |

L2 是工具：`python3 scripts/verify_bundle.py --zip x.zmb.zip --run-l1`，
加 `--anchor-url https://cwt66.github.io/zhuangmingbai/verify/anchor.json`
可再对照**公开锚点**（链头 + 6 条摘要，由 `publish_pages.py` 发布到 gh-pages）。
公开锚点不是信任根——能改仓库的人也能改它；但它把「对照」从
「clone 仓库」降到「curl 一个 URL」，且改动需要一次可见的 push。

---

## 五、8 招 + 3 招：哪些拦住，哪些拦不住

链级（合成 fixture，可复现）：

| 攻击 | 期望 | 实际 |
|---|---|---|
| 干净账本 | PASS | PASS |
| 改一条 payload | 拦截 | ✅ 拦截 |
| 删中间一条 | 拦截 | ✅ 拦截 |
| 换两条顺序 | 拦截 | ✅ 拦截 |
| 截尾 | 拦截 | ✅ 拦截（锚点） |
| 整条重写成自洽新链 | 拦截 | ✅ 拦截（锚点） |
| 新增一条但不重新锚定 | 拦截 | ✅ 拦截（锚点落后） |
| 只改锚点 head | 拦截 | ✅ 拦截 |
| **链和锚点一起重写** | **得手** | ❌ **得手（离线）** |

包级（真实打一个包再攻）：

| 攻击 | 期望 | 实际 |
|---|---|---|
| 干净包 | PASS | PASS |
| 改标准 + 重算结论（上一版失守的那招） | 拦截 | ✅ 拦截（2 处独立检出） |
| 改标准 + 同步改 `result.json` 哈希表 | 拦截 | ✅ 拦截（账本 payload 对不上） |
| 改标准 + 链 + 锚点 + 哈希表整包重写 | L2 拦截 | ✅ 离线 PASS / **L2 FAIL** |

**第 8 招必须留在报告里。** 离线包里没有「链外的真相」，
任何自洽性校验都会被一起说服——这不是实现缺陷，是离线校验的边界。
把它删掉，这份报告就变成「我们什么都防住了」，那才是真的不可信。

> 这一条和红队那一步是同一个纪律：8 招拦住 7 招，第 8 招写进负向知识库；
> 这里 8 招拦住 7 招，第 8 招写进本文件的第五节，并给出 L2 的补法。

---

## 六、与华为云的关系：代码就绪、实例未开通

账本设计成两处落，`src/ledger.py` 里有可直接执行的 DDL：

```sql
CREATE TABLE IF NOT EXISTS audit_ledger (
    seq BIGSERIAL PRIMARY KEY, ts TEXT NOT NULL, kind TEXT NOT NULL,
    payload JSONB NOT NULL, prev_hash CHAR(64) NOT NULL,
    hash CHAR(64) NOT NULL UNIQUE, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- 只允许追加：撤销一条结论靠写一条 kind='revoke'，不 UPDATE / DELETE
REVOKE UPDATE, DELETE ON audit_ledger FROM zmb_app;
GRANT  INSERT, SELECT   ON audit_ledger TO   zmb_app;
```

- **RDS**：append-only 表 + 权限收口（撤销靠追加 `revoke`，不改不删）。
- **OBS**：锚点对象 + 对象锁（WORM），开了锁之后连账号所有者也不能覆盖。

**现状：两处都是 `status: not_run`。** RDS 实例与 OBS AK 都不可用
（OBS AK 于 2026-11-07 失效），材料里不写「账本已落库到 RDS」。
锚点当前落在仓库文件 + git 历史 + 两个远端。

这不是「华为关联」里最硬的一条，但它是**唯一一条能诚实说明边界**的：
我们写了 DDL、写了权限收口、写了降级，但没有把「代码就绪」说成「已上线」。

---

## 七、怎么用

```bash
# 把 6 份真实单据记进链（幂等，重跑不会重复追加）
python3 scripts/ledger.py seed
python3 scripts/ledger.py anchor          # 锚定，然后 commit 锚点文件

# 校验
python3 scripts/ledger.py verify --json   # 链 + 锚点
python3 eval/ledger_chain.py --json       # 链级 + 包级攻击全跑一遍

# 打一个带账本的审计包，并做 L2
python3 scripts/make_bundle.py --csv data/real/feitai_budget.csv --label 翡翠台 \
    --out /tmp/x.zmb.zip
python3 scripts/verify_bundle.py --zip /tmp/x.zmb.zip --run-l1
```

> 一条纪律：**锚点文件必须 commit**。锚点只在本地工作区，
> 等于链头只存在于攻击者能改的地方——那不是锚点，是便签。
