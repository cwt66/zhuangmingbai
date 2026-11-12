# -*- coding: utf-8 -*-
"""工序对齐的混合检索（候选通道）。

## 为什么要单独一个模块

`pipeline.Ontology.align()` 现在的顺序是：正式名 → 别名 → 编辑距离（difflib）
→ 包含匹配。它能跑，但模糊那一档用的是 **SequenceMatcher**，本质是「最长公共子序列」，
对中文短词不稳：`漏电保护 / 电梯保护` 相似度 0.68，`定制橱柜 / 柜体定制` 只有 0.77——
前者是错的，后者该命中却没命中。

这个模块只做一件事：**给现有链路补一条候选通道**，且**只补未命中的**。
已经命中（exact/alias）的一律不动——那部分是全项目判定、冻结指纹与评测基线赖以成立的前提。

## 打分（不用 embedding，避免引入模型依赖）

    score = 0.65 * 字符二元组 Dice + 0.35 * 最长公共子串占比

- Dice 对「同字不同序」敏感（柜体定制 vs 定制橱柜 仍有重叠）
- LCS 对「少一两个字」敏感（开关插座 vs 开关插座安装）

两个加起来比单用 SequenceMatcher 稳，且完全确定、无外部依赖。
易混词对（地砖↔地漏、找平↔找坡）仍然跳过——这是红队撞出来的血账，不能退。
"""
import re

try:
    from .pipeline import norm, _confusable, PAREN
except ImportError:  # pragma: no cover
    from pipeline import norm, _confusable, PAREN

MIN_SCORE = 0.55          # 低于这个分不给候选：宁可漏，不可张冠李戴
MIN_ALIAS_LEN = 2


def _bigrams(s):
    if len(s) < 2:
        return {s} if s else set()
    return {s[i:i + 2] for i in range(len(s) - 1)}


def _dice(a, b):
    A, B = _bigrams(a), _bigrams(b)
    if not A or not B:
        return 0.0
    return 2 * len(A & B) / (len(A) + len(B))


def _lcs_ratio(a, b):
    """最长公共子串长度 / 较长串长度。短词场景比 LCS 子序列更贴近「同一个词」。"""
    if not a or not b:
        return 0.0
    la, lb = len(a), len(b)
    prev = [0] * (lb + 1)
    best = 0
    for i in range(1, la + 1):
        cur = [0] * (lb + 1)
        ai = a[i - 1]
        for j in range(1, lb + 1):
            if ai == b[j - 1]:
                cur[j] = prev[j - 1] + 1
                if cur[j] > best:
                    best = cur[j]
        prev = cur
    return best / max(la, lb)


def score(name, alias):
    a, b = norm(name), norm(alias)
    if not a or not b:
        return 0.0
    return 0.65 * _dice(a, b) + 0.35 * _lcs_ratio(a, b)


def candidates(onto, name, topk=3, min_score=MIN_SCORE):
    """返回 [(cid, concept, score, method)]，按分数降序；不做最终决定。"""
    n = norm(name)
    stripped = norm(PAREN.sub("", name or ""))
    if not n:
        return []
    out = []
    for probe in {n, stripped} - {""}:
        for alias, cid in onto.alias.items():
            if len(alias) < MIN_ALIAS_LEN:
                continue
            if _confusable(probe, alias):
                continue
            s = score(probe, alias)
            if s >= min_score:
                out.append((cid, onto.by_id[cid]["n"], round(s, 3),
                            "hybrid_stripped" if probe is stripped else "hybrid"))
    # 同一 cid 取最高分
    best = {}
    for cid, concept, s, how in out:
        if cid not in best or s > best[cid][2]:
            best[cid] = (cid, concept, s, how)
    return sorted(best.values(), key=lambda x: -x[2])[:topk]


def align_with_fallback(onto, name):
    """先走原有链路；只有未命中时，才用混合检索给候选。

    返回 (cid, conf, how)。how 以 `hybrid` 开头表示「候选，需人工确认」，
    与 exact/alias（可直接采信）区分开——评测里不能混着算。
    """
    cid, conf, how = onto.align(name)
    if how != "unmatched":
        return cid, conf, how
    cands = candidates(onto, name, topk=1)
    if not cands:
        return None, 0.0, "unmatched"
    c, _concept, s, _m = cands[0]
    # 候选档的置信度压到 0.6 以下：它只能进「待确认」，不能进自动结论
    return c, round(min(0.59, s * 0.6), 3), "hybrid"
