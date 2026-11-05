# -*- coding: utf-8 -*-
"""确定性增项预测：把「漏项 + 开口条款」翻译成签约前能预防的清单。

对标与差异
----------
RenoPit 在报价单分析后追加一次 LLM 调用 `predict_extra_items()`，输出
「预计实际总额、置信区间、发生概率、金额范围、触发阶段、预防方式」。

我们做同一件事，但换了三条口径：

1. **不调用大模型**——所有条目都能回指到漏项表/条款表的具体行号与依据；
2. **不输出概率**——我们没有校准过的概率模型，给「70% 会发生」就是编数；
   输出 `probability: null` 并在报告里写明原因；
3. **金额区间来自参照价库**（真实单据的 min/max），不是模型猜的区间；
   没有金额样本的工序项就留空，只给「是否施工 + 留什么验收记录」。

另一个容易被忽略的坑：开口条款（「另计/按实结算」）涉及的金额**已经列在报价里**，
不能和漏项补项相加，否则会把同一笔钱算两遍。所以这里用
`exposure_amount` 单列，并明确标注「已列金额，不重复计入」。

用法：
    from addon_forecast import forecast
    result = forecast(res, reference_prices_path="data/reference_prices.json")
"""
import json
import os

# 触发阶段：只按工序语义归类，不推断「什么时候会加钱」（那需要真实结算数据）
STAGE_RULES = (
    (("保护", "拆除", "拆旧", "垃圾", "清运", "铲", "找平"), "开工 / 基层"),
    (("防水", "闭水", "水电", "管线", "打压", "回填", "挂网"), "隐蔽工程"),
    (("砖", "铺贴", "填缝", "美缝", "吊顶", "乳胶漆", "腻子", "墙面", "顶面"), "瓦木油"),
    (("门", "洁具", "灯具", "踢脚线", "门槛", "开关", "五金", "柜"), "安装收尾"),
    (("管理费", "设计费", "税金", "总价", "优惠"), "结算"),
)


def stage_of(name):
    for words, stage in STAGE_RULES:
        if any(w in (name or "") for w in words):
            return stage
    return "施工全过程"


def _load_book(path):
    if not path or not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return (json.load(f).get("items") or {})
    except (ValueError, OSError):
        return {}


def _range_for(cid, ref_amount, book):
    """金额区间只取真实参照值；无样本就返回单点或空。"""
    it = book.get(cid)
    if it and it.get("amount_range"):
        lo, hi = it["amount_range"]
        return [round(float(lo), 2), round(float(hi), 2)], (
            "参照价库：%d 份真实单据的实价区间（中位 %.2f）"
            % (it.get("n_docs", 0), it.get("amount_median", 0)))
    if ref_amount:
        return [round(float(ref_amount), 2), round(float(ref_amount), 2)], (
            "同批/参照单点估计；样本不足，区间不可估计")
    return None, "无金额样本：该工序在真实单据里从不单列计价，只能定性提示"


def forecast(res, reference_prices_path=None):
    """res 是 pipeline.audit() 的单据结果字典。"""
    book = _load_book(reference_prices_path)
    items = []

    for m in res.get("missing") or []:
        cid = m.get("cid") or ""
        amt = m.get("ref_amount")
        lo_hi, basis = _range_for(cid, amt, book)
        ev = m.get("evidence") or {}
        items.append({
            "id": "af_missing_" + cid,
            "kind": "missing_priced" if amt else "missing_process",
            "title": "漏项：%s" % m.get("concept", cid),
            "concept": m.get("concept", cid),
            "tier": m.get("tier"),
            "severity": "high" if amt else "medium",
            "trigger_stage": stage_of(m.get("concept", "")),
            "amount_range": lo_hi,
            "amount_basis": basis,
            "reference": [x.get("quote") for x in (m.get("ref_source") or [])],
            "prevention": (
                "签约前要求把该项写进报价单，注明单价与工程量；"
                "若已含在其他项，请报价方书面注明是哪一项。"
                if amt else
                "签约前确认是否施工、由谁负责，并约定过程记录（照片/签字）作为验收依据；"
                "这类工序通常不单列收费，问单价问不出结果。"
            ),
            "evidence": {
                "basis": ev.get("basis", "标准工序模板集合差"),
                "formula": ev.get("formula", "T \\ S"),
            },
            "probability": None,
        })

    for o in res.get("obligations") or []:
        amount = o.get("amount") or 0
        items.append({
            "id": "af_clause_" + o.get("id", "x"),
            "kind": "open_clause",
            "title": "开口：%s" % o.get("title", "模糊条款"),
            "concept": o.get("title", ""),
            "tier": "review",
            "severity": o.get("severity", "medium"),
            "trigger_stage": "签约 / 变更签证",
            "amount_range": None,
            "amount_basis": "金额无封顶，不可估计（条款本身留了开口）",
            "exposure_amount": round(float(amount), 2) if amount else 0,
            "exposure_note": "已列在报价里的金额，仅表示受影响规模，不重复计入预测合计",
            "lines": (o.get("lines") or [])[:12],
            "prevention": o.get("confirm", "签约前书面确认触发条件与计价方式"),
            "evidence": {
                "basis": "报价单风险表述清单",
                "formula": "命中 %s" % "、".join(o.get("patterns") or []),
                "acceptance": o.get("evidence", ""),
            },
            "probability": None,
        })

    priced = [x for x in items if x.get("amount_range")]
    low = round(sum(x["amount_range"][0] for x in priced), 2)
    high = round(sum(x["amount_range"][1] for x in priced), 2)
    open_items = [x for x in items if x["kind"] == "open_clause"]

    return {
        "items": items,
        "summary": {
            "total": len(items),
            "priced": len(priced),
            "open_clause": len(open_items),
            "process_only": len([x for x in items if x["kind"] == "missing_process"]),
            "amount_low": low,
            "amount_high": high,
            "exposure_listed": round(sum(x.get("exposure_amount") or 0 for x in open_items), 2),
        },
        "method": {
            "probability": None,
            "note": "不输出发生概率：没有校准过的概率模型，给百分比等于编数。",
            "amount_note": "区间来自参照价库真实单据的实价 min/max；无样本的工序项留空，只做定性提示。",
            "no_double_count": "开口条款的已列金额单独记为 exposure_listed，不并入 amount_low/high。",
            "not_a_quote": "这是「需要书面确认的风险清单」，不是对最终结算价的承诺或预测。",
        },
    }
