# -*- coding: utf-8 -*-
"""哈希链审计账本：把「每一次判定」串成一条改不动的链。

## 为什么需要它（不是加个哈希就叫安全）

2026-11-09 之前，审计包里 `verify.py` 只校验**输入文件**的 sha256。
我实际跑了一次攻击：从 `templates_v0.json` 删掉 3 个必做项，
漏项从 12 掉到 10，再让包里的引擎自己重算出新 digest、写回 `result.json`——
`verify.py` 依然打印 `[PASS]`。

根因不是「忘了校验标准」，而是**记录真相的账本和被审的东西放在同一个可改容器里**。
只要攻击者能同时改「标准」和「结论记录」，任何自洽性校验都会被一起说服。

哈希链解决的是「事后改一条」。真正让「整条重写」也藏不住的是**外部锚点**：
链头哈希必须出现在链外的某个地方（仓库文件 + git 历史 + 台账），
攻击者改完链还得改锚点，改完锚点还得改 git 历史——成本与可见性都不是一个量级。

## 结构

每条 entry 一行 JSON：
    {"seq":1, "ts":"...", "kind":"audit", "payload":{...}, "prev":"00..0", "hash":"..."}

    hash = sha256(canonical(seq, ts, kind, payload) + "|" + prev)

于是：
- 改任一条的 payload → 该条 hash 变 → 与下一条 prev 断开；
- 删中间一条 → seq 断号 + prev 断链；
- 换顺序 → prev 断链；
- 截尾 → 链内自洽，只有锚点能发现；
- 整条重写 → 链内自洽，只有锚点能发现。

## 与华为云的关系（代码就绪、实例未开通）

账本设计成两处落：
1. **RDS（append-only 表）** —— `RDS_DDL` 里的表结构带 `prev_hash` 与
   `seq BIGSERIAL`，写入只允许 INSERT，撤销靠新增一条 `kind="revoke"`，
   不 UPDATE/DELETE（数据库层面用权限收口，见 docs/HASH-CHAIN-LEDGER.md）。
2. **OBS（对象锁存锚点）** —— 每次 anchor 把 `{seq, head}` 写成对象；
   开了对象锁（WORM）后连账号所有者也不能覆盖。

**这两条目前都只是代码与 DDL，没有在真实例上跑过。**
`docs/华为云依赖与失效矩阵.md` 里 RDS/DCS 的状态写着「代码就绪、实例未开通」，
这份账本不改变那个事实——材料里不写「已落库到 RDS」。
"""
import hashlib
import json
import os

GENESIS = "0" * 64


def canonical(obj):
    """稳定序列化：排序键、不留空格、保留中文。跨机器逐字节一致。"""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"))


def entry_hash(seq, ts, kind, payload, prev, dedupe_key=None):
    """哈希覆盖条目的**全部**字段。

    2026-11-09 修的一个洞：dedupe_key 原来加在 hash 之后，
    篡改它链照样绿（实测确认）。它是「这条记录的身份」，
    身份可以被悄悄改掉，防篡改就是半成品——所以它必须进哈希。
    """
    body = {"seq": seq, "ts": ts, "kind": kind, "payload": payload}
    if dedupe_key is not None:
        body["dk"] = dedupe_key
    return hashlib.sha256((canonical(body) + "|" + prev).encode("utf-8")).hexdigest()


def make_entry(seq, ts, kind, payload, prev, dedupe_key=None):
    e = {"seq": seq, "ts": ts, "kind": kind, "payload": payload,
         "prev": prev, "hash": entry_hash(seq, ts, kind, payload, prev,
                                          dedupe_key)}
    if dedupe_key is not None:
        e["dedupe_key"] = dedupe_key
    return e


def load(path):
    """读 JSONL。空文件 = 空链；坏行直接抛，不静默跳过。"""
    if not os.path.isfile(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError as e:
                raise ValueError("第 %d 行不是合法 JSON：%s" % (i, e))
    return out


def dumps(entries):
    return "".join(canonical(e) + "\n" for e in entries)


def append(path, kind, payload, ts, dedupe_key=None, force=False):
    """追加一条。

    `dedupe_key` 是「这条记录的身份」。默认用 payload 本身，但审计记录的身份
    应该由**内容**决定（输入 + 标准 + 代码 + 结论），显示用的单据名不算——
    同一份 CSV 叫「翡翠台」还是「feitai_budget」，判定是同一个，链上也该是同一条。
    2026-11-09 因为把 label 算进身份，同一份单据在链上重复出现了两次。
    """
    entries = load(path)
    body = canonical(payload)
    key = dedupe_key if dedupe_key is not None else body
    if not force:
        for e in entries:
            if e.get("kind") != kind:
                continue
            prev_key = e.get("dedupe_key")
            if prev_key is None:
                prev_key = canonical(e.get("payload"))
            if prev_key == key:
                return e, False
    prev = entries[-1]["hash"] if entries else GENESIS
    # dedupe_key 必须在 make_entry 里进哈希——事后补写在链外，等于没保护
    e = make_entry(len(entries) + 1, ts, kind, payload, prev, dedupe_key)
    d = os.path.dirname(os.path.abspath(path))
    if d and not os.path.isdir(d):
        os.makedirs(d)
    with open(path, "a", encoding="utf-8") as f:
        f.write(canonical(e) + "\n")
    return e, True


def verify(entries):
    """链内自洽性。返回 (ok, violations)。

    只查「链有没有断」，不查「链是不是真的」——后者要靠 anchor。
    """
    v = []
    if not entries:
        return False, ["账本为空"]
    for i, e in enumerate(entries):
        want_seq = i + 1
        if e.get("seq") != want_seq:
            v.append("第 %d 条 seq=%r，应为 %d（断号或重排）"
                     % (want_seq, e.get("seq"), want_seq))
            continue
        for k in ("ts", "kind", "payload", "prev", "hash"):
            if k not in e:
                v.append("第 %d 条缺字段 %s" % (want_seq, k))
        if v and v[-1].startswith("第 %d 条缺字段" % want_seq):
            continue
        want_prev = GENESIS if i == 0 else entries[i - 1]["hash"]
        if e["prev"] != want_prev:
            v.append("第 %d 条 prev 断链（记录 %s…，应为 %s…）"
                     % (want_seq, str(e["prev"])[:12], want_prev[:12]))
        got = entry_hash(e["seq"], e["ts"], e["kind"], e["payload"], e["prev"],
                         e.get("dedupe_key"))
        if got != e["hash"]:
            v.append("第 %d 条内容被改（hash 记录 %s…，重算 %s…）"
                     % (want_seq, str(e["hash"])[:12], got[:12]))
    return (not v), v


def check_anchor(entries, anchor):
    """锚点校验：链头必须等于锚点记的 head，且条数不能少于锚点 seq。

    - 条数 < anchor.seq → 被截尾；
    - 条数 == anchor.seq 但 head 不等 → 被整条重写；
    - 条数 > anchor.seq → 新增未锚定（尾部还不具备防篡改能力）。
    """
    v = []
    if not anchor:
        return False, ["缺锚点：没有锚点的哈希链只能防改单条，防不了整条重写"]
    seq = anchor.get("seq")
    head = anchor.get("head")
    if not isinstance(seq, int) or seq < 1:
        return False, ["锚点 seq 非法：%r" % (seq,)]
    if not isinstance(head, str) or len(head) != 64:
        return False, ["锚点 head 非法：%r" % (str(head)[:20],)]
    if len(entries) < seq:
        v.append("账本被截断：现存 %d 条，锚点说至少 %d 条" % (len(entries), seq))
        return False, v
    got = entries[seq - 1]["hash"]
    if got != head:
        v.append("锚点不符：第 %d 条 hash 记录 %s…，实际 %s…（整条可能被重写）"
                 % (seq, head[:12], got[:12]))
    extra = len(entries) - seq
    if extra > 0:
        v.append("锚点落后 %d 条：新追加的记录还没被锚定，尾部暂不具备防篡改能力"
                 % extra)
    return (not v), v


# RDS 侧的 append-only 表。代码就绪、实例未开通——材料里不写「已落库」。
RDS_DDL = """
CREATE TABLE IF NOT EXISTS audit_ledger (
    seq        BIGSERIAL PRIMARY KEY,
    ts         TEXT        NOT NULL,
    kind       TEXT        NOT NULL,
    payload    JSONB       NOT NULL,
    prev_hash  CHAR(64)    NOT NULL,
    hash       CHAR(64)    NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- 只允许追加：撤销一条结论靠写一条 kind='revoke'，不 UPDATE / DELETE
REVOKE UPDATE, DELETE ON audit_ledger FROM zmb_app;
GRANT  INSERT, SELECT   ON audit_ledger TO   zmb_app;
"""
