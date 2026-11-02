# -*- coding: utf-8 -*-
"""本地 OCR 感知层：macOS Vision + 表格重建 + 确定性 critic。

为什么加这一层
--------------
云端 MaaS / OCR 需要 Key 与额度；而真实用户第一步就是"拍一张报价单"。
macOS 自带的 Vision 框架可以在**本机**完成中文/英文文字识别，图片不出机器。
它只负责"看见"；表格重建是启发式的，所以每条重建结果都要过一道 **critic**：

- 缺项目名 / 缺数量 / 缺单价 / 缺合价 → 降级为"需核对"，不当作已识别；
- 数量 × 单价 ≠ 合价（容差 2%）→ 标"量价不符"，审计层会把它变成算术风险；
- 置信度 < 0.5 → 标"低置信"。

设计原则与判断层一致：**宁可承认没看清，也不把噪声当结论。**
这个 provider 只在 macOS 且编译了 `scripts/bin/vision_ocr` 时可用；
其他平台自动提示不可用，不影响 file / maas / ocr_table。

用法：
    bash scripts/build_vision_ocr.sh
    python3 scripts/ocr_vision.py 报价单.jpg --out out/quote_vision.csv
"""
import json
import os
import re
import shutil
import statistics
import subprocess

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN = os.path.join(BASE, "scripts", "bin", "vision_ocr")

UNIT_TOKENS = ("m²", "m2", "㎡", "m", "米", "平方", "项", "根", "套", "个", "块",
               "条", "组", "樘", "处", "只", "张", "延米", "kg", "卷", "桶")


class VisionUnavailable(RuntimeError):
    pass


class VisionTableError(RuntimeError):
    pass


def binary_path():
    return os.environ.get("VISION_OCR_BIN") or (BIN if os.path.isfile(BIN) else None)


def available():
    if os.uname().sysname != "Darwin":
        return False, "仅 macOS 支持本地 Vision OCR"
    p = binary_path()
    if not p:
        return False, "未编译；运行 bash scripts/build_vision_ocr.sh"
    return True, p


def ocr_boxes(image_path, lang="zh-Hans", timeout=180):
    ok, info = available()
    if not ok:
        raise VisionUnavailable(info)
    try:
        out = subprocess.run([info, image_path, lang], capture_output=True,
                             timeout=timeout, check=False)
    except Exception as e:  # noqa: BLE001
        raise VisionUnavailable("Vision OCR 调用失败：%s" % e)
    if out.returncode != 0:
        raise VisionUnavailable("Vision OCR 返回 %d：%s" % (
            out.returncode, out.stderr.decode(errors="ignore")[:300]))
    try:
        boxes = json.loads(out.stdout.decode("utf-8", "ignore"))
    except Exception as e:  # noqa: BLE001
        raise VisionUnavailable("Vision OCR 输出不是 JSON：%s" % e)
    clean = []
    for b in boxes:
        t = (b.get("text") or "").strip()
        if not t:
            continue
        clean.append({"text": t, "conf": float(b.get("conf") or 0),
                      "x": float(b["x"]), "y": float(b["y"]),
                      "w": float(b["w"]), "h": float(b["h"]),
                      "cx": float(b["cx"]), "cy": float(b["cy"])})
    return clean


def _has(text, keys):
    return any(k in (text or "") for k in keys)


def _find_header(boxes):
    """找表头：单位 + 工程量/数量 是硬条件；项目/单价/小计/备注尽量找。"""
    def first(keys, y_max=400):
        best = None
        for b in boxes:
            if b["cy"] <= y_max and _has(b["text"], keys):
                if best is None or b["cy"] < best["cy"]:
                    best = b
        return best

    unit = first(["单位"])
    qty = first(["工程量", "数量"])
    if not unit or not qty:
        raise VisionTableError("表头未识别到「单位 / 工程量」，无法重建表格")
    name = first(["项目名称", "项目", "产品名称", "名称", "品名"])
    price = first(["单价"])
    amount = first(["小计", "合价", "金额", "金額", "合计"])
    note = first(["备注"])
    return {"unit": unit, "qty": qty, "name": name, "price": price,
            "amount": amount, "note": note}


def _num(text):
    """从 OCR 文本里取第一个数；修「180 00」这类被空格拆开的小数。"""
    t = (text or "").replace("，", ",").replace(" ", "")
    m = re.search(r"\d+[.,]\d{2}$", t)
    if m and " " in (text or ""):
        pass
    m = re.search(r"\d[\d,]*(?:\.\d+)?", t)
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def _num_fix(text):
    """处理 OCR 把 180.00 读成「180 00」的情况。"""
    t = (text or "").strip()
    if re.search(r"\d+\s+\d{2}\b", t) and not re.search(r"\d+\.\d", t):
        t = re.sub(r"(\d+)\s+(\d{2})\b", r"\1.\2", t)
    return _num(t)


def _unit_of(text):
    t = (text or "").strip()
    t = t.replace('"', "²").replace("”", "²").replace("'", "").replace("’", "")
    for u in UNIT_TOKENS:
        if t.lower() == u.lower():
            return "㎡" if u in ("m2", "m²", "平方") else ("m" if u in ("米",) else u)
    for u in UNIT_TOKENS:
        if u in t:
            return "㎡" if u in ("m2", "m²", "平方") else ("m" if u in ("米",) else u)
    return t[:4]


def _join(items):
    if not items:
        return ""
    # 中文 OCR 常在字间加空格；去掉空格再拼，英文保留一个空格
    s = "".join(x["text"].strip() for x in items)
    return re.sub(r"\s+", " ", s).strip()


def _cluster_rows(boxes, qty_x, price_x, header_y):
    anchors = [b for b in boxes if b["cy"] > header_y + 6 and re.search(r"\d", b["text"])
               and (abs(b["cx"] - qty_x) < 50 or abs(b["cx"] - price_x) < 50)]
    rows = []
    for a in sorted(anchors, key=lambda b: b["cy"]):
        if rows and a["cy"] - rows[-1]["cy"] < 8:
            rows[-1]["items"].append(a)
            rows[-1]["cy"] = statistics.mean(x["cy"] for x in rows[-1]["items"])
        else:
            rows.append({"cy": a["cy"], "items": [a]})
    return rows


def reconstruct(boxes):
    h = _find_header(boxes)
    unit_x = h["unit"]["cx"]
    qty_x = h["qty"]["cx"]
    name_x = h["name"]["cx"] if h["name"] and h["name"]["cx"] < unit_x - 20 else unit_x - 145
    price_x = h["price"]["cx"] if h["price"] else qty_x + 80
    amount_x = h["amount"]["cx"] if h["amount"] else price_x + 170
    header_y = max(x["cy"] for x in h.values() if x)

    rows = _cluster_rows(boxes, qty_x, price_x, header_y)
    if not rows:
        raise VisionTableError("没有找到任何「数量/单价」行，无法重建")

    out = []
    for row in rows:
        items = [b for b in boxes if abs(b["cy"] - row["cy"]) < 12]
        # 序号：名称左侧的纯数字
        no_c = [b for b in items if b["cx"] < name_x - 30 and re.fullmatch(r"\d{1,3}", b["text"].strip())]
        names = [b for b in items if name_x - 80 < b["cx"] < unit_x - 30]
        names = [b for b in names if not re.fullmatch(r"[\d.,]+", b["text"].strip())]
        units = [b for b in items if abs(b["cx"] - unit_x) < 32]
        qs = [b for b in items if abs(b["cx"] - qty_x) < 42]
        ps = [b for b in items if abs(b["cx"] - price_x) < 42]
        am = [b for b in items if b["cx"] > price_x + 50]

        name = _join(names)
        unit = _unit_of(_join(units))
        if not unit:
            for tok in UNIT_TOKENS:
                if name.endswith(tok):
                    unit = _unit_of(tok)
                    name = name[:-len(tok)].strip()
                    break
        qty = next((_num_fix(b["text"]) for b in sorted(qs, key=lambda b: abs(b["cx"] - qty_x))
                    if _num_fix(b["text"]) is not None), None)
        price = next((_num_fix(b["text"]) for b in sorted(ps, key=lambda b: abs(b["cx"] - price_x))
                      if _num_fix(b["text"]) is not None), None)

        amount = None
        note = ""
        used_amount_box = None
        # 金额列：取离 amount_x 最近的数字框；同一框里「金额 + 备注」拆开
        am_sorted = sorted(am, key=lambda b: abs(b["cx"] - amount_x))
        for b in am_sorted:
            m = re.match(r"^([\d,]+(?:\.\d+)?)\s*(.*)$", b["text"].strip())
            if m and amount is None:
                amount = float(m.group(1).replace(",", ""))
                note = m.group(2).strip()
                used_amount_box = b
                break
        tail = [b for b in sorted(am, key=lambda b: b["x"])
                if b is not used_amount_box and b["cx"] > amount_x + 70 and b["text"].strip()]
        if tail:
            note = (note + " " + _join(tail)).strip()

        # 规格：名称与单位之间的宽列（复杂表里的材质/长宽高）
        spec_boxes = [b for b in items if name_x + 40 < b["cx"] < unit_x - 35
                      and not re.fullmatch(r"[\d.,]+", b["text"].strip())]
        spec = _join(spec_boxes)

        used = names + units + qs + ps + am
        conf = round(statistics.mean([b["conf"] for b in used]), 2) if used else 0.0
        row = {
            "序号": (no_c[0]["text"].strip() if no_c else ""),
            "项目名称": name,
            "规格工艺": spec,
            "单位": unit,
            "数量": qty,
            "单价": price,
            "合价": amount,
            "备注": note,
            "_conf": conf,
            "_cy": round(row["cy"], 1),
        }
        # 列漂移修复：单位列被数量占位、单位被并进项目名（缩进表格常见）
        if re.fullmatch(r"[\d.,]+", str(row.get("单位") or "")):
            if row.get("数量") is None:
                row["数量"] = _num(row["单位"])
            for tok in UNIT_TOKENS:
                if (row.get("项目名称") or "").endswith(tok):
                    row["单位"] = _unit_of(tok)
                    row["项目名称"] = row["项目名称"][:-len(tok)].strip()
                    break
        row["_flags"] = review_flags(row)
        out.append(row)
    return out


def review_flags(row):
    flags = []
    if not row.get("项目名称"):
        flags.append("缺项目名")
    q, p, a = row.get("数量"), row.get("单价"), row.get("合价")
    if q is None or q <= 0:
        flags.append("缺数量")
    if p is None or p <= 0:
        flags.append("缺单价")
    if a is None or a <= 0:
        flags.append("缺合价")
    if q and p and a:
        tol = max(1.0, abs(a) * 0.02)
        if abs(q * p - a) > tol:
            flags.append("量价不符")
    if (row.get("_conf") or 0) < 0.3:
        flags.append("低置信")
    if not row.get("单位"):
        flags.append("缺单位")
    elif row["单位"] not in ("㎡", "m", "项", "根", "套", "个", "块",
                                               "条", "组", "樘", "处", "只", "张", "延米",
                                               "kg", "卷", "桶"):
        flags.append("单位异常")
    return flags


def perceive(image_path, lang="zh-Hans"):
    """返回 (rows, report)。rows 含 _conf/_flags，供上层决定是否降级。"""
    boxes = ocr_boxes(image_path, lang=lang)
    rows = reconstruct(boxes)
    accepted = [r for r in rows if not r["_flags"]]
    review = [r for r in rows if r["_flags"]]
    report = {
        "provider": "vision",
        "engine": "macos-vision",
        "boxes": len(boxes),
        "rows": len(rows),
        "accepted": len(accepted),
        "review": len(review),
        "arith_checked": len([r for r in rows if r.get("数量") and r.get("单价") and r.get("合价")]),
        "arith_bad": [{"row": r.get("序号"), "name": r.get("项目名称"),
                       "flags": r["_flags"]} for r in rows if "量价不符" in r["_flags"]],
        "flags": sorted({f for r in rows for f in r["_flags"]}),
        "review_ratio": round(len(review) / max(1, len(rows)), 3),
    }
    return rows, report


if __name__ == "__main__":
    import sys
    p = sys.argv[1]
    rows, report = perceive(p)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    for r in rows[:20]:
        print(r["序号"], r["项目名称"], r["单位"], r["数量"], r["单价"], r["合价"], r["_flags"])
