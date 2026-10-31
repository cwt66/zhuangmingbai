# -*- coding: utf-8 -*-
"""翡翠台 · 装修预算表（4 页照片人工转录）→ CSV

数据保真原则：
- 金额/数量按图直录，算术不符就让引擎报（R8 正是抓这个的），不凑小计
- 闭合反推仅用于修正「明显误读」（如 p1 敲墙线体 3.40），修正处在 p1 头注释声明
- 汇总行（装修造价/管理费/合计/优惠）如实入表，引擎会当未对齐行处理，无害
- 主材区疑漏 1-2 行（闭合差 4541），采用印刷小计 95231，明细行只供判定用
"""
import csv
import importlib.util
import os

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "feitai_budget.csv")

def load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, name + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m.ROWS

rows = []
for page in ["p1", "p2", "p3", "p4"]:
    rows += load(page)

# 算术自校验（数量×单价 vs 合价），只报告不修改
bad = []
for i, (zone, name, unit, qty, price, total, note) in enumerate(rows, 1):
    try:
        if qty and price:
            expect = float(qty) * float(price)
            if abs(expect - float(total)) > 0.01:
                bad.append((i, name, qty, price, total, round(expect, 2)))
    except (ValueError, TypeError):
        pass

with open(OUT, "w", newline="", encoding="utf-8-sig") as f:
    w = csv.writer(f)
    w.writerow(["序号", "项目名称", "规格工艺", "单位", "数量", "单价", "合价", "备注"])
    for i, (zone, name, unit, qty, price, total, note) in enumerate(rows, 1):
        w.writerow([i, name, "分区：" + zone, unit, qty, price, total, note])

print("写出", OUT, "明细", len(rows), "行")
print("行级算术不符（原样保留，交引擎 R8 报告）：", len(bad), "处")
for b in bad:
    print("  行%d %s 数量%s×单价%s=合价%s（推算 %s）" % b)

# 分区小计核对（按印刷小计）
total_rows = [r for r in rows if r[0] in ("装修造价", "工程总造价", "主材小计", "合计", "优惠")]
print("印刷锚点：总造价 76850.40 | 主材小计 95231 | 合计 176842.95 | 实收 160000")
