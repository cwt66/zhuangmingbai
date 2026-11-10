# -*- coding: utf-8 -*-
"""RFC 6962 风格的 Merkle 包含证明（纯标准库、零依赖）。

## 为什么加这一层

2026-11-09 的公开锚点把**整张账本摘要**发到 gh-pages，L2 要下载全部条目
才能对照。6 条时无所谓，账本长到几千条就既不经济也不可审计：
评委只是想确认「我手里这一条在不在链上」，却要拿走整本账。

RFC 6962（Certificate Transparency，Sigstore Rekor / Trillian 同款）给的答案是
Merkle 根 + 审计路径：日志只发布一个 32 字节根哈希，每个持有者带一条
`ceil(log2(n))` 长的路径，就能自己算出同一个根，**不需要其他条目**。

## 规则（严格按 RFC 6962 §2.1）

    MTH({})       = SHA-256(b"")
    MTH({d0})     = SHA-256(0x00 || d0)              # 叶子
    MTH(D[n])     = SHA-256(0x01 || MTH(D[:k]) || MTH(D[k:]))   # 内部节点
    k             = 小于 n 的最大 2 的幂

0x00 / 0x01 是**域分隔**：没有它，攻击者可以把一个内部节点哈希
冒充叶子，构造两棵根相同、内容不同的树。

本项目的叶子 `d_i` 定义为**账本第 i 条 hash 的 32 个原始字节**
（`bytes.fromhex(entry["hash"])`）。条目全文由审计包里的 `ledger_entry.json`
携带，其 hash 必须与叶子一致；Merkle 根承诺的是「条目 hash 的有序序列」。

## 边界（必须一起说）

- 包含证明只证明「这个叶子在这棵树里」，**不证明树本身诚实**。
  根仍需外部锚点（仓库文件 + git + 公开 URL）背书。
- 我们**没有实现 RFC 6962 的紧凑一致性证明**（SUBPROOF），
  只提供 `verify_append_only`：拿到完整账本时检查旧根是新账本前缀的根。
  它验证的是同一性质，但不是对数长度的证明，不能冒充 Rekor 的 STH 一致性证明。
- 没有签名（STH 是签名的）。签名回执仍是对标清单里明确不补的差距。
"""
import hashlib


def _sha(data):
    return hashlib.sha256(data).digest()


def leaf_hash(data):
    return _sha(b"\x00" + data)


def node_hash(left, right):
    return _sha(b"\x01" + left + right)


def _split(n):
    """小于 n 的最大 2 的幂（n>1）。"""
    k = 1
    while k * 2 < n:
        k *= 2
    return k


def mth(leaves):
    """Merkle Tree Hash：leaves 是 bytes 列表。"""
    if not leaves:
        return _sha(b"")
    if len(leaves) == 1:
        return leaf_hash(leaves[0])
    k = _split(len(leaves))
    return node_hash(mth(leaves[:k]), mth(leaves[k:]))


def root_hex(entry_hashes):
    """从账本条目的 hash（64 位 hex 字符串列表）算根，返回 hex。"""
    return mth([bytes.fromhex(h) for h in entry_hashes]).hex()


def audit_path(entry_hashes, index):
    """返回 index（0 基）对应的审计路径，元素为 32 字节 hex。"""
    leaves = [bytes.fromhex(h) for h in entry_hashes]
    if not 0 <= index < len(leaves):
        raise ValueError("leaf_index 越界：%r / %d" % (index, len(leaves)))
    path, sub, i = [], leaves, index
    while len(sub) > 1:
        k = _split(len(sub))
        if i < k:
            path.append(mth(sub[k:]).hex())     # 右兄弟
            sub = sub[:k]
        else:
            path.append(mth(sub[:k]).hex())     # 左兄弟
            sub, i = sub[k:], i - k
    # 循环是「自顶向下」收集的；RFC 6962 的 PATH 定义是
    # PATH(内层) : MTH(兄弟)，即**自底向上**。不 reverse 会在
    # 非 2 的幂树上（n=3 起）算出错误的根——单测第一个抓到的就是这个。
    return list(reversed(path))


def make_proof(entry_hashes, index):
    """生成一个可直接写进 JSON 的包含证明。"""
    return {
        "schema": "zmb-merkle-inclusion/v1",
        "rfc": "RFC 6962 §2.1（0x00 叶子 / 0x01 节点域分隔）",
        "leaf_index": index,
        "tree_size": len(entry_hashes),
        "leaf": entry_hashes[index],
        "audit_path": audit_path(entry_hashes, index),
        "root": root_hex(entry_hashes),
    }


def verify_proof(proof, expect_root=None, expect_tree_size=None):
    """验证包含证明，返回 (ok, reason)。

    `expect_root` 是外部锚点公布的 32 字节 hex；传了就必须等于证明里的 root。
    `expect_tree_size` 是外部锚点公布的树大小（或本地账本条数）。
    必须传：RFC 6962-bis 的 fn/sn 算法**单独不把 tree_size 绑死**——
    实测把 6 叶证明的 tree_size 改成 5，仍能用同一条路径算出同一个根。
    根是由「条目序列」承诺的，树大小要由锚点另外承诺。
    """
    try:
        idx = proof["leaf_index"]
        n = proof["tree_size"]
        leaf = bytes.fromhex(proof["leaf"])
        path = [bytes.fromhex(h) for h in proof["audit_path"]]
        root = bytes.fromhex(proof["root"])
    except (KeyError, TypeError, ValueError) as e:
        return False, "证明字段不合法：%s" % e
    if len(leaf) != 32 or len(root) != 32 or any(len(x) != 32 for x in path):
        return False, "证明里的哈希不是 32 字节"
    if n < 1 or idx < 0 or idx >= n:
        return False, "leaf_index / tree_size 越界"
    if expect_tree_size is not None and n != expect_tree_size:
        return False, "tree_size 与外部锚点条数不符（%d vs %d）" % (n, expect_tree_size)
    if expect_root is not None:
        try:
            if bytes.fromhex(expect_root) != root:
                return False, "证明里的 root 与外部锚点不符"
        except ValueError:
            return False, "外部锚点的 root 不是合法 hex"

    # RFC 6962-bis §2.1.3.2 的 fn/sn 位运算验证。
    # 不能简单地「按 split 自顶向下消费」——审计路径是自底向上的，
    # 非 2 的幂树（n=3 起）会把左右顺序算反。单测抓到后改用标准算法。
    fn, sn = idx, n - 1
    r = leaf_hash(leaf)
    for sib in path:
        if sn == 0:
            return False, "审计路径过长"
        if (fn & 1) == 1 or fn == sn:
            r = node_hash(sib, r)
            while (fn & 1) == 0 and fn != 0:
                fn >>= 1
                sn >>= 1
        else:
            r = node_hash(r, sib)
        fn >>= 1
        sn >>= 1
    if sn != 0:
        return False, "审计路径过短（没走到根）"
    if r != root:
        return False, "由审计路径算出的根与证明里的 root 不符"
    return True, "OK"


def validate_public_anchor(pub):
    """公开锚点文档自身的完整性（两端共用，避免各写一套）。

    2026-11-10 修的两个洞：
    ① `publish_pages.py --check` 原来只比 seq/head/entries，
       摘要表被改也判 PASS（实测复现）；
    ② `verify_bundle.py` 的 L2-c 只比第 seq 条摘要，
       没有检查 head 是否等于最后一条、root 是否由摘要算出。
    """
    import re
    errs = []
    seq = pub.get("seq")
    entries = pub.get("entries")
    sums = pub.get("entry_summaries") or []
    head = pub.get("head")
    root = pub.get("merkle_root")
    hx = lambda s: isinstance(s, str) and bool(re.fullmatch(r"[0-9a-f]{64}", s))  # noqa: E731

    if not isinstance(seq, int) or seq < 1:
        errs.append("seq 非法")
    if entries != seq or len(sums) != seq:
        errs.append("entries / 摘要条数与 seq 不一致（%r / %d / %d）"
                    % (entries, len(sums), seq if isinstance(seq, int) else -1))
    for i, s in enumerate(sums, 1):
        if s.get("seq") != i:
            errs.append("第 %d 条摘要 seq=%r（断号或重排）" % (i, s.get("seq")))
        if not hx(s.get("hash")):
            errs.append("第 %d 条摘要 hash 非法" % i)
    if sums and head != sums[-1].get("hash"):
        errs.append("head 不是最后一条摘要的 hash")
    if root is not None:
        if not hx(root):
            errs.append("merkle_root 不是 64 位 hex")
        elif sums and all(hx(s.get("hash")) for s in sums):
            if root_hex([s["hash"] for s in sums]) != root:
                errs.append("merkle_root 与摘要序列算出的根不符")
    elif pub.get("schema", "").startswith("zmb-ledger-public-anchor"):
        errs.append("缺 merkle_root")
    return (not errs), errs


def verify_append_only(old_root_hex, old_size, entry_hashes):
    """完整账本下的 append-only 检查：旧根必须是新账本前 old_size 条的根。

    这不是 RFC 6962 的紧凑一致性证明（那个只要对数长度路径），
    只是「我们手里有整本账」时的前缀校验——不冒充。
    """
    if old_size < 1 or old_size > len(entry_hashes):
        return False, "旧树大小越界"
    got = root_hex(entry_hashes[:old_size])
    return (got == old_root_hex), ("前缀根一致" if got == old_root_hex
                                   else "前缀根不一致（历史被重写过）")
