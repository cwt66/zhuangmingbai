# -*- coding: utf-8 -*-
"""隐私扫描器自己也要被查：别让「白名单」变成「不扫了」。

起因：主张台账把撤回档案的 SHA-256 指纹写进 `data/claims_v0.json`，
十六进制串里一段连续数字被银行卡正则认成 19 位卡号，自检第 8 步直接红。
修法不是把整个文件加白名单，而是只跳过带摘要键名的那一行；
这两条测试锁住「跳过得够窄」——真卡号在普通行里仍然必须报出来。
"""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import pii_scan  # noqa: E402


def _scan(text):
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                    encoding="utf-8") as f:
        f.write(text)
        path = f.name
    try:
        return pii_scan.scan_file(path)
    finally:
        os.unlink(path)


class PiiScanTests(unittest.TestCase):
    def test_digest_line_is_not_a_bank_card(self):
        line = '  "history_digest": "02e538b025e6f724f404f3742df5af5932599728824666683aadeaf3ee6b0a43"\n'
        self.assertEqual(_scan("{\n" + line + "}\n"), [])

    def test_real_card_on_normal_line_still_flagged(self):
        # 同样的数字串，去掉摘要键名就必须被报出来——否则白名单等于关掉扫描
        hits = _scan('{"note": "5932599728824666683"}\n')
        self.assertTrue(any(h[1] == "bank_card" for h in hits), hits)

    def test_unrelated_line_in_same_file_still_scanned(self):
        text = ('{\n'
                '  "history_digest": "02e538b025e6f724f404f3742df5af5932599728824666683aadeaf3ee6b0a43",\n'
                '  "phone": "13912345678"\n'
                '}\n')
        hits = _scan(text)
        self.assertTrue(any(h[1] == "phone" for h in hits), hits)

    def test_whitelist_number_is_not_flagged(self):
        self.assertEqual(_scan('{"demo": "13800138000"}\n'), [])


if __name__ == "__main__":
    unittest.main()


class TestGitAddressesNotPii(unittest.TestCase):
    """git 远程地址与 GitHub noreply 不是个人信息（2026-11-09 误报回归）。"""

    def test_git_remote_host_is_not_email(self):
        hits = _scan('git push ssh://git@ssh.github.com:443/o/r.git\n')
        self.assertEqual(hits, [])

    def test_github_noreply_is_not_pii(self):
        hits = _scan('-c user.email=cwt66@users.noreply.github.com\n')
        self.assertEqual(hits, [])

    def test_real_email_still_caught(self):
        self.assertTrue(_scan('联系：someone@example.com\n'))
