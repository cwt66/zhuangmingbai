# -*- coding: utf-8 -*-
"""本地 OCR 感知层的锁：重建 + critic。

这些测试不依赖 macOS Vision 二进制（CI 也能跑），只测纯逻辑：
- 表格重建能否把一行「名称/单位/数量/单价/合价」拼对；
- 量价不符、缺字段、低置信是否会被 critic 抓住，而不是静默进审计。
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
import vision_ocr as V  # noqa: E402


def box(text, cx, cy, conf=0.9, w=60, h=16):
    return {"text": text, "conf": conf, "x": cx - w / 2, "y": cy - h / 2,
            "w": w, "h": h, "cx": cx, "cy": cy}


HEADER = [
    box("序号", 170, 100), box("项目名称", 250, 100), box("单位", 390, 100),
    box("工程量", 445, 100), box("单价", 525, 100), box("小计", 695, 100),
]


class TestVisionLogic(unittest.TestCase):
    def test_num_fix_space_decimal(self):
        self.assertEqual(V._num_fix("180 00"), 180.0)
        self.assertEqual(V._num_fix("1,234.50"), 1234.5)
        self.assertIsNone(V._num_fix("abc"))

    def test_reconstruct_one_row(self):
        boxes = HEADER + [
            box("1", 170, 130), box("打拆墙体", 250, 130), box("㎡", 390, 130),
            box("16.00", 445, 130), box("70.00", 525, 130), box("1120.00", 700, 130),
        ]
        rows = V.reconstruct(boxes)
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["项目名称"], "打拆墙体")
        self.assertEqual(r["单位"], "㎡")
        self.assertEqual(r["数量"], 16.0)
        self.assertEqual(r["单价"], 70.0)
        self.assertEqual(r["合价"], 1120.0)
        self.assertEqual(r["_flags"], [])

    def test_arithmetic_mismatch_flagged(self):
        boxes = HEADER + [
            box("1", 170, 130), box("新做门洞过梁", 250, 130), box("根", 390, 130),
            box("3.00", 445, 130), box("180.00", 525, 130), box("510.00", 700, 130),
        ]
        rows = V.reconstruct(boxes)
        self.assertIn("量价不符", rows[0]["_flags"])

    def test_missing_amount_flagged(self):
        boxes = HEADER + [
            box("1", 170, 130), box("打拆墙体", 250, 130), box("㎡", 390, 130),
            box("16.00", 445, 130), box("70.00", 525, 130),
        ]
        rows = V.reconstruct(boxes)
        self.assertIn("缺合价", rows[0]["_flags"])

    def test_low_confidence_flagged(self):
        boxes = HEADER + [
            box("1", 170, 130, conf=0.1), box("打拆墙体", 250, 130, conf=0.1),
            box("㎡", 390, 130, conf=0.1), box("16.00", 445, 130, conf=0.1),
            box("70.00", 525, 130, conf=0.1), box("1120.00", 700, 130, conf=0.1),
        ]
        rows = V.reconstruct(boxes)
        self.assertIn("低置信", rows[0]["_flags"])

    def test_header_missing_raises(self):
        with self.assertRaises(V.VisionTableError):
            V.reconstruct([box("随便", 100, 100)])

    def test_available_returns_tuple(self):
        ok, why = V.available()
        self.assertIsInstance(ok, bool)
        self.assertIsInstance(why, str)


if __name__ == "__main__":
    unittest.main()
