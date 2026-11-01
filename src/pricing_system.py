# -*- coding: utf-8 -*-
"""计价体系识别：先认出「这是哪种报价单」，再决定怎么读它。

## 为什么需要

同一套解析与审计逻辑套到不同计价体系上会失真。你给的真实单据里至少有四种：

| 体系 | 长什么样 | 典型特征 |
|---|---|---|
| qty_price 工程量清单 | 项目/单位/数量/单价/合价 齐全 | 多数行可算、单位多样 |
| area_rate 按面积计价 | 以 ㎡ 为主，单价接近统一 | ㎡ 占比高、行数少、单价集中 |
| package 套餐/人工费 | 大量「项」，不写工程量 | 「项」占比高、可算行少 |
| lump_sum 汇总列表 | 只有金额，没有量价 | 多数行无量无价 |

识别的用途有两个：
1. **审计前提示口径风险**：按面积计价单里逐行比对单价没有意义，应该比总面积与总价；
2. **解析策略选择**：工程量清单要分列，汇总列表只需要金额列。

## 纪律

- 只输出**倾向 + 依据 + 置信度**，不输出「一定是」；
- 特征全部可复述（占比、行数、单位分布），可人工复核；
- 识别错误不影响审计判定，只影响报告里的一句话与提示——不允许它成为漏报的理由。
"""

QTY_UNITS = {"m2", "㎡", "m", "米", "延米", "kg", "t", "套", "个", "根", "樘", "块", "张"}
AREA_UNITS = {"m2", "㎡", "平米", "平方米"}


def _norm_unit(u):
    return (u or "").strip().lower()


def features(quote):
    rows = quote.get("rows") or []
    n = len(rows) or 1
    # 「项」是打包标记，数量恒为 1、单价即整包价 —— 它不算「量价可核」。
    # 套餐类单据因此不会被误判成工程量清单（这是真实误判，靠合成样本测出来的）。
    quantified = [r for r in rows
                  if r["qty"] > 0 and r["price"] > 0 and r["amount"] > 0
                  and _norm_unit(r["unit"]) != "项"]
    item_unit = [r for r in rows if _norm_unit(r["unit"]) == "项"]
    area = [r for r in rows if _norm_unit(r["unit"]) in {u.lower() for u in AREA_UNITS}]
    units = {}
    for r in rows:
        u = _norm_unit(r["unit"]) or "（无单位）"
        units[u] = units.get(u, 0) + 1
    # 按面积计价的一个强信号：㎡ 行的单价集中（同一单价反复出现）
    area_prices = [round(r["price"], 2) for r in area if r["price"] > 0]
    area_price_mode = 0
    if area_prices:
        from collections import Counter
        area_price_mode = Counter(area_prices).most_common(1)[0][1]
    return {
        "rows": len(rows),
        "quantified": len(quantified),
        "quantified_ratio": round(len(quantified) / n, 3),
        "item_ratio": round(len(item_unit) / n, 3),
        "area_ratio": round(len(area) / n, 3),
        "area_price_mode_ratio": round(area_price_mode / n, 3),
        "unit_kinds": len([k for k in units if k != "（无单位）"]),
        "units": dict(sorted(units.items(), key=lambda kv: -kv[1])[:5]),
    }


def classify(quote):
    """返回 {system, label, confidence, evidence, implication}。"""
    f = features(quote)
    scores = {}

    # 工程量清单：可算行多 + 单位多样
    scores["qty_price"] = 0.65 * f["quantified_ratio"] + 0.35 * min(1.0, f["unit_kinds"] / 5.0)
    # 「项」占比高时，单位多样性不该再给工程量清单加分
    scores["qty_price"] *= (1 - 0.5 * f["item_ratio"])
    # 按面积计价：㎡ 占比高 + 单价集中
    scores["area_rate"] = 0.6 * f["area_ratio"] + 0.4 * f["area_price_mode_ratio"]
    # 套餐/人工费：项占比高 或 行数少且可算行少
    scores["package"] = 0.7 * f["item_ratio"] + 0.3 * (1 - f["quantified_ratio"]) * (
        1.0 if f["rows"] <= 60 else 0.5)
    # 汇总列表：可算行少 + 项也不多
    scores["lump_sum"] = 0.8 * (1 - f["quantified_ratio"]) * (1 - f["item_ratio"]) + \
        0.2 * (1.0 if f["rows"] <= 30 else 0.4)

    system = max(scores, key=scores.get)
    top = scores[system]
    total = sum(scores.values()) or 1
    conf = round(min(0.95, 0.45 + 0.55 * (top / total)), 3)

    labels = {"qty_price": "工程量清单（量价齐全）", "area_rate": "按面积计价",
              "package": "套餐/人工费表", "lump_sum": "汇总列表（无量价）"}
    implications = {
        "qty_price": "逐行比量价比对有效；漏项按工序模板比对。",
        "area_rate": "逐行比单价意义有限，应重点核对总面积与总价是否自洽。",
        "package": "工程量不可核，重点看「项」里含什么、不含什么（另计条款）。",
        "lump_sum": "无法逐行核量，只能做文档级对账与口径审计。",
    }
    evidence = [
        "共 %d 行，其中 %d 行量价齐全（%.0f%%）" % (
            f["rows"], f["quantified"], f["quantified_ratio"] * 100),
        "「项」计价 %d 行（%.0f%%）｜㎡ 计价 %d 行（%.0f%%）" % (
            round(f["item_ratio"] * f["rows"]), f["item_ratio"] * 100,
            round(f["area_ratio"] * f["rows"]), f["area_ratio"] * 100),
        "出现单位 %d 种：%s" % (f["unit_kinds"],
                            "、".join("%s×%d" % (k, v) for k, v in f["units"].items())),
    ]
    return {"system": system, "label": labels[system], "confidence": conf,
            "evidence": evidence, "implication": implications[system],
            "features": f, "scores": {k: round(v, 3) for k, v in scores.items()}}


def template_hint(system):
    """体系 → 审计侧重提示（写进报告，避免用户误读结论）。"""
    return {
        "qty_price": "本单量价齐全，漏项与重复计价结论可直接采信。",
        "area_rate": "本单按面积计价，逐行单价对比仅供参考；请重点看总面积/总价对账。",
        "package": "本单为套餐/人工费口径，工程量不可核，请重点看「另计/不含」条款清单。",
        "lump_sum": "本单为汇总口径，只能做文档级对账与口径审计，不能核量。",
    }.get(system, "")
