# -*- coding: utf-8 -*-
"""构造一份「变更后」报价，用来演示两版对比（**模拟数据，不是真实变更单**）。

为什么需要它
------------
版本对比要回答「补漏之后钱多了没有」，但我们手上没有同一项目的两版真实报价
（用户侧仍在收集「报价 + 结算」成对单据）。没有演示数据，这个能力就只是代码。

做法：拿一份**真实报价**（翡翠台 123 行），按审计结论做 4 处典型变更，
模拟一次真实的「业主拿报告去谈 → 装修公司改报价」：

1. 补 2 项漏项（成品保护-门窗、闭水试验）—— 对应审计里的 missing；
2. 第 15 行墙面乳胶漆单价 18 → 22（材料涨价）；
3. 第 90 行垃圾清运工程量 95 → 105（实测面积变大）；
4. 删掉第 57 行「地面找平二排」—— 审计判为与第 38 行重复计价。

**口径**：这是演示数据，材料里必须写「模拟变更」，不得当成真实结算单；
金额为构造值，只用于验证对比逻辑能否把差额对上账。

用法：
    python3 scripts/make_change_demo.py --out /tmp/feitai_v2.csv
"""
import argparse
import csv
import io
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_SRC = os.path.join(ROOT, "data", "real", "feitai_budget.csv")

# (行号, 字段, 新值)；行号是原表「序号」列
EDITS = [("15", "price", 22.0), ("90", "qty", 105.0)]
DELETE = {"57"}
# (项目名称, 规格工艺, 单位, 数量, 单价)
ADDS = [
    ("成品保护（门窗）", "装修前门窗成品保护", "项", 1, 200),
    ("闭水试验", "卫生间蓄水 24 小时", "项", 1, 150),
]

CHANGES = [
    "补漏项：成品保护（门窗）1 项 × 200 = 200",
    "补漏项：闭水试验 1 项 × 150 = 150",
    "调价：第 15 行 墙面乳胶漆 18 → 22 元/㎡（83 ㎡，+332）",
    "增量：第 90 行 垃圾清运 95 → 105 ㎡（8 元/㎡，+80）",
    "减项：删除第 57 行 地面找平二排（与第 38 行重复计价，−98）",
]


def build(src=DEFAULT_SRC):
    with open(src, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    fields = list(rows[0].keys())
    out = []
    for r in rows:
        no = (r.get("序号") or "").strip()
        if no in DELETE:
            continue
        for target, field, val in EDITS:
            if no == target:
                col = {"price": "单价", "qty": "数量"}[field]
                r[col] = str(val)
                try:
                    r["合价"] = "%.2f" % (float(r["数量"]) * float(r["单价"]))
                except (TypeError, ValueError, KeyError):
                    pass
        out.append(r)
    for name, spec, unit, qty, price in ADDS:
        out.append({"序号": str(len(out) + 1), "项目名称": name, "规格工艺": spec,
                    "单位": unit, "数量": str(qty), "单价": str(price),
                    "合价": "%.2f" % (qty * price), "备注": "变更补报"})
    return fields, out


def write(path, fields, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def main():
    ap = argparse.ArgumentParser(description="构造演示用的「变更后」报价")
    ap.add_argument("--src", default=DEFAULT_SRC)
    ap.add_argument("--out", default="/tmp/feitai_v2_change.csv")
    a = ap.parse_args()
    fields, rows = build(a.src)
    write(a.out, fields, rows)
    print("源：%s（%d 行）" % (a.src, len(rows) - len(ADDS)))
    for c in CHANGES:
        print("  ·", c)
    print("输出：%s（%d 行，模拟变更，非真实结算单）" % (a.out, len(rows)))


if __name__ == "__main__":
    main()
