# -*- coding: utf-8 -*-
"""条款 → 义务 → 验收证据。

把报价单里「按实结算 / 暂定 / 另计 / 多退少补 / 自购」这类开口词，
翻译成**签约前必须书面确认的问题**和**验收时必须留下的凭证**。

为什么这是审计的一部分而不是文案润色：
漏项是「少写了工序」，开口条款是「写了但没说清多少钱」——后者金额上不封顶，
对用户的实际风险往往更大。本项目红线是不判「贵不贵」，但**判「说没说清」是合规的**。

边界：不构成法律意见，不推断对方动机，只把原文的词翻译成行动项。
"""
import json
import os

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
_CACHE = {"loaded": False, "items": [], "meta": {}}


def load():
    if not _CACHE["loaded"]:
        p = os.path.join(DATA, "obligations.json")
        if os.path.isfile(p):
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            _CACHE["items"] = d.get("obligations") or []
            _CACHE["meta"] = d.get("meta") or {}
        _CACHE["loaded"] = True
    return _CACHE


def _text(row):
    return " ".join([row.get("name") or "", row.get("spec") or "", row.get("note") or ""])


def extract(quote):
    """扫描一份单据，产出「签约前确认清单」。

    聚合口径：同一义务只出一条，行号与金额全部列出——否则一份单能刷出几十条重复项，
    用户会直接不看了（和 R4 口径风险聚合同一个道理）。
    """
    book = load()["items"]
    found = {}
    for row in quote.get("rows") or []:
        text = _text(row)
        for ob in book:
            hit = next((p for p in ob["match"] if p in text), None)
            if not hit:
                continue
            item = found.setdefault(ob["id"], {
                "id": ob["id"], "title": ob["title"], "severity": ob.get("severity", "medium"),
                "exposure": ob.get("exposure", "bounded_if_priced"),
                "confirm": ob["confirm"], "evidence": ob["evidence"], "note": ob.get("note", ""),
                "patterns": [], "lines": [], "amount": 0.0, "samples": [],
            })
            if hit not in item["patterns"]:
                item["patterns"].append(hit)
            item["lines"].append(row["no"])
            item["amount"] = round(item["amount"] + (row["amount"] or 0), 2)
            if len(item["samples"]) < 3:
                item["samples"].append("%s（%s）" % (row["name"][:26], hit))

    order = {"high": 0, "medium": 1, "low": 2}
    out = sorted(found.values(),
                 key=lambda x: (order.get(x["severity"], 1), -x["amount"]))
    for it in out:
        it["lines"] = list(dict.fromkeys(it["lines"]))
    return out


def summary(obligations):
    """一句话概括，给报告抬头用。"""
    if not obligations:
        return "未发现开口条款（按实结算/暂定/另计/多退少补/自购）——这是好事，但仍建议把范围写进合同。"
    high = [o for o in obligations if o["severity"] == "high"]
    open_ended = [o for o in obligations if o["exposure"] == "open"]
    parts = ["共 %d 类开口条款" % len(obligations)]
    if high:
        parts.append("其中 %d 类属高风险" % len(high))
    if open_ended:
        parts.append("%d 类金额上不封顶" % len(open_ended))
    return "，".join(parts) + "。签约前逐条问清楚并写进合同附件。"
