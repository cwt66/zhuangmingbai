# -*- coding: utf-8 -*-
"""上传守门：先判断「这是不是一份报价单」，再决定能不能审。

为什么要做
----------
对标 GitHub 上的 RenoPit（fthux/RenoPit）：它有 `classify_document()`，
用关键词 + 金额格式把文档分成 contract / quotation / irrelevant，再决定后续分支。
我们之前缺这一环——传一份装修合同进来，引擎会把它当报价单逐行「审」，
输出一堆看似合理、其实没有意义的结论。这是**会误导用户的失败模式**。

和 RenoPit 的差别（刻意设计）
----------------------------
- 同样是规则分类、不调用大模型；但我们**输出命中词与结构证据**，可逐条回原文核对；
- 置信度只表示「证据充分程度」，**不是概率**（我们没有校准过的概率模型）；
- 判定为合同/无关时**明确拒绝审计**，而不是继续出报告。

用法：
    from doc_gate import classify, gate
    info = classify(rows, filename="xxx.csv")
"""
import re

# 报价单的结构列（我们的标准 schema）。结构证据比关键词可靠得多。
STRUCT_COLS = ("序号", "项目名称", "单位", "数量", "单价", "合价", "备注", "规格工艺")
NUMERIC_COLS = ("数量", "单价", "合价")

QUOTE_WORDS = (
    "报价", "预算", "工程量", "单价", "合价", "小计", "合计", "工序", "施工",
    "材料费", "人工费", "主材", "辅材", "面积", "平米", "平方米", "管理费",
    "工程量", "项目", "规格", "工艺", "单位", "数量",
)
CONTRACT_WORDS = (
    "甲方", "乙方", "合同", "协议", "违约责任", "争议解决", "签字盖章", "签章",
    "工期", "开工日期", "竣工日期", "付款方式", "保修条款", "不可抗力",
    "本合同", "双方约定", "法律效力", "一式两份",
)
IRRELEVANT_WORDS = ("菜谱", "菜单", "简历", "成绩单", "课程表", "股票", "病历", "处方")

_AMOUNT_RE = re.compile(r"^\s*[-+]?[\d,]+(?:\.\d+)?\s*$")


def _text_of(rows):
    parts = []
    for r in rows or []:
        for k in ("name", "spec", "note", "unit", "项目名称", "规格工艺", "备注", "单位"):
            v = r.get(k) if isinstance(r, dict) else None
            if v:
                parts.append(str(v))
    return " ".join(parts)


def _count_hits(text, words):
    hits = []
    for w in words:
        n = text.count(w)
        if n:
            hits.append({"word": w, "count": n})
    return sorted(hits, key=lambda x: -x["count"])


def _confidence(n):
    """与 RenoPit 相同的分档（3/5/10），但只作为「证据充分度」，不是概率。"""
    if n >= 10:
        return 0.90
    if n >= 5:
        return 0.75
    if n >= 3:
        return 0.60
    return 0.45


def classify(rows, filename=""):
    """对已解析的行项做类型判定。rows 是 pipeline 里的标准行字典列表。"""
    text = _text_of(rows)
    lower = (filename or "").lower()
    q_hits = _count_hits(text, QUOTE_WORDS)
    c_hits = _count_hits(text, CONTRACT_WORDS)
    i_hits = _count_hits(text, IRRELEVANT_WORDS)
    nq = sum(h["count"] for h in q_hits)
    nc = sum(h["count"] for h in c_hits)

    # 结构证据：标准列覆盖数 + 可核算金额行占比
    keys = set()
    for r in (rows or [])[:1]:
        keys = set(r.keys())
    struct = [c for c in STRUCT_COLS if c in keys]
    # pipeline 内部行是 {"no","name","spec","unit","qty","price","amount","note"}
    if not struct and keys:
        alias = {"no": "序号", "name": "项目名称", "spec": "规格工艺", "unit": "单位",
                 "qty": "数量", "price": "单价", "amount": "合价", "note": "备注"}
        struct = [alias[k] for k in keys if k in alias]
    numeric_rows = 0
    for r in rows or []:
        a = r.get("amount", r.get("合价"))
        if isinstance(a, (int, float)) and a > 0:
            numeric_rows += 1
        elif isinstance(a, str) and _AMOUNT_RE.match(a) and float(a.replace(",", "")) > 0:
            numeric_rows += 1
    amount_ratio = numeric_rows / max(1, len(rows or []))
    has_amount = amount_ratio >= 0.3

    reasons = []
    if len(struct) >= 5:
        reasons.append("具备标准报价单列结构（%d/%d 列）" % (len(struct), len(STRUCT_COLS)))
    if has_amount:
        reasons.append("有金额列且 %.0f%% 行有正数金额" % (amount_ratio * 100))
    if nq:
        reasons.append("报价单关键词命中 %d 次（%s）" %
                       (nq, "、".join(h["word"] for h in q_hits[:5])))
    if nc:
        reasons.append("合同条款关键词命中 %d 次（%s）" %
                       (nc, "、".join(h["word"] for h in c_hits[:5])))

    conf = _confidence(max(nq, nc))
    # 文件名也是证据，但只给小幅加权，不能靠改名骗过守门
    if any(k in lower for k in ("baojia", "quote", "报价", "预算", "清单")):
        conf = min(0.95, conf + 0.05)
        reasons.append("文件名含报价/预算/清单线索")
    if has_amount and nq >= 3:
        conf = min(0.95, conf + 0.10)

    if i_hits and not has_amount:
        doc_type = "irrelevant"
        reasons.append("命中无关内容词：%s" % "、".join(h["word"] for h in i_hits[:3]))
    elif nc >= 3 and nc > nq:
        doc_type = "contract"
        reasons.append("合同词多于报价词 → 判定合同，不应按报价单审")
    elif len(struct) >= 5 and has_amount and nc < 3:
        # 结构证据强于关键词：真实报价单（如篱笆单）项目名里可能一个「报价/预算」都没有，
        # 但只要 8 列齐全 + 金额列普遍有数，就是量价清单。踩过这个误判。
        doc_type = "quotation"
        conf = max(conf, 0.85)
        reasons.append("标准列结构 + 金额列普遍有数 → 判定报价单（结构证据优先）")
    elif has_amount and nq >= 3:
        doc_type = "quotation_nonstandard"
        reasons.append("有金额但列结构不完整 → 非标准报价单，建议先校对再审")
    elif not rows:
        doc_type = "empty"
        conf = 0.0
    else:
        doc_type = "unknown"
        conf = min(conf, 0.55)
        reasons.append("证据不足，既不判报价单也不判合同")

    zh_chars = len(re.findall(r"[\u4e00-\u9fa5]", text))
    lang = "zh" if zh_chars > len(text) * 0.3 else ("mixed" if zh_chars else "en")

    return {
        "doc_type": doc_type,
        "confidence": round(conf, 3),
        "language": lang,
        "quote_hits": q_hits[:8],
        "contract_hits": c_hits[:8],
        "structure_cols": sorted(struct),
        "amount_ratio": round(amount_ratio, 3),
        "rows": len(rows or []),
        "filename": filename,
        "reasons": reasons,
        "note": "置信度表示证据充分程度，不是概率；判定可由命中词与列结构逐条复核。",
    }


def gate(rows, filename="", strict=False):
    """返回 (ok, info)。ok=False 时上层必须拒绝审计并展示 info['message']。"""
    info = classify(rows, filename)
    t = info["doc_type"]
    labels = {
        "quotation": "报价单",
        "quotation_nonstandard": "非标准报价单",
        "contract": "装修合同",
        "irrelevant": "无关文档",
        "empty": "空文件",
        "unknown": "无法判定",
    }
    info["label"] = labels.get(t, t)
    info["ok"] = True
    info["message"] = ""
    if t in ("contract", "irrelevant"):
        info["ok"] = False
        info["message"] = ("《%s》看起来是%s，不是报价单。本系统只做报价单的计费核对，"
                           "不审合同条款；请上传带「项目/数量/单价/合价」的报价表。"
                           % (filename or "该文件", info["label"]))
    elif t == "empty":
        info["ok"] = False
        info["message"] = "文件里没有可解析的行项。"
    elif t in ("unknown", "quotation_nonstandard") and strict:
        info["ok"] = False
        info["message"] = ("《%s》证据不足以确认是报价单（%s）。"
                           "请先在校对台确认列结构，或关闭严格模式重试。"
                           % (filename or "该文件", "；".join(info["reasons"][:2])))
    return info["ok"], info
