# -*- coding: utf-8 -*-
"""报告交付门：让报告自己说「我不能交付」。

## 为什么需要这一层

单条结论已经有 auto / review / manual 三档，但那是「**这条结论**有多可信」。
整份报告缺一个更上层的判断：**这些结论合起来，能不能交到业主手里**。

没有这一层的后果很具体：一份报告可以同时满足
「12 条漏项全是 tier=auto」和「账目差 11,711 元却没解释」，
然后照样导成 PDF、盖上指纹、发给业主。

## 三档

| 档 | 含义 | 谁能出 |
|---|---|---|
| `pass` | 可以交付。结论站得住，字段齐全 | 任何角色 |
| `needs_fix` | 能看懂，但有字段缺失，需要补 | 内部底稿 |
| `block` | **禁止交付**。出现无依据金额 / 金额口径错 / 不可复现 / 账目有差异未披露 | 谁都不能出 |

## 为什么 block 只留四条

block 用得越多越像营销，用得越少越没意义。
所以只拦「业主拿着这份报告去质问装修公司会当场翻车」的四种情形：

- 金额是编的（说不出这钱从哪来）
- 金额算错了（低置信结论混进了核减金额）
- 同一单跑两次不一样（不可复现）
- 账对不上却装作对上了（差异没披露）

**不拦**的东西同样重要：措辞不漂亮、缺一条风险提示、模板选错——
这些进 `needs_fix`，不 block。否则业主永远拿不到报告，
这跟我们「宁可少报不可错报」是两回事：**少报可以，交假的不行**。

## 对标

`jayden-lg/costing-master` 的 second-pass-review 同样是三档
（通过 / 需修正 / 禁止交付），且把「无依据金额」列为禁止交付项——
这一条是行业共识，不是我们发明的。

我们额外加了一条它没有的 **D6 可复现**（同一输入 → 同一指纹）：
这一条只有确定性引擎能做，纯 LLM 形态的产品做不了。
它也是本项目导出给评委看时最硬的一句：**你可以不信我的话，但不能不信同一个数跑两次还是它。**
"""

import os as _os
import sys as _sys

# 这个模块有两条被加载的路径，导入方式不同：
#   1. scripts/*.py 先 `sys.path.insert(0, <repo>/src)`，于是 `import report_gate`；
#   2. main.py 用 `from src import report_gate`（FastAPI/Web 场景）。
# 原来只写 `import pipeline as P`，第 2 条会直接 ModuleNotFoundError——
# 这也是交付门一直没接进 Web 主链路的真实原因（不是设计取舍，是 import 崩了）。
try:
    import pipeline as P
except ModuleNotFoundError:  # pragma: no cover - 仅在 Web 场景走这里
    _HERE = _os.path.dirname(_os.path.abspath(__file__))
    if _HERE not in _sys.path:
        _sys.path.insert(0, _HERE)
    import pipeline as P

# 检查项定义：id / 名称 / 级别（block 表示失败即禁止交付）
CHECKS = (
    ("D1", "可追溯：每条结论能指回具体行或明确基准", "needs_fix"),
    ("D2", "可复算：每条结论带计算公式", "needs_fix"),
    ("D3", "账目闭合：对账差异为 0，或已作为结论披露", "block"),
    ("D4", "金额有据：结论金额能回溯到原表行或参照价库", "block"),
    ("D5", "分级守纪：低置信结论未混入核减金额", "block"),
    ("D6", "可复现：同一输入重跑得到同一判定指纹", "block"),
    ("D7", "无未覆盖偏差：小额同向累积必须有人管", "needs_fix"),
)


def _conclusions(res):
    """遍历三类结论，统一成 (kind, item)。"""
    for m in res.get("missing") or []:
        yield "missing", m
    for d in res.get("duplicates") or []:
        yield "duplicate", d
    for x in res.get("risks") or []:
        yield "risk", x


def _lines_of(item):
    ls = item.get("lines")
    if ls:
        return [str(v) for v in ls if str(v).strip()]
    one = item.get("line")
    return [str(one)] if one not in (None, "") else []


def _check_traceable(res):
    """D1：漏项没有行号（它本来就不在表里），但必须有对照基准；其余必须有行号或基准。"""
    bad = []
    for kind, item in _conclusions(res):
        basis = (item.get("evidence") or {}).get("basis")
        if kind == "missing":
            if not basis:
                bad.append("%s（漏项缺对照基准）" % (item.get("concept") or "?"))
        elif not _lines_of(item) and not basis:
            name = item.get("concept") or item.get("rule") or "?"
            bad.append("%s（无行号也无基准）" % name)
    return (not bad), bad


def _check_formula(res):
    """D2：公式缺失只影响可读性，不影响对错，所以只进 needs_fix。"""
    bad = []
    for kind, item in _conclusions(res):
        if not (item.get("evidence") or {}).get("formula"):
            bad.append("%s（缺公式）" % (item.get("concept") or item.get("rule") or "?"))
    return (not bad), bad


def _check_reconciled(res):
    """D3：对不上账本身不是错，**对不上还不说**才是错。

    真实单据里 2/5 都有小计误差。把差异当成一条 R13 结论列出来，
    这份报告就是诚实的；差异存在却没有任何一条 R13，就是在掩盖。
    """
    rc = res.get("recon") or {}
    ok = rc.get("ok")
    disc = rc.get("discount_ambiguous") or []
    if ok is None:
        # 没有印刷合计 → 没有对账锚点，不算失败，但要说明
        return True, ["无印刷合计，未做整单对账（已注明）"], "skip"
    if ok:
        if disc:
            return True, ["对账闭合；另有 %d 行优惠表述歧义，已单列待确认" % len(disc)], "pass"
        return True, [], "pass"
    has_r13 = any((x.get("rule") or "").startswith("R13") for x in res.get("risks") or [])
    diff = rc.get("diff")
    if has_r13:
        return True, ["账目差 %s 元，已作为 R13 结论披露" % diff], "pass"
    return False, ["账目差 %s 元，但报告里没有对应结论——这是掩盖" % diff], "fail"


def _check_amount_sourced(res):
    """D4：这是全模块最重要的一条——不许有说不出出处的钱。"""
    bad = []
    for kind, item in _conclusions(res):
        if kind == "missing":
            amt = item.get("ref_amount")
            if amt in (None, 0):
                continue
            if not (item.get("ref_source") or item.get("ref_basis")):
                bad.append("%s %s 元（无参照价来源）" % (item.get("concept"), amt))
        else:
            amt = item.get("amount")
            if amt in (None, 0):
                continue
            # 重复计价/风险的金额是从**具体那些行**加出来的，所以必须有行号。
            # evidence.basis 解释的是「为什么算重复」，不能替金额背锅。
            if not _lines_of(item):
                bad.append("%s %s 元（无行号，说不出在哪几行）"
                           % (item.get("concept") or item.get("rule"), amt))
    # 增项预测：有金额区间就必须有参照样本，没有就只该给定性建议
    for it in (res.get("addon_forecast") or {}).get("items") or []:
        rng = it.get("amount_range")
        if rng and not it.get("reference"):
            bad.append("%s 有区间无参照样本" % (it.get("title") or it.get("id")))
    return (not bad), bad


def _check_tier_discipline(res):
    """D5：核减金额必须**逐分**等于 auto 档之和。

    这条是「低分不进钱」从口号变成可验证事实的地方：
    只要有人把 review 档的金额加进 missing_cost，这里立刻不等。
    """
    bad = []
    auto_missing = round(sum(m.get("ref_amount") or 0
                             for m in res.get("missing") or []
                             if m.get("tier") == "auto"), 2)
    declared_missing = round(res.get("missing_cost") or 0, 2)
    if abs(auto_missing - declared_missing) > 0.01:
        bad.append("漏项核减 %s ≠ auto 档合计 %s" % (declared_missing, auto_missing))

    auto_dup = round(sum(d.get("amount") or 0
                         for d in res.get("duplicates") or []
                         if d.get("tier") == "auto"), 2)
    declared_dup = round(res.get("duplicate_cut") or 0, 2)
    if abs(auto_dup - declared_dup) > 0.01:
        bad.append("重复核减 %s ≠ auto 档合计 %s" % (declared_dup, auto_dup))
    return (not bad), bad


def _check_uncovered_drift(quote, res):
    """D7：单行几分钱没人管，但几十行同向累积就是「蚂蚁搬家」。

    ## 这个检查补的是规则之间的空隙

    R8 的判据是 `|合价 − 数量×单价| > 1`，所以单行改 0.99 元不报；
    R13 的容差是 `max(1.0, |合计| × 0.5%)`，对 15 万的单约 750 元，
    所以累计 99 元也不报。两条规则中间留了一段**完全沉默的地带**：
    100 行各虚 0.99 元 = 99 元，谁也抓不到。

    实测见 `eval/detection_floor.py`：8 组配置里 7 组双规则沉默。

    ## 为什么能区分「正常舍入」和「系统性偏移」

    真实单据的舍入是**双向**的：篱笆那份 15 行小额偏差，正向 9 行、反向 6 行，
    净额只有 +0.23 元——随机误差自己抵消了。
    而「蚂蚁搬家」是**单向**的：几十行全往上取整，净额随时间线性增长。

    所以判据用「净额 + 同向比例」，不用「偏差行数」。
    """
    rows = quote.get("rows") or []
    small = []
    for r in rows:
        if (r.get("qty") or 0) > 0 and (r.get("price") or 0) > 0:
            d = round((r.get("amount") or 0) - r["qty"] * r["price"], 2)
            if 0 < abs(d) <= 1:
                small.append(d)
    if len(small) < 5:
        return True, []
    total = round(sum(small), 2)
    pos = sum(1 for d in small if d > 0)
    same = max(pos, len(small) - pos) / len(small)
    if abs(total) >= 1 and same >= 0.8:
        return False, [
            "%d 行小额偏差同向累积 %+.2f 元（同向 %.0f%%），但没有任何规则覆盖——"
            "可以是一次整数报价的习惯，也可能是系统性多收；报告里必须给一条待确认"
            % (len(small), total, same * 100)
        ]
    return True, []


def _check_reproducible(quote, res, expected_digest, replay):
    """D6：同一输入两次跑出同一指纹。

    replay 是一个返回新 result 的可调用（通常就是再跑一次 audit）。
    不给 replay 也不给 expected 时记为 skip —— 但报告里要写明「本次未验」，
    不能假装验过。
    """
    try:
        digest = P.result_digest(quote, res)
    except Exception as e:  # noqa: BLE001
        return False, ["指纹计算失败：%s" % e], "fail"
    if expected_digest:
        if digest == expected_digest:
            return True, ["指纹 %s 与基准一致" % digest], "pass"
        return False, ["指纹 %s ≠ 基准 %s" % (digest, expected_digest)], "fail"
    if replay is not None:
        try:
            again = replay()
        except Exception as e:  # noqa: BLE001
            return False, ["重跑失败：%s" % e], "fail"
        d2 = P.result_digest(quote, again)
        if d2 == digest:
            return True, ["重跑两次指纹一致（%s）" % digest], "pass"
        return False, ["两次指纹不一致：%s / %s" % (digest, d2)], "fail"
    return True, ["本次未做重跑验证（只算了一次指纹 %s）" % digest], "skip"


def check(quote, res, expected_digest=None, replay=None):
    """对一份审计结果做交付前的七项检查。

    参数
    ----
    quote : dict    `load_quote` 的产物（D6 需要它算指纹）
    res   : dict    `audit` 返回的单份结果
    expected_digest : str | None
        冻结基线指纹。给了就比对，比 replay 便宜。
    replay : callable | None
        返回同输入的新 result。不给则 D6 记 skip。

    返回
    ----
    dict: {verdict, checks[], blocking[], counts{pass,needs_fix,block,skip}, digest}
    """
    results = {}

    def put(cid, ok, detail, level=None):
        results[cid] = {"ok": bool(ok), "detail": list(detail or []),
                        "level": level or dict((c[0], c[2]) for c in CHECKS)[cid]}

    ok, detail = _check_traceable(res)
    put("D1", ok, detail)
    ok, detail = _check_formula(res)
    put("D2", ok, detail)
    ok, detail, _lv = _check_reconciled(res)
    put("D3", ok, detail)
    ok, detail = _check_amount_sourced(res)
    put("D4", ok, detail)
    ok, detail = _check_tier_discipline(res)
    put("D5", ok, detail)
    ok, detail, _lv = _check_reproducible(quote, res, expected_digest, replay)
    put("D6", ok, detail)
    ok, detail = _check_uncovered_drift(quote, res)
    put("D7", ok, detail)

    checks = []
    for cid, name, level in CHECKS:
        r = results[cid]
        checks.append({
            "id": cid, "name": name, "level": level,
            "ok": r["ok"], "detail": r["detail"],
        })

    blocking = [c for c in checks if not c["ok"] and c["level"] == "block"]
    fixable = [c for c in checks if not c["ok"] and c["level"] != "block"]
    if blocking:
        verdict = "block"
    elif fixable:
        verdict = "needs_fix"
    else:
        verdict = "pass"

    # 有哪些检查是「没真验」的，必须如实写进报告，不能算进 pass
    skipped = [c["id"] for c in checks
               if any("未做重跑验证" in d or "未做整单对账" in d for d in c["detail"])]

    return {
        "verdict": verdict,
        "checks": checks,
        "blocking": [c["id"] for c in blocking],
        "fixable": [c["id"] for c in fixable],
        "skipped": skipped,
        "digest": P.result_digest(quote, res),
    }


def to_markdown(gate):
    """一行结论 + 一张表。给 CLI 和报告页共用。"""
    label = {"pass": "可交付", "needs_fix": "需修正后交付", "block": "禁止交付"}
    lines = ["**交付判定：%s**（%s）" % (label.get(gate["verdict"], gate["verdict"]),
                                    gate["digest"]), ""]
    lines.append("| 检查 | 判定 | 说明 |")
    lines.append("|---|---|---|")
    for c in gate["checks"]:
        mark = "通过" if c["ok"] else ("**阻断**" if c["level"] == "block" else "需修正")
        detail = "；".join(c["detail"]) if c["detail"] else "—"
        lines.append("| %s %s | %s | %s |" % (c["id"], c["name"], mark, detail))
    return "\n".join(lines)


def to_html(gate):
    """报告页顶部的判定条。颜色只有三种，不玩花活。"""
    color = {"pass": ("#2F7A4F", "#EFF6F1"),
             "needs_fix": ("#8A6A1F", "#FBF5E6"),
             "block": ("#B33A2B", "#FBEEEC")}[gate["verdict"]]
    label = {"pass": "可交付", "needs_fix": "需修正后交付", "block": "禁止交付"}[gate["verdict"]]
    rows = []
    for c in gate["checks"]:
        mark = "通过" if c["ok"] else ("阻断" if c["level"] == "block" else "需修正")
        detail = "；".join(c["detail"]) if c["detail"] else "—"
        rows.append(
            "<tr><td class='gid'>%s</td><td>%s</td><td class='gm %s'>%s</td>"
            "<td class='gd'>%s</td></tr>"
            % (c["id"], c["name"], "ok" if c["ok"] else "no", mark, detail)
        )
    return """<div class="gate" style="border-left:3px solid %s;background:%s">
  <div class="gh"><b>交付判定：%s</b><span>判定指纹 %s</span></div>
  <table class="gt">%s</table>
</div>""" % (color[0], color[1], label, gate["digest"], "".join(rows))
