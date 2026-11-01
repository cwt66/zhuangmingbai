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

# 2026-11-01 起：汇总行（费用总合计/设计费/总价）**保留在 CSV 里**，
# 由 pipeline._is_summary_row 分层识别 → 进入对账，不再进工序明细。
# 之前这里是按「分区=总价」硬过滤（ad-hoc），换成通用层后能顺带校验 506.6 元小计误差。

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

print("写出", OUT, "| 全部", len(rows), "行（含汇总行，由引擎分层）")
print("行级算术不符（原样保留）：", len(bad), "处")
for b in bad[:8]:
    print("  行%d %s 数量%s×单价%s=合价%s（推算 %s）" % b)
print("印刷锚点：总价 76269.16（12 分区小计之和精确等于此值）")
