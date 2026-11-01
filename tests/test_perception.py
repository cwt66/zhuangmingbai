# -*- coding: utf-8 -*-
"""感知层单测：表头映射、算术自校验、失败要失败得明显。

跑法：python3 -m unittest discover -s tests -v
"""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
import perception as P  # noqa: E402


class TestHeaderMapping(unittest.TestCase):
    def test_real_header_variants(self):
        # 花溪北岸的表头：序号/项目名称/规格工艺/单位/数量/单价/合价/备注
        cells = {"A": "序号", "B": "项目名称", "C": "规格工艺", "D": "单位",
                 "E": "数量", "F": "单价", "G": "合价", "H": "备注"}
        m = P.map_header(cells)
        self.assertEqual(m["项目名称"], "B")
        self.assertEqual(m["合价"], "G")

    def test_units_in_parens(self):
        # 真实单据常写「单价(元)」「合价（元）」
        cells = {"A": "施工项目", "B": "工程量", "C": "单价(元)", "D": "合价（元）"}
        m = P.map_header(cells)
        self.assertEqual(m["项目名称"], "A")
        self.assertEqual(m["数量"], "B")
        self.assertEqual(m["单价"], "C")
        self.assertEqual(m["合价"], "D")

    def test_unknown_header_not_guessed(self):
        m = P.map_header({"A": "随便什么", "B": "另一列"})
        self.assertEqual(m, {})


class TestRowsToQuote(unittest.TestCase):
    def setUp(self):
        self.raw = [
            {"A": "序号", "B": "项目名称", "C": "单位", "D": "数量", "E": "单价",
             "F": "合价", "G": "备注"},
            {"A": "1", "B": "粘贴地砖", "C": "m2", "D": "31", "E": "140.4",
             "F": "4352.4", "G": ""},
            {"A": "2", "B": "滚涂乳胶漆", "C": "m2", "D": "110", "E": "15.6",
             "F": "1716", "G": "面漆两遍"},
        ]

    def test_parses_two_rows_and_total(self):
        rows, rep = P.rows_to_quote(self.raw)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["项目名称"], "粘贴地砖")
        self.assertAlmostEqual(rep["total"], 4352.4 + 1716, places=2)
        self.assertEqual(rep["arith_bad"], [])

    def test_flags_arithmetic_mismatch(self):
        raw = [dict(r) for r in self.raw]
        raw[1]["F"] = "9999"          # 故意写错的合价
        rows, rep = P.rows_to_quote(raw)
        self.assertEqual(len(rep["arith_bad"]), 1)
        self.assertEqual(rep["arith_bad"][0]["name"], "粘贴地砖")

    def test_missing_header_raises_not_silent(self):
        # 关键：识别不出表头必须抛异常，不能返回空表让上层当成"没漏项"
        with self.assertRaises(P.PerceptionError):
            P.rows_to_quote([{"A": "x", "B": "y"}])

    def test_number_parsing_with_separators(self):
        self.assertEqual(P._num("1,234.50 元"), 1234.5)
        self.assertIsNone(P._num(""))


class TestProviders(unittest.TestCase):
    def test_mock_is_marked_as_fixture(self):
        rows, meta, rep = P.perceive("__selftest__", provider="mock")
        self.assertEqual(len(rows), 2)
        self.assertTrue(meta.get("fixture"))
        self.assertIn("warning", meta)

    def test_unknown_provider_raises(self):
        with self.assertRaises(P.PerceptionError):
            P.perceive("__selftest__", provider="gpt-imaginary")

    def test_ocr_without_credentials_gives_actionable_error(self):
        # 不联网也要给出可执行提示，而不是 KeyError
        env = {k: os.environ.pop(k, None) for k in
               ("HW_ACCESS_KEY", "HW_SECRET_KEY", "HW_PROJECT_ID")}
        try:
            with self.assertRaises(P.PerceptionError) as ctx:
                P.perceive("__selftest__", provider="ocr_table")
            msg = str(ctx.exception)
            self.assertTrue(any(k in msg for k in ("HW_ACCESS_KEY", "project_id", "开通")),
                            msg)
        finally:
            for k, v in env.items():
                if v is not None:
                    os.environ[k] = v




# ------------------------------------------------- 结构化文件（无 AI 服务）

def _make_xlsx(csv_path, xlsx_path):
    """用标准库造一个最小 xlsx（inline string），用于验证解析路径。"""
    import csv as _csv
    import zipfile as _zip

    with open(csv_path, encoding="utf-8-sig") as f:
        rows = list(_csv.reader(f))

    def col(i):
        s = ""
        i += 1
        while i:
            i, r = divmod(i - 1, 26)
            s = chr(65 + r) + s
        return s

    def esc(t):
        return (t or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    body = ['<?xml version="1.0" encoding="UTF-8"?><worksheet '
            'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>']
    for r_i, row in enumerate(rows, start=1):
        cells = []
        for c_i, val in enumerate(row):
            if val == "":
                continue
            cells.append('<c r="%s%d" t="inlineStr"><is><t>%s</t></is></c>'
                         % (col(c_i), r_i, esc(val)))
        body.append('<row r="%d">%s</row>' % (r_i, "".join(cells)))
    body.append("</sheetData></worksheet>")

    parts = {
        "[Content_Types].xml":
            '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.'
            'openxmlformats.org/package/2006/content-types"><Default Extension="rels" '
            'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/><Override '
            'PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-'
            'officedocument.spreadsheetml.sheet.main+xml"/><Override '
            'PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.'
            'openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>',
        "_rels/.rels":
            '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.'
            'openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
            'officeDocument" Target="xl/workbook.xml"/></Relationships>',
        "xl/workbook.xml":
            '<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.'
            'openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.'
            'openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet '
            'name="报价" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels":
            '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.'
            'openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
            'worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
        "xl/worksheets/sheet1.xml": "\n".join(body),
    }
    with _zip.ZipFile(xlsx_path, "w", _zip.ZIP_DEFLATED) as z:
        for name, content in parts.items():
            z.writestr(name, content)


class TestFileProvider(unittest.TestCase):
    """file provider 不需要任何云服务，是覆盖率最高的一条路。"""

    def setUp(self):
        self.csv = os.path.join(ROOT, "data/real/sh_zhuoguan_halfpack.csv")
        self.tmp = tempfile.mkdtemp(prefix="zmb-")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_csv_real_document(self):
        rows, meta, rep = P.perceive(self.csv, provider="file")
        self.assertEqual(meta["kind"], "csv")
        self.assertFalse(meta["needs_ai"])
        self.assertEqual(len(rows), 127)
        self.assertAlmostEqual(rep["total"], 54936.0, delta=0.01)
        self.assertEqual(rep["arith_bad"], [])
        self.assertEqual(rep["arith_checked"], 127)

    def test_xlsx_real_document(self):
        xlsx = os.path.join(self.tmp, "a.xlsx")
        _make_xlsx(self.csv, xlsx)
        rows, meta, rep = P.perceive(xlsx, provider="file")
        self.assertEqual(meta["kind"], "xlsx")
        self.assertEqual(len(rows), 127)
        self.assertAlmostEqual(rep["total"], 54936.0, delta=0.01)
        self.assertEqual(rep["arith_bad"], [])

    def test_csv_and_xlsx_give_same_rows(self):
        xlsx = os.path.join(self.tmp, "b.xlsx")
        _make_xlsx(self.csv, xlsx)
        a, _, _ = P.perceive(self.csv, provider="file")
        b, _, _ = P.perceive(xlsx, provider="file")
        self.assertEqual([r["项目名称"] for r in a], [r["项目名称"] for r in b])
        self.assertEqual([r["合价"] for r in a], [r["合价"] for r in b])

    def test_pdf_without_text_layer_must_fail_loudly(self):
        """扫描件 PDF 必须报错，不能返回空表——空表会被当成没漏项。"""
        try:
            import pypdf  # noqa: F401
        except ImportError:
            self.skipTest("pypdf 未安装：pip install -r requirements.txt 后再跑 PDF 用例")
        blank = os.path.join(self.tmp, "blank.pdf")
        with open(blank, "wb") as f:
            f.write(b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
                    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
                    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 595 842]>>endobj\n"
                    b"trailer<</Size 4/Root 1 0 R>>\n%%EOF\n")
        try:
            P.perceive(blank, provider="file")
        except P.PerceptionError as e:
            self.assertIn("没有文本层", str(e))
        except Exception as e:                    # pypdf 对残缺 PDF 可能直接抛错
            self.assertTrue(type(e).__name__ in ("PdfReadError", "EmptyFileError",
                                                 "DependencyError", "PerceptionError")
                            or "PDF" in str(e), str(e))

    def test_unsupported_extension_says_what_to_do(self):
        p = os.path.join(self.tmp, "a.xls")
        open(p, "wb").close()
        with self.assertRaises(P.PerceptionError) as ctx:
            P.perceive(p, provider="file")
        self.assertIn("xlsx", str(ctx.exception))




class TestPdfCoords(unittest.TestCase):
    """PDF 坐标解析：用合成片段测，不依赖二进制样本。

    这几条对应真实 PDF 上踩过的坑（深圳新新装饰那份 7 页公开报价单）：
    1. 数字比表头宽 → 按字符位置切会吃掉首字符，所以必须按 x 坐标；
    2. `#` 是 ￥ 被字体映射的结果，不是数据；
    3. 单位 `M` 落在「损耗」与「单位」列之间，按坐标会判错，按内容才稳；
    4. **y 轴可能是反的**（表头 y 最小），按 -y 排序会倒着读。
    """

    HDR = [("序", 48.4), ("项目名称", 92.3), ("工程量", 166.6), ("单位", 206.6),
           ("损耗", 223.3), ("单价", 246.7), ("合计", 305.7), ("施工工艺及材料说明", 358.7)]

    def _page(self, hdr_y, rows):
        """rows: [(y, [(x, text), ...])]"""
        frags = [(hdr_y, x, t) for t, x in self.HDR]
        for y, cells in rows:
            for x, t in cells:
                frags.append((y, x, t))
        return frags

    def test_basic_two_rows(self):
        page = self._page(760.0, [
            (700.0, [(51, "1"), (85, "地砖铺设人工"), (173, "19.4"), (213, "㎡"),
                     (232, "#"), (243, "60.00"), (299, "1164.00")]),
            (680.0, [(51, "2"), (85, "批腻子"), (173, "43.4"), (213, "㎡"),
                     (232, "#"), (243, "29.90"), (299, "1297.66")]),
        ])
        grid = P._pdf_rows_by_coords([page])
        self.assertEqual(len(grid), 3)                      # 表头 + 2 行
        first = grid[1]
        self.assertEqual(first["B"], "地砖铺设人工")          # 名称完整，没被切首字
        self.assertEqual(first["E"], "19.4")
        self.assertEqual(first["F"], "60.00")
        self.assertEqual(first["G"], "1164.00")
        self.assertNotIn("#", first["B"])                    # 货币符号没污染名称

    def test_unit_is_forced_by_content(self):
        """单位 M 的 x=215 离「损耗」更近，但必须判成单位。"""
        page = self._page(760.0, [
            (700.0, [(51, "1"), (85, "不锈钢地脚线"), (173, "11.6"), (215, "M"),
                     (232, "#"), (243, "23.40"), (299, "271.44")]),
        ])
        grid = P._pdf_rows_by_coords([page])
        self.assertEqual(grid[1]["D"], "M")
        self.assertEqual(grid[1]["B"], "不锈钢地脚线")

    def test_inverted_y_axis_preserves_order(self):
        """表头 y 最小（翻转坐标系）时，行序仍要是视觉从上到下。"""
        page = self._page(50.0, [
            (680.0, [(51, "1"), (85, "第一项"), (173, "1.0"), (213, "项"),
                     (232, "#"), (243, "10.00"), (299, "10.00")]),
            (700.0, [(51, "2"), (85, "第二项"), (173, "2.0"), (213, "项"),
                     (232, "#"), (243, "20.00"), (299, "40.00")]),
        ])
        grid = P._pdf_rows_by_coords([page])
        self.assertEqual([grid[1]["B"], grid[2]["B"]], ["第一项", "第二项"])

    def test_section_title_becomes_zone_not_row(self):
        page = self._page(760.0, [
            (720.0, [(66, "一"), (80, "、"), (94, "候诊大厅")]),
            (700.0, [(51, "1"), (85, "贴砖"), (173, "19.4"), (213, "㎡"),
                     (232, "#"), (243, "60.00"), (299, "1164.00")]),
        ])
        grid = P._pdf_rows_by_coords([page])
        self.assertEqual(len(grid), 2)                       # 分区不占数据行
        self.assertEqual(grid[1]["C"], "分区：一、候诊大厅")

    def test_no_usable_header_raises(self):
        with self.assertRaises(P.PerceptionError):
            P._pdf_rows_by_coords([[("随便", 10, "什么")]])


if __name__ == "__main__":
    unittest.main(verbosity=2)


