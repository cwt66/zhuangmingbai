# -*- coding: utf-8 -*-
"""Merkle 包含证明：正确性与抗篡改。

依据 RFC 6962 §2.1（Sigstore Rekor / CT 同款）。
不测「实现得像不像」，测「换一个字节会不会被抓到」。
"""
import hashlib
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
import merkle as M  # noqa: E402


def leaves(n):
    return [hashlib.sha256(("entry-%d" % i).encode()).hexdigest()
            for i in range(n)]


class TestMerkle(unittest.TestCase):
    def test_domain_separation(self):
        """0x00 叶子 / 0x01 节点：同样的输入不能混用。"""
        d = b"abc"
        self.assertNotEqual(M.leaf_hash(d), M._sha(d))
        self.assertNotEqual(M.node_hash(b"a" * 32, b"b" * 32),
                            M._sha(b"a" * 32 + b"b" * 32))

    def test_empty_and_single(self):
        self.assertEqual(M.mth([]), M._sha(b""))
        self.assertEqual(M.mth([b"x"]), M.leaf_hash(b"x"))

    def test_proof_all_indices_1_to_33(self):
        """每种树形（含非 2 的幂）的每条都要能被验出来。"""
        for n in range(1, 34):
            ls = leaves(n)
            root = M.root_hex(ls)
            for i in range(n):
                p = M.make_proof(ls, i)
                ok, why = M.verify_proof(p, expect_root=root)
                self.assertTrue(ok, "n=%d i=%d: %s" % (n, i, why))
                self.assertLessEqual(len(p["audit_path"]),
                                     (n - 1).bit_length() + 1)

    def test_known_rfc6962_shape(self):
        """n=7 时 d0 的审计路径长度应为 3（RFC 6962 §2.1.3 例图）。"""
        self.assertEqual(len(M.audit_path(leaves(7), 0)), 3)
        self.assertEqual(len(M.audit_path(leaves(7), 6)), 2)

    def test_tamper_leaf_fails(self):
        ls = leaves(8)
        p = M.make_proof(ls, 3)
        p["leaf"] = leaves(1)[0]
        ok, _ = M.verify_proof(p)
        self.assertFalse(ok)

    def test_tamper_path_fails(self):
        p = M.make_proof(leaves(8), 3)
        p["audit_path"][0] = "0" * 64
        ok, _ = M.verify_proof(p)
        self.assertFalse(ok)

    def test_wrong_index_fails(self):
        p = M.make_proof(leaves(8), 3)
        p["leaf_index"] = 4
        ok, _ = M.verify_proof(p)
        self.assertFalse(ok)

    def test_tree_size_must_match_external_anchor(self):
        """真实边界回归：RFC fn/sn 算法单独不绑 tree_size，
        6 叶证明改成 5 仍算出同一个根；必须由外部条数约束。"""
        p = M.make_proof(leaves(6), 2)
        ok, _ = M.verify_proof(p)              # 不给外部条数：确实能过
        self.assertTrue(ok)
        p["tree_size"] = 5
        ok2, why = M.verify_proof(p, expect_tree_size=6)
        self.assertFalse(ok2)
        self.assertIn("tree_size", why)

    def test_public_anchor_consistency(self):
        """D2/D3 回归：摘要表 / head / merkle_root 各自被抓。"""
        hs = leaves(6)
        pub = {"schema": "zmb-ledger-public-anchor/v1",
               "seq": 6, "entries": 6, "head": hs[-1],
               "merkle_root": M.root_hex(hs),
               "entry_summaries": [
                   {"seq": i + 1, "hash": h, "result_digest": "d%d" % i}
                   for i, h in enumerate(hs)]}
        ok, errs = M.validate_public_anchor(pub)
        self.assertTrue(ok, errs)
        for mutate in (
                lambda q: q.update({"head": "0" * 64}),
                lambda q: q.update({"merkle_root": "0" * 64}),
                lambda q: q["entry_summaries"][2].update({"hash": "1" * 64}),
                lambda q: q["entry_summaries"][2].update({"seq": 99}),
                lambda q: q.update({"entries": 5}),
                lambda q: q.pop("merkle_root")):
            q = dict(pub)
            q["entry_summaries"] = [dict(x) for x in pub["entry_summaries"]]
            mutate(q)
            ok2, _ = M.validate_public_anchor(q)
            self.assertFalse(ok2, mutate)

    def test_wrong_external_root_fails(self):
        p = M.make_proof(leaves(8), 3)
        ok, why = M.verify_proof(p, expect_root="f" * 64)
        self.assertFalse(ok)
        self.assertIn("外部锚点", why)

    def test_reorder_or_delete_changes_root(self):
        ls = leaves(7)
        r = M.root_hex(ls)
        self.assertNotEqual(r, M.root_hex(list(reversed(ls))))
        self.assertNotEqual(r, M.root_hex(ls[:-1]))

    def test_append_only_prefix(self):
        old_root = M.root_hex(leaves(4))
        ok, _ = M.verify_append_only(old_root, 4, leaves(9))
        self.assertTrue(ok)
        # 把前 4 条换掉 → 前缀根不再一致
        changed = ["0" * 64] + leaves(9)[1:]
        ok2, _ = M.verify_append_only(old_root, 4, changed)
        self.assertFalse(ok2)

    def test_append_only_boundary(self):
        ok, why = M.verify_append_only(M.root_hex(leaves(4)), 40, leaves(9))
        self.assertFalse(ok)
        self.assertIn("越界", why)


if __name__ == "__main__":
    unittest.main()
