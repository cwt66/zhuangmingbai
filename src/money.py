# -*- coding: utf-8 -*-
"""金额的十进制工具：审计里的「分」不能用浮点糊过去。

为什么单独一个模块
------------------
`0.1 + 0.2 != 0.3` 在报价单里就是 1 分钱的差额。判断层目前对 R8 保留 1 元容差
（避免把印刷四舍五入全报成错），但报告质量门会额外算一遍**精确到分**的对账差额，
让人知道「在容差内」和「精确对平」是两件事。

本模块只做 Decimal 转换/求和/求差，不改变任何既有判定。
"""
from decimal import Decimal, ROUND_HALF_UP

CENT = Decimal("0.01")


def d(x):
    if x is None or x == "":
        return Decimal("0")
    if isinstance(x, Decimal):
        return x
    return Decimal(str(x))


def q2(x):
    return d(x).quantize(CENT, rounding=ROUND_HALF_UP)


def sum2(values):
    total = Decimal("0")
    for v in values:
        total += d(v)
    return total.quantize(CENT, rounding=ROUND_HALF_UP)


def diff2(a, b):
    return (d(a) - d(b)).quantize(CENT, rounding=ROUND_HALF_UP)


def eq2(a, b):
    return diff2(a, b) == Decimal("0.00")
