# -*- coding: utf-8 -*-
"""分位金额的锁：0.1+0.2 必须等于 0.30。"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
import money  # noqa: E402


class TestMoney(unittest.TestCase):
    def test_float_trap(self):
        self.assertEqual(str(money.sum2([0.1, 0.2])), "0.30")
        self.assertTrue(money.eq2(money.sum2([0.1, 0.2]), 0.3))

    def test_round_half_up(self):
        self.assertEqual(str(money.q2("1.005")), "1.01")
        self.assertEqual(str(money.q2(2.675)), "2.68")

    def test_diff(self):
        self.assertEqual(str(money.diff2(100.00, 99.99)), "0.01")
        self.assertEqual(str(money.diff2(100, 100)), "0.00")

    def test_none(self):
        self.assertEqual(str(money.d("")), "0")
        self.assertEqual(str(money.d(None)), "0")


if __name__ == "__main__":
    unittest.main()
