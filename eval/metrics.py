# -*- coding: utf-8 -*-
"""校准审计指标（v2 组件三）：ECE / 可靠性表 / risk-coverage / bootstrap 置信区间

用法（W4 拿到标注集后）：
    from eval.metrics import ece, reliability, risk_coverage, bootstrap_ece
    ece_val = ece(y_true, y_score, n_bins=10)
    lo, hi = bootstrap_ece(y_true, y_score)
    # 报告写法：ECE = 0.0x（95% CI 0.0y–0.0z）

口径说明：
- y_true: 0/1，人工标注判定该结论是否正确
- y_score: 引擎输出的置信度 c∈[0,1]
- ECE 用等频分箱（每箱样本数相近），比等宽分箱在小样本下更稳
"""
import random


def _equal_freq_bins(scores, n_bins):
    idx = sorted(range(len(scores)), key=lambda i: scores[i])
    out = []
    for b in range(n_bins):
        lo = b * len(idx) // n_bins
        hi = (b + 1) * len(idx) // n_bins
        if hi > lo:
            out.append(idx[lo:hi])
    return out


def ece(y_true, y_score, n_bins=10):
    """Expected Calibration Error（等频分箱）"""
    assert len(y_true) == len(y_score) and y_true
    total = len(y_true)
    val = 0.0
    for idx in _equal_freq_bins(y_score, n_bins):
        conf = sum(y_score[i] for i in idx) / len(idx)
        acc = sum(y_true[i] for i in idx) / len(idx)
        val += len(idx) / total * abs(acc - conf)
    return round(val, 4)


def reliability(y_true, y_score, n_bins=10):
    """可靠性表：每箱的样本数/平均置信度/实际正确率"""
    rows = []
    for i, idx in enumerate(_equal_freq_bins(y_score, n_bins)):
        rows.append({
            "bin": i,
            "n": len(idx),
            "confidence": round(sum(y_score[j] for j in idx) / len(idx), 4),
            "accuracy": round(sum(y_true[j] for j in idx) / len(idx), 4),
        })
    return rows


def risk_coverage(y_true, y_score, steps=20):
    """按置信度降序做选择性预测：覆盖率 vs 错误率"""
    order = sorted(range(len(y_true)), key=lambda i: -y_score[i])
    out = []
    for k in range(1, steps + 1):
        n = max(1, int(len(order) * k / steps))
        sel = order[:n]
        out.append({
            "coverage": round(n / len(order), 4),
            "risk": round(1 - sum(y_true[i] for i in sel) / n, 4),
        })
    return out


def risk_at_coverage(y_true, y_score, target=0.80):
    """覆盖率 target 时的错误率（用于写「覆盖率 80% 时错误率 ≤5%」）"""
    order = sorted(range(len(y_true)), key=lambda i: -y_score[i])
    n = max(1, int(len(order) * target))
    sel = order[:n]
    return round(1 - sum(y_true[i] for i in sel) / n, 4)


def bootstrap_ece(y_true, y_score, n_bins=10, B=1000, seed=2026):
    """ECE 的 95% bootstrap 置信区间——1000 条标注下噪声约 ±0.01–0.02，必须报区间"""
    random.seed(seed)
    n = len(y_true)
    vals = []
    for _ in range(B):
        idx = [random.randrange(n) for _ in range(n)]
        vals.append(ece([y_true[i] for i in idx], [y_score[i] for i in idx], n_bins))
    vals.sort()
    return round(vals[int(0.025 * B)], 4), round(vals[int(0.975 * B)], 4)


if __name__ == "__main__":
    # 自检：校准良好 vs 过度自信 两组合成数据
    random.seed(7)
    n = 800
    scores = [random.uniform(0.55, 0.99) for _ in range(n)]
    good = [1 if random.random() < s else 0 for s in scores]
    bad = [1 if random.random() < s * 0.82 else 0 for s in scores]
    print("校准良好组：ECE =", ece(good, scores), " 95%CI =", bootstrap_ece(good, scores, B=300))
    print("过度自信组：ECE =", ece(bad, scores), " 95%CI =", bootstrap_ece(bad, scores, B=300))
    print("覆盖率80%时错误率：", risk_at_coverage(good, scores, 0.8), "|", risk_at_coverage(bad, scores, 0.8))
    print("可靠性表（前3箱）：", reliability(good, scores)[:3])
