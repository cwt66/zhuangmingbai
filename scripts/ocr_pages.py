# -*- coding: utf-8 -*-
"""一叠报价单照片 → 逐页 OCR → 合并成一份标准 CSV

为什么需要这个脚本：
  华为云「通用表格识别」每页独立返回，而真实单据从第 2 页起通常不再重复表头。
  逐页调 rows_to_quote 会在续页直接报「前 8 行里没找到可识别的表头」。
  本脚本先取每页原始网格，首页识别表头，续页沿用同一映射，最后统一清理：
    ・分区标题行（无数值）→ 变成后续行的 规格工艺=分区：XXX
    ・小计行 → 保留用于页级对账，不进入审计行
    ・页边噪声行（只有列 A 的数字/空名）→ 丢弃

用法：
  HW_ACCESS_KEY=... HW_SECRET_KEY=... HW_REGION_NAME=cn-north-4 \
  python3 scripts/ocr_pages.py --pages a.jpg b.jpg c.jpg --out data/real/xxx.csv
"""
import argparse
import base64
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
import perception as P  # noqa: E402


def ocr_raw_grid(path, ak, sk, region):
    """调通用表格识别，返回原始网格（不做表头映射）。"""
    img = open(path, "rb").read()
    b64 = base64.b64encode(img).decode()
    pid = P._project_id(ak, sk, region)
    host = "ocr.%s.myhuaweicloud.com" % region
    uri = "/v2/%s/ocr/general-table" % pid
    body = json.dumps({"image": b64, "return_excel": True,
                       "return_confidence": True}).encode()
    import urllib.request
    # 踩过的坑（见 src/perception.py）：URL 不带尾斜杠，但签名 URI 必须带
    headers = P._sign(ak, sk, "POST", host, uri + "/", body)
    req = urllib.request.Request("https://" + host + uri, data=body, method="POST",
                                 headers=headers)
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read())
    result = data.get("result") or {}
    excel_b64 = result.get("excel")
    if not excel_b64:
        raise SystemExit("OCR 未返回 excel（可能表格未识别）: %s" % path)
    return P._xlsx_to_rows(base64.b64decode(excel_b64))


def is_subtotal(name):
    return ("小计" in (name or "")) or ("合计" in (name or "")) or ("总计" in (name or ""))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", nargs="+", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--label", default="OCR")
    ap.add_argument("--header-page", type=int, default=0, help="第几页有表头（0 起）")
    args = ap.parse_args()

    ak = os.environ.get("HW_ACCESS_KEY")
    sk = os.environ.get("HW_SECRET_KEY")
    region = os.environ.get("HW_REGION_NAME", "cn-north-4")
    if not (ak and sk):
        raise SystemExit("需要 HW_ACCESS_KEY / HW_SECRET_KEY")

    grids = []
    for i, p in enumerate(args.pages):
        g = ocr_raw_grid(p, ak, sk, region)
        grids.append(g)
        print("页 %d/%d OCR: %s → %d 行网格" % (i + 1, len(args.pages), os.path.basename(p), len(g)))

    # 表头：用指定页（默认首页）
    hp = args.header_page
    mapping, header_idx = None, None
    for i in range(hp, min(hp + 3, len(grids))):
        for j, cells in enumerate(grids[i][:8]):
            m = P.map_header(cells)
            if len(m) >= 4 and "项目名称" in m:
                if "合价" not in m and j + 1 < len(grids[i]):
                    for k, v in P.map_header(grids[i][j + 1]).items():
                        m.setdefault(k, v)
                body = grids[i][j + 1:]
                if "合价" not in m and m.get("单价"):
                    cand = P._next_col(m["单价"])
                    if P._num_ratio(body, cand) >= 0.5:
                        m["合价"] = cand
                if "备注" not in m:
                    cols = sorted({c for r in body for c in r if (r.get(c) or "").strip()})
                    if cols and P._num_ratio(body, cols[-1]) < 0.5:
                        m["备注"] = cols[-1]
                mapping, header_idx = m, j
                break
        if mapping:
            print("表头取自第 %d 页第 %d 行：%s" % (i + 1, header_idx + 1, mapping))
            break
    if not mapping:
        raise SystemExit("前 3 页的 8 行内都没找到可识别表头")

    col = lambda std: mapping.get(std)  # noqa: E731
    rows, subtotals, zones = [], [], []
    cur_zone = ""
    for pi, g in enumerate(grids):
        start = header_idx + 1 if pi == hp else 0
        for cells in g[start:]:
            name = (cells.get(col("项目名称") or "", "") or "").strip()
            qty = P._num(cells.get(col("数量") or ""))
            price = P._num(cells.get(col("单价") or ""))
            amount = P._num(cells.get(col("合价") or ""))
            if not name:
                continue
            if is_subtotal(name):
                subtotals.append({"page": pi + 1, "zone": cur_zone, "amount": amount})
                continue
            if qty is None and price is None and amount is None:
                # 分区标题行
                if len(name) <= 20:
                    cur_zone = name
                    zones.append(name)
                continue
            rows.append({
                "分区": cur_zone,
                "项目名称": name,
                "单位": (cells.get(col("单位") or "", "") or "").strip(),
                "数量": qty, "单价": price, "合价": amount,
                "备注": (cells.get(col("备注") or "", "") or "").strip(),
                "page": pi + 1,
            })

    # 写出标准 CSV + 页级对账
    import csv
    with open(args.out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["序号", "项目名称", "规格工艺", "单位", "数量", "单价", "合价", "备注"])
        for i, r in enumerate(rows, 1):
            w.writerow([i, r["项目名称"], "分区：" + r["分区"], r["单位"],
                        r["数量"], r["单价"], r["合价"], r["备注"]])

    bad = []
    for r in rows:
        if r["数量"] is not None and r["单价"] is not None and r["合价"] is not None:
            calc = round(r["数量"] * r["单价"], 2)
            if abs(calc - r["合价"]) > max(1.0, r["合价"] * 0.01):
                bad.append((r["项目名称"], r["数量"], r["单价"], r["合价"], calc))

    print("\n=== 结果 ===")
    print("分区: %d 个 → %s" % (len(zones), " / ".join(zones[:12])))
    print("行项: %d ｜ 小计行: %d ｜ 算术不符: %d" % (len(rows), len(subtotals), len(bad)))
    for b in bad[:10]:
        print("  ! %s 数量%s×单价%s=%s（推算 %s）" % b)
    print("小计（用于对账）: %s" % [(s["page"], s["zone"], s["amount"]) for s in subtotals])
    print("已写入:", args.out)


if __name__ == "__main__":
    main()
