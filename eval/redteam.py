# -*- coding: utf-8 -*-
"""红队自测：主动构造「专门骗这个引擎」的单据，看它会不会上当。

## 这是「诚信」的最高形态

前面几层诚信都是自证：
- 交付门说「我不能交付」，判据是我们自己定的
- 负向知识库说「这 12 种情况我不判」，也是我们自己列的

自证的天花板是「我说我不行」——听起来诚实，但没有证明力。

**真正可验证的诚信是：主动去找自己的边界在哪，把找到的结果公开。**
所以这里换一个姿势：不解释自己能做什么，而是**扮演一个想骗过系统的人**，
用真实世界里存在的花招去攻击它，然后如实报告哪几招得手了。

得手的不是丑闻，是**下一版的工单**；没得手的才是我可以拿出来说的能力。

## 攻击面（都来自真实单据里见过的写法）

| # | 花招 | 攻击什么 | 期望 |
|---|---|---|---|
| A1 | 同一工序换个近义名再列一次 | 本体归一 | 判重复（归不了一 = 本体缺口） |
| A2 | 名字里塞空格/全角字符 | 归一化 | 仍能对齐 |
| A3 | ㎡ 写成 m（量纲差 1 个数量级） | R5/R6 单位校验 | 报单位异常 |
| A4 | 有工程量、单价写 0 | R9 数量/单价缺失 | 报「有量无价」 |
| A5 | 把漏项写成表尾「××处理费 1 项」 | 汇总行分层 | 不算漏（确实有），也不当工序 |
| A6 | 漏项只写在备注里 | 第二路证据词 | 不判漏 |
| A7 | 优惠写成两行（一行负、一行正）抵消 | 减项对账 | 净额正确，不误报 |
| A8 | 数量写 0 但合价不为 0 | R8 算术 | 报算术不符 |

## 纪律

- 每类都必须给出「期望」，否则跑完也不知道算不算被骗；
- 结果分三档：**拦住 / 得手 / 不适用**（判据本身不覆盖这种情形）；
- 得手的写进输出，不藏的。
"""
import argparse
import copy
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
import pipeline as P  # noqa: E402

DATA = os.path.join(ROOT, "data")
HEAD = "项目名称,规格工艺,单位,数量,单价,合价,备注\n"


def _env():
    with open(os.path.join(DATA, "templates_v0.json"), encoding="utf-8") as f:
        tpl = json.load(f)
    with open(os.path.join(DATA, "rules_v0.json"), encoding="utf-8") as f:
        rules = json.load(f)
    with open(os.path.join(DATA, "coverage_v0.json"), encoding="utf-8") as f:
        cov = json.load(f)
    return P.Ontology(os.path.join(DATA, "ontology_v0.json")), tpl, rules, cov


def _audit_csv(csv_text, env, tk="old_house"):
    d = tempfile.mkdtemp()
    p = os.path.join(d, "q.csv")
    with open(p, "w", encoding="utf-8-sig") as f:
        f.write(csv_text)
    q = P.build_line_index(P.load_quote(p, "红队"), env[0])
    r = P.audit([q], env[0], env[1], env[2], env[3], template_key=tk)["红队"]
    return q, r


def _rules_of(res):
    return {(x.get("rule") or "") for x in (res.get("risks") or [])}


def _dups_of(res):
    return [d for d in (res.get("duplicates") or [])]


# ---------------------------------------------------------------- 攻击实现

def a1_synonym_duplicate(env):
    """同一工序换个近义名再列一次。真正的问题：本体能不能把两个名字归到一个概念。"""
    csv = HEAD + (
        "地砖铺贴,800x800,㎡,40,120,4800,\n"
        "地砖安装,800x800,㎡,40,120,4800,\n"
    )
    q, r = _audit_csv(csv, env)
    cids = [x.get("cid") for x in q["rows"]]
    dup = _dups_of(r)
    if len(set(cids)) == 1 and cids[0]:
        ok = bool(dup)
        return ("拦住" if ok else "得手",
                "两个名字归一到同一个 cid=%s，重复结论 %d 条" % (cids[0], len(dup)))
    return ("得手", "没归一到同一概念（cid=%s）——本体缺口，重复检测直接失效" % cids)


def a2_alias_with_space(env):
    """名字里塞空格 + 全角括号。真实 OCR 常出。"""
    csv = HEAD + (
        "地砖铺贴,800x800,㎡,40,120,4800,\n"
        "地 砖 铺 贴 （同款）,800x800,㎡,40,120,4800,\n"
    )
    q, r = _audit_csv(csv, env)
    cids = [x.get("cid") for x in q["rows"]]
    if all(cids) and len(set(cids)) == 1:
        return ("拦住" if _dups_of(r) else "得手",
                "空格/全角干扰下仍归一到 %s；重复 %d 条" % (cids[0], len(_dups_of(r))))
    return ("得手", "干扰字符让对齐失败（cid=%s）" % cids)


def a3_unit_switch(env):
    """㎡ 写成 m：量纲差一个数量级，是最容易藏钱的地方。"""
    csv = HEAD + (
        "防水,聚氨酯,㎡,20,80,1600,\n"
        "墙面粉刷,立邦,m,50,38,1900,\n"
    )
    q, r = _audit_csv(csv, env)
    hit = _rules_of(r) & {"R5_unit_mix", "R6_unit_anomaly"}
    return ("拦住" if hit else "得手",
            "单位异常规则命中：%s（该单只有一个 m，跨单 R5 不适用）" % (sorted(hit) or "无"))


def a4_qty_without_price(env):
    """有工程量、单价写 0。业主会以为「含了」，实际施工时补钱。"""
    csv = HEAD + (
        "防水,聚氨酯,㎡,20,0,0,\n"
        "墙面粉刷,立邦,㎡,50,38,1900,\n"
    )
    q, r = _audit_csv(csv, env)
    hit = _rules_of(r) & {"R9_qty_unit", "R8_arithmetic"}
    rows = [(x["name"], x["qty"], x["price"], x["amount"]) for x in q["rows"]]
    return ("拦住" if hit else "得手",
            "规则命中 %s｜原文行 %s" % (sorted(hit) or "无", rows))


def a5_hidden_in_summary(env):
    """把「防水」写成表尾「防水处理费 1 项」，试图既不算漏项也不算工序。"""
    csv = HEAD + (
        "墙面粉刷,立邦,㎡,50,38,1900,\n"
        "防水处理费,,项,1,1600,1600,\n"
    )
    q, r = _audit_csv(csv, env)
    missing = [m["concept"] for m in r["missing"] if m["tier"] == "auto"]
    dup_names = [d["concept"] for d in _dups_of(r)]
    leaked = [x for x in dup_names if "防水" in str(x)]
    if "防水" in missing:
        return ("得手", "仍被判漏项（表尾写法没被认作已含）——误报")
    return ("拦住", "未被判漏项（auto 漏项：%s）；重复结论：%s" % (missing[:3], dup_names))


def a6_missing_in_note(env):
    """漏项只写在上一行的备注里（不单列）。"""
    csv = HEAD + (
        "墙面基础处理,含界面剂,㎡,120,45,5400,\n"
    )
    q, r = _audit_csv(csv, env)
    missing = [m["concept"] for m in r["missing"] if m["tier"] == "auto"]
    if "界面剂" in missing or "墙固" in missing:
        return ("得手", "备注里的「含界面剂」没被第二路证据救回")
    return ("拦住", "备注证据生效，「界面剂/墙固」未判漏（auto 漏项数 %d）" % len(missing))


def a7_split_discount(env):
    """优惠拆两行：一行 -3000、一行 +3000，试图让净额变成 0 但制造混乱。"""
    csv = HEAD + (
        "拆除墙体,砖混,㎡,10,50,500,\n"
        "全屋优惠,,项,1,-3000,-3000,\n"
        "活动补贴,,项,1,3000,3000,\n"
        "合计,,,,,500,\n"
    )
    q, r = _audit_csv(csv, env)
    rc = q["recon"]
    ok = rc.get("ok")
    return ("拦住" if ok else "得手",
            "减项 %s、歧义 %d 条、对账 diff=%s、ok=%s"
            % (rc.get("discount_sum"), len(rc.get("discount_ambiguous") or []),
               rc.get("diff"), ok))


def a8_zero_qty_nonzero_amount(env):
    """数量 0 但合价不为 0：凭空出现的一笔钱。"""
    csv = HEAD + (
        "垃圾清运,,项,0,0,800,\n"
        "墙面粉刷,立邦,㎡,50,38,1900,\n"
    )
    q, r = _audit_csv(csv, env)
    hit = _rules_of(r)
    if "R8_arithmetic" in hit or "R9_qty_unit" in hit:
        return ("拦住", "规则命中 %s" % sorted(hit & {"R8_arithmetic", "R9_qty_unit"}))
    return ("得手", "数量 0、合价 800 静默通过（命中规则：%s）" % (sorted(hit) or "无"))


ATTACKS = (
    ("A1", "同一工序换近义名再列一次", "本体归一", a1_synonym_duplicate),
    ("A2", "名字塞空格 + 全角括号", "归一化鲁棒性", a2_alias_with_space),
    ("A3", "㎡ 写成 m（量纲差一个数量级）", "单位校验", a3_unit_switch),
    ("A4", "有工程量、单价写 0", "有量无价", a4_qty_without_price),
    ("A5", "漏项伪装成表尾「××处理费」", "汇总行分层", a5_hidden_in_summary),
    ("A6", "漏项只写在备注里", "第二路证据词", a6_missing_in_note),
    ("A7", "优惠拆成负数+正数两行", "减项对账", a7_split_discount),
    ("A8", "数量 0 但合价不为 0", "算术校验", a8_zero_qty_nonzero_amount),
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    env = _env()

    rows = []
    for aid, name, target, fn in ATTACKS:
        try:
            verdict, detail = fn(env)
        except Exception as e:  # noqa: BLE001
            verdict, detail = "异常", "%s: %s" % (type(e).__name__, e)
        rows.append({"id": aid, "attack": name, "target": target,
                     "verdict": verdict, "detail": detail})

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0

    print("# 红队自测：主动攻击这套引擎，看哪几招得手")
    print()
    print("> 姿势：不解释自己能做什么，而是扮演一个想骗过系统的人。")
    print("> 得手的不是丑闻，是下一版的工单；没得手的才是能拿出来说的能力。")
    print()
    print("| # | 花招 | 攻击面 | 结果 | 细节 |")
    print("|---|---|---|---|---|")
    for x in rows:
        mark = {"拦住": "✅ 拦住", "得手": "**❌ 得手**", "不适用": "— 不适用"}.get(x["verdict"], x["verdict"])
        print("| %s | %s | %s | %s | %s |" % (x["id"], x["attack"], x["target"], mark, x["detail"]))

    blocked = sum(1 for x in rows if x["verdict"] == "拦住")
    got = [x for x in rows if x["verdict"] == "得手"]
    print()
    print("拦住 **%d/%d**，得手 %d 招。" % (blocked, len(rows), len(got)))
    if got:
        print()
        print("得手的：")
        for x in got:
            print("- **%s %s** → %s" % (x["id"], x["attack"], x["detail"]))
        print()
        print("这些会被逐条处理：能修的当轮修掉，修不掉的写进负向知识库并标明边界。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
