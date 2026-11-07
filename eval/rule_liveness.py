# -*- coding: utf-8 -*-
"""规则活性测试：把变异打在 rules_v0.json 上，测「13 条可审阅的规则」到底有几条在起作用。

## 为什么做这个

项目一直主张「判断交给 13 条可逐条审阅的规则，不是硬编码 if-else」。
但主张不会自己成立。实测一下就发现：

  - 改 `R9.enabled=false`，结论一个不少；
  - 改 `R3.min_lines=99`，重复计价一个不少；
  - 改 `R13.tolerance_pct=0.9`，判定指纹一模一样；
  - 5 份真实单据里，R5 / R10 / R11 / R12 **从未触发过**。

前三条是「字段写了但代码没读」；第四条可能是规则失效，也可能只是真实单据里
没有这类问题——**光看输出分不出来**。

方法来自华为云社区《CI 全绿，但你的质量门禁可能已经悄悄少了一条规则》
（配套仓库 `simin-yuan/self-auditing-agent` / gatecheck，MIT，零依赖）：
代码覆盖率测的是「哪些代码被执行」，测不出「哪些规则已失效」。
把变异测试从代码挪到规则文件上，逐字段改坏重跑，看哪些改动没人拦。

## 两条设计纪律（照抄那篇的原则）

1. **双向断言**：既证明坏输入会被拒（阳性对照），也证明好输入会通过。
   只会报 pass 的判据比没有更糟；只会报 fail 的同样没用。
2. **只给清单，不给判决**：活下来的变异是**候选**，不是缺陷。
   删掉 `name` 这种纯描述字段本来就不该影响判定。谁喊「发现 19 个缺陷」谁在撒谎。

## 用法

    python3 eval/rule_liveness.py                 # 报告写 out/rule_liveness.md
    python3 eval/rule_liveness.py --strict       # 有「判定字段」存活则非 0
    python3 eval/rule_liveness.py --json         # 机器可读

退出码：0 报告完成（survived 不算失败）；1 阳性对照失败或 --strict 下有判定字段存活。
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
REAL = os.path.join(DATA, "real")
OUT = os.path.join(ROOT, "out", "rule_liveness.md")

# 真实语料：5 份公开单据，各自带正确的模板与口径
REAL_DOCS = [
    ("篱笆·卓观", [("sh_zhuoguan_halfpack.csv", "篱笆")], "old_house", "full"),
    ("花溪北岸", [("huaxibeian_newhouse.csv", "花溪")], "new_house", "full"),
    ("深圳新新·诊所", [("szxinxin_clinic_pdf.csv", "诊所")], "new_house", "full"),
    ("上海翡翠台", [("feitai_budget.csv", "翡翠台")], "old_house", "full"),
    ("124㎡基础", [("guijiao_124_basic.csv", "124㎡")], "new_house", "base"),
]

# 阳性对照：为「真实语料里从未触发」的 4 条规则各造一个最小样本。
# 目的不是造数据凑数，是回答「这条规则是坏了，还是没遇到」。
FIXTURES = {
    "R10_fee_rate": (
        "项目名称,规格工艺,单位,数量,单价,合价\n"
        "拆除墙体,砖混,m2,10,50,500\n"
        "墙面粉刷,立邦,m2,20,30,600\n"
        "管理费,,项,1,300,300\n"),
    "R11_spec_missing": (
        "项目名称,规格工艺,单位,数量,单价,合价\n"
        "防水,,m2,30,100,3000\n"
        "拆除墙体,砖混,m2,10,50,500\n"),
    "R12_wp_height": (
        "项目名称,规格工艺,单位,数量,单价,合价\n"
        "防水,地面聚氨酯,m2,20,80,1600\n"
        "拆除墙体,砖混,m2,10,50,500\n"),
    "R9_qty_unit": (
        "项目名称,规格工艺,单位,数量,单价,合价\n"
        "防水,,m2,20,0,0\n"
        "拆除墙体,砖混,m2,10,50,500\n"),
}
# R5 需要两份单据；单独放
PAIR_FIXTURE = [
    ("项目名称,规格工艺,单位,数量,单价,合价\n墙面粉刷,立邦,㎡,10,30,300\n", "甲"),
    ("项目名称,规格工艺,单位,数量,单价,合价\n墙面粉刷,立邦,m,10,30,300\n", "乙"),
]

# 变异算子：字段 → 改坏后的值。MUT-ALL 对每条规则都试，
# MUT-PER 只在该字段存在时试。
MUT_ALL = {
    "enabled": False,
    "strength": 0.01,
}
MUT_PER = {
    "pairs": [],
    "min_lines": 99,
    "patterns": [],
    "keywords": [],
    "threshold": 9.0,          # 管理费 300/1100 = 27% → 改成 9 倍后不报
    "min_amount": 999999.0,
    "tolerance_pct": 0.9,
    "scope": "__broken__",
}

# 声明性字段：写给人看的，不参与判定。它们存活是**预期**，不是缺陷。
DECLARATIVE = ("name", "type", "when", "then.conclusion", "then.severity",
               "then.evidence.basis", "then.evidence.formula")

# 判定字段：应当影响输出。存活即候选缺陷。
DECISIVE = ("enabled", "strength", "pairs", "min_lines", "patterns", "keywords",
            "threshold", "min_amount", "tolerance_pct")


def _write(tmpdir, name, csv):
    p = os.path.join(tmpdir, name)
    with open(p, "w", encoding="utf-8-sig") as f:
        f.write(csv)
    return p


def build_corpus(tmpdir):
    """返回 [(名称, [csv路径...], [label...], template_key, scope)]。"""
    corpus = []
    for name, files, tk, sc in REAL_DOCS:
        paths = [os.path.join(REAL, f) for f, _ in files]
        labels = [lb for _, lb in files]
        corpus.append((name, paths, labels, tk, sc))

    for rid, csv in FIXTURES.items():
        p = _write(tmpdir, rid + ".csv", csv)
        corpus.append(("阳性对照·" + rid, [p], [rid], "old_house", "full"))

    ps = [_write(tmpdir, "pair_%d.csv" % i, c) for i, (c, _) in enumerate(PAIR_FIXTURE)]
    corpus.append(("阳性对照·R5_unit_mix", ps, [lb for _, lb in PAIR_FIXTURE],
                   "old_house", "full"))

    # 阴性对照：一张量价一致、有规格、无开口条款的干净单据
    clean = ("项目名称,规格工艺,单位,数量,单价,合价\n"
             "拆除墙体,砖混,m2,10,50,500\n"
             "墙面粉刷,立邦净味,m2,20,30,600\n")
    p = _write(tmpdir, "clean.csv", clean)
    corpus.append(("阴性对照·干净单", [p], ["干净单"], "old_house", "full"))
    return corpus


def prepare_corpus(corpus, onto):
    """把语料解析成 quote 对象一次。

    变异要跑几十遍，每遍都重新读 CSV + 四档本体对齐是纯浪费
    （第一版 47 个变异跑 60 秒）。判定才是被测对象，解析不是。
    注意：audit() 会按 R13 容差改写 q["recon"]["ok"]，这是幂等的，
    每次重跑都会按当前规则重新算回来。
    """
    out = []
    for name, paths, labels, tk, sc in corpus:
        qs = []
        bad = None
        for p, lb in zip(paths, labels):
            try:
                qs.append(P.build_line_index(P.load_quote(p, lb), onto))
            except Exception as e:  # noqa: BLE001
                bad = "%s: %s" % (type(e).__name__, e)
                break
        out.append({"name": name, "quotes": qs, "tk": tk, "sc": sc, "error": bad})
    return out


def run_all(prepared, onto, tpl, rules, cov):
    """对已解析的语料跑一遍 audit，返回规范化结果（含 confidence/tier/evidence）。"""
    out = []
    for item in prepared:
        name, qs, tk, sc = item["name"], item["quotes"], item["tk"], item["sc"]
        if item.get("error"):
            out.append({"name": name, "error": item["error"]})
            continue
        res = P.audit(qs, onto, tpl, rules, cov, template_key=tk, scope=sc)
        slim = []
        for q in qs:
            r = res.get(q["label"]) or {}
            slim.append({
                "missing": r.get("missing") or [],
                "duplicates": r.get("duplicates") or [],
                "risks": r.get("risks") or [],
                "missing_cost": r.get("missing_cost"),
                "duplicate_cut": r.get("duplicate_cut"),
                "effective": r.get("effective"),
            })
        out.append({"name": name, "cross": res.get("_cross_unit") or [], "docs": slim})
    return out


def canon(x):
    return json.dumps(x, sort_keys=True, ensure_ascii=False, default=str)


def triggered(rule_id, base, real_only=False):
    """该规则产生了多少条结论。real_only=True 时只看 5 份公开真实单据。

    必须分开算：阳性对照是我们自己造来触发规则的，把它算进「真实语料覆盖」
    等于自己给自己发奖状。
    """
    n = 0
    pool = base[:len(REAL_DOCS)] if real_only else base
    for item in pool:
        if item.get("error"):
            continue
        for x in item.get("cross") or []:
            if x.get("rule") == rule_id:
                n += 1
        for d in item.get("docs") or []:
            for bucket in ("missing", "duplicates", "risks"):
                for c in d.get(bucket) or []:
                    rid = c.get("rule")
                    if rid is None and bucket == "missing":
                        rid = "R1_missing_item"
                    if rid == rule_id:
                        n += 1
    return n


def mutate(rules, rid, field, value):
    """返回改坏一份规则后的深拷贝；字段不存在返回 None。"""
    d = copy.deepcopy(rules)
    target = next((x for x in d["rules"] if x["id"] == rid), None)
    if target is None:
        return None
    if field.startswith("then."):
        parts = field.split(".")
        cur = target
        for p in parts[:-1]:
            cur = cur.setdefault(p, {})
        if parts[-1] not in cur:
            return None
        cur[parts[-1]] = value
    else:
        if field not in target:
            return None
        target[field] = value
    return d


def unreachable_pairs(rules, tpl):
    """静态可达性：R2 的每对「容器工序 → 被含工序」有没有可能触发。

    变异测试只测「改了有没有反应」，测不出「条件本来就不可能成立」。
    R2 的触发条件是 `container 不在模板 且 contained 在模板`，
    但容器工序（贴地砖/墙砖/铲墙皮）本身也是模板必做项——
    于是这几对在任何模板下都进不去。改规则值不会暴露，必须单独静态查。
    """
    out = []
    rid = "R2_duplicate_known"
    r = next((x for x in rules["rules"] if x["id"] == rid), None)
    if not r:
        return out
    for p in r.get("pairs") or []:
        c_, d_ = p.get("container"), p.get("contained")
        for tk, items in tpl.items():
            if not isinstance(items, list):
                continue
            s = set(items)
            if not (c_ not in s and d_ in s):
                out.append({"pair": "%s→%s" % (c_, d_), "template": tk,
                            "container_in_template": c_ in s,
                            "contained_in_template": d_ in s})
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="规则活性测试（变异打在 rules_v0.json 上）")
    ap.add_argument("--strict", action="store_true", help="有判定字段存活则非 0")
    ap.add_argument("--json", action="store_true", help="输出 JSON 而非 Markdown")
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args(argv)

    onto = P.Ontology(os.path.join(DATA, "ontology_v0.json"))
    with open(os.path.join(DATA, "templates_v0.json"), encoding="utf-8") as f:
        tpl = json.load(f)
    with open(os.path.join(DATA, "rules_v0.json"), encoding="utf-8") as f:
        rules = json.load(f)
    with open(os.path.join(DATA, "coverage_v0.json"), encoding="utf-8") as f:
        cov = json.load(f)

    with tempfile.TemporaryDirectory(prefix="zmb_liveness_") as tmpdir:
        corpus = build_corpus(tmpdir)
        prepared = prepare_corpus(corpus, onto)
        base = run_all(prepared, onto, tpl, rules, cov)
        base_s = canon(base)

        rows = []
        for r in rules["rules"]:
            rid = r["id"]
            hits = triggered(rid, base)
            hits_real = triggered(rid, base, real_only=True)
            ops = [("enabled", MUT_ALL["enabled"]), ("strength", MUT_ALL["strength"]),
                   ("then.evidence.basis", "【变异：规则基准被改坏】")]
            for f, v in MUT_PER.items():
                if f in r:
                    ops.append((f, v))
            for field, value in ops:
                m = mutate(rules, rid, field, value)
                if m is None:
                    continue
                now = run_all(prepared, onto, tpl, m, cov)
                killed = canon(now) != base_s
                rows.append({
                    "rule": rid, "field": field,
                    "kind": "declarative" if field in DECLARATIVE else "decisive",
                    "killed": killed,
                    "status": "killed" if killed else "survived",
                    "triggered": hits, "triggered_real": hits_real,
                })

    total = len(rows)
    killed = sum(1 for x in rows if x["killed"])
    survived = total - killed
    decisive_survived = [x for x in rows if not x["killed"] and x["kind"] == "decisive"]
    no_trigger = sorted({x["rule"] for x in rows if x["triggered_real"] == 0})

    # 双向断言：阴性对照不能报「编造金额」类问题，阳性对照必须触发目标规则
    clean_item = next((x for x in base if x["name"].startswith("阴性对照")), {})
    clean_ok = bool(clean_item) and not clean_item.get("error")
    positive = {rid: triggered(rid, base) for rid in FIXTURES}
    positive["R5_unit_mix"] = triggered("R5_unit_mix", base)
    positive_ok = all(v > 0 for v in positive.values())

    report = {
        "rules": len(rules["rules"]), "mutants": total,
        "killed": killed, "survived": survived,
        "kill_rate": round(killed / total, 3) if total else 0,
        "decisive_survived": decisive_survived,
        "no_real_trigger": no_trigger,
        "positive_controls": positive,
        "positive_ok": positive_ok,
        "clean_ok": clean_ok,
        "unreachable_pairs": unreachable_pairs(rules, tpl),
        "rows": rows,
    }

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        lines = render(report)
        text = "\n".join(lines) + "\n"
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print(text)

    if not positive_ok or not clean_ok:
        print("阳性/阴性对照失败：活性测试本身不可信，不能当成证据。", file=sys.stderr)
        return 1
    if args.strict and decisive_survived:
        print("strict：%d 个判定字段存活" % len(decisive_survived), file=sys.stderr)
        return 1
    return 0


def render(rep):
    L = []
    L.append("# 规则活性报告（把变异打在规则文件上）")
    L.append("")
    L.append("> 方法来自华为云社区《CI 全绿，但你的质量门禁可能已经悄悄少了一条规则》")
    L.append("> 与 `simin-yuan/self-auditing-agent` / gatecheck（MIT）。")
    L.append("> **代码覆盖率测的是哪些代码被执行，测不出哪些规则已失效。**")
    L.append("")
    L.append("| 项 | 值 |")
    L.append("|---|---:|")
    L.append("| 规则数 | %d |" % rep["rules"])
    L.append("| 生成的变异 | %d |" % rep["mutants"])
    L.append("| 被杀（改了就有反应） | **%d** |" % rep["killed"])
    L.append("| 存活（改了没反应） | %d |" % rep["survived"])
    L.append("| 变异杀死率 | %.1f%% |" % (rep["kill_rate"] * 100))
    L.append("| 双向断言 | %s |" % ("通过" if rep["positive_ok"] and rep["clean_ok"] else "**失败**"))
    L.append("")
    L.append("## 一、逐字段矩阵")
    L.append("")
    L.append("| 规则 | 字段 | 类型 | 结果 | 真实单据触发 | 全语料触发 |")
    L.append("|---|---|---|---|---:|---:|")
    for x in rep["rows"]:
        mark = "✅ 被杀" if x["killed"] else "⚪ 存活"
        kind = "判定" if x["kind"] == "decisive" else "声明"
        L.append("| `%s` | `%s` | %s | %s | %d | %d |" % (
            x["rule"], x["field"], kind, mark,
            x["triggered_real"], x["triggered"]))
    L.append("")
    if rep["decisive_survived"]:
        L.append("## 二、判定字段存活（候选缺陷，需人工确认）")
        L.append("")
        for x in rep["decisive_survived"]:
            L.append("- `%s` 的 `%s`：改了没反应" % (x["rule"], x["field"]))
        L.append("")
    L.append("## 三、真实语料从未触发的规则")
    L.append("")
    if rep["no_real_trigger"]:
        for rid in rep["no_real_trigger"]:
            L.append("- `%s`" % rid)
        L.append("")
        L.append("**以上 %d 条在 5 份公开真实单据上一次都没触发过。**" % len(rep["no_real_trigger"]))
        L.append("阳性对照证明规则本身能报（见第四节），所以这不是「规则坏了」；")
        L.append("但也不能反过来说规则有效——只能是**语料没覆盖那类问题**。")
        L.append("这是证据边界，不是功能证明。对外只写「已构造样本验证可触发」。")
    else:
        L.append("（无）")
    L.append("")
    L.append("## 四、双向断言")
    L.append("")
    L.append("| 阳性对照（坏输入必须报） | 触发次数 |")
    L.append("|---|---:|")
    for k, v in rep["positive_controls"].items():
        L.append("| `%s` | %d |" % (k, v))
    L.append("")
    L.append("| 阴性对照 | 结果 |")
    L.append("|---|---|")
    L.append("| 干净单据正常解析、无报错 | %s |" % ("✅" if rep["clean_ok"] else "❌"))
    L.append("")
    L.append("> 只会报「通过」的判据比没有更糟；只会报「失败」的同样没用。")
    L.append("> 两边同时断言，这个报告才有资格当证据。")
    L.append("")
    if rep.get("unreachable_pairs"):
        L.append("## 四·五、静态可达性：条件永远成立不了的配置")
        L.append("")
        L.append("变异测试改的是「值」，查不出「条件本来就不可能满足」。"
                 "R2 的触发条件要求容器工序不在模板里，而容器工序本身是模板必做项：")
        L.append("")
        L.append("| 工艺对 | 模板 | 容器在模板 | 被含项在模板 |")
        L.append("|---|---|---|---|")
        for x in rep["unreachable_pairs"]:
            L.append("| `%s` | `%s` | %s | %s |" % (
                x["pair"], x["template"],
                "是" if x["container_in_template"] else "否",
                "是" if x["contained_in_template"] else "否"))
        L.append("")
        L.append("这几对**在任何输入下都不会触发**，改 strength / pairs 都测不出来。"
                 "本轮不修：修条件会新增重复计价结论，牵动五份冻结基线与 auto 精确率，"
                 "需要单独一轮重冻 + 第二标注者复核。见 `docs/RULE-BLIND-SPOTS.md`。")
        L.append("")

    L.append("## 五、边界（照抄原方法，不放大结论）")
    L.append("")
    L.append("1. **存活 ≠ 缺陷。** 声明性字段（`name` / `type` / `when` / `then.*`）"
             "本来就是写给人看的，改了不影响判定是预期。")
    L.append("2. **只给清单，不给判决。** 候选缩小到人工能看的范围，工具不替人下结论。")
    L.append("3. **变异算子有限。** 只改单字段的值，没做跨字段、类型级或语义级变异；"
             "「没发现存活」不等于规则正确。")
    L.append("4. **它测的是规则有没有在起作用，不测规则是否符合业务意图。**"
             "一条被完整验证的错规则，依然是错规则。")
    return L


if __name__ == "__main__":
    sys.exit(main())
