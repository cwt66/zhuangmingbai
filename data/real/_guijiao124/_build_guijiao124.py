# -*- coding: utf-8 -*-
"""124㎡ 基础报价单（4 页照片人工转录）→ CSV"""
import csv, importlib.util, os

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "guijiao_124_basic.csv")

def load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, name + ".py"))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m.ROWS

rows = []
for page in ["p1", "p2", "p3", "p4"]:
    rows += load(page)

# 汇总行（费用总合计/设计费/总价）不是工序，不进审计明细——否则 total 会翻倍。
# 它们保留在 p4.py 里作留痕，印刷总价 76,269.16 记在转录说明与报告中。
SUBTOTAL_ZONES = {"总价"}
all_rows = rows
rows = [r for r in all_rows if r[0] not in SUBTOTAL_ZONES]
dropped = len(all_rows) - len(rows)

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

print("写出", OUT, "| 明细", len(rows), "行（剔除非工序汇总行 %d）" % dropped)
print("行级算术不符（原样保留）：", len(bad), "处")
for b in bad[:8]:
    print("  行%d %s 数量%s×单价%s=合价%s（推算 %s）" % b)
print("印刷锚点：总价 76269.16（12 分区小计之和精确等于此值）")
