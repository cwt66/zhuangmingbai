# -*- coding: utf-8 -*-
"""哈希链账本的攻击套件：7 招必须拦住，1 招拦不住必须承认。

## 为什么要有「拦不住」的那一招

红队那一步的结论是「8 招拦住 7 招」，没人觉得那是失败，因为剩下一招写进了
负向知识库。防篡改同理：哈希链能防改单条、删条、换序、截尾、整条重写，
但如果攻击者**同时**把链和锚点一起重写，离线校验是发现不了的——
因为离线包里没有「链外的真相」。

所以这里第 8 招期望值是「拦不住」，写进报告，而不是从套件里删掉。
真正能发现它的是 L2：拿包里的 entry hash 去仓库 `data/ledger.jsonl`、
`data/ledger_anchor.json` 和 git 历史三处对照。

用法：
    python3 eval/ledger_chain.py            # 真实账本 + 攻击套件
    python3 eval/ledger_chain.py --self-test --json
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
import ledger as L  # noqa: E402

LEDGER = os.path.join(ROOT, "data", "ledger.jsonl")
ANCHOR = os.path.join(ROOT, "data", "ledger_anchor.json")
OUT = os.path.join(ROOT, "out", "ledger_chain.md")


def fixture(tmp):
    """造一条 3 条的合成链 + 锚点。用合成数据是为了让攻击可复现，
    不依赖仓库里真实账本有多少条。"""
    lp = os.path.join(tmp, "ledger.jsonl")
    ap = os.path.join(tmp, "anchor.json")
    for i in range(1, 4):
        L.append(lp, "audit", {"label": "doc%d" % i,
                               "result_digest": "d%d" % i}, "2026-11-09T00:00:0%d+08:00" % i)
    entries = L.load(lp)
    anchor = {"schema": "zmb-ledger-anchor/v1", "seq": len(entries),
              "head": entries[-1]["hash"], "anchored_at": "2026-11-09T00:00:09+08:00"}
    with open(ap, "w", encoding="utf-8") as f:
        json.dump(anchor, f, ensure_ascii=False)
    return lp, ap


def run_case(name, mutate, expect):
    """在干净 fixture 上施加一次攻击，返回判定结果。"""
    tmp = tempfile.mkdtemp(prefix="zmb_ledger_atk_")
    try:
        lp, ap = fixture(tmp)
        mutate(lp, ap)
        entries = L.load(lp)
        ok_chain, v_chain = L.verify(entries)
        anchor = None
        if os.path.isfile(ap):
            with open(ap, encoding="utf-8") as f:
                anchor = json.load(f)
        ok_anchor, v_anchor = L.check_anchor(entries, anchor)
        caught = not (ok_chain and ok_anchor)
        got = "拦截" if caught else "得手"
        return {"name": name, "expected": expect, "got": got,
                "ok": got == expect,
                "why": (v_chain + v_anchor)[:2]}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _write(lp, entries):
    with open(lp, "w", encoding="utf-8") as f:
        f.write(L.dumps(entries))


def attacks():
    def edit_payload(lp, ap):
        es = L.load(lp)
        es[1]["payload"]["result_digest"] = "TAMPERED"
        _write(lp, es)

    def delete_middle(lp, ap):
        es = L.load(lp)
        del es[1]
        _write(lp, es)

    def reorder(lp, ap):
        es = L.load(lp)
        es[0], es[1] = es[1], es[0]
        _write(lp, es)

    def truncate(lp, ap):
        es = L.load(lp)
        _write(lp, es[:-1])

    def rewrite_all(lp, ap):
        """整条重写：从零重算一条自洽的新链（hash 全部合法）。"""
        es = L.load(lp)
        prev, new = L.GENESIS, []
        for i, e in enumerate(es, 1):
            e2 = L.make_entry(i, e["ts"], e["kind"],
                             {"label": e["payload"]["label"],
                              "result_digest": "REWRITTEN%d" % i}, prev)
            new.append(e2)
            prev = e2["hash"]
        _write(lp, new)

    def append_unanchored(lp, ap):
        L.append(lp, "audit", {"label": "extra", "result_digest": "x"},
                 "2026-11-09T00:00:10+08:00")

    def anchor_edited(lp, ap):
        with open(ap, encoding="utf-8") as f:
            a = json.load(f)
        a["head"] = "f" * 64
        with open(ap, "w", encoding="utf-8") as f:
            json.dump(a, f, ensure_ascii=False)

    def rewrite_all_and_anchor(lp, ap):
        """第 8 招：链和锚点一起重写。离线校验发现不了——期望「得手」。"""
        rewrite_all(lp, ap)
        es = L.load(lp)
        with open(ap, "w", encoding="utf-8") as f:
            json.dump({"schema": "zmb-ledger-anchor/v1", "seq": len(es),
                       "head": es[-1]["hash"]}, f, ensure_ascii=False)

    return [
        ("干净账本必须 PASS（会说「是」）", lambda lp, ap: None, "PASS"),
        ("改一条 payload", edit_payload, "拦截"),
        ("删中间一条", delete_middle, "拦截"),
        ("换两条顺序", reorder, "拦截"),
        ("截尾（删最后一条）", truncate, "拦截"),
        ("整条重写成自洽新链", rewrite_all, "拦截"),
        ("新增一条但不重新锚定", append_unanchored, "拦截"),
        ("只改锚点 head", anchor_edited, "拦截"),
        ("链和锚点一起重写（已知盲点）", rewrite_all_and_anchor, "得手"),
    ]


def _bundle_build(tmp):
    """用临时账本打一个真实审计包，供包级攻击用。"""
    lp = os.path.join(tmp, "ledger.jsonl")
    ap = os.path.join(tmp, "anchor.json")
    zp = os.path.join(tmp, "base.zmb.zip")
    r = subprocess.run(
        [sys.executable, os.path.join(ROOT, "scripts", "make_bundle.py"),
         "--csv", os.path.join(ROOT, "data", "real", "feitai_budget.csv"),
         "--label", "链测试", "--scope", "full",
         "--out", zp, "--ledger", lp, "--anchor-file", ap],
        cwd=ROOT, capture_output=True, text=True)
    if r.returncode != 0 or not os.path.isfile(zp):
        raise RuntimeError("打不出测试包：%s" % (r.stderr or r.stdout)[-300:])
    return zp, lp, ap


def _extract(zp, dest):
    os.makedirs(dest)
    with zipfile.ZipFile(zp) as z:
        z.extractall(dest)
    return dest


def _l1(case_dir):
    r = subprocess.run([sys.executable, "verify.py", "--quiet"],
                       cwd=case_dir, capture_output=True, text=True)
    return "PASS" if r.returncode == 0 else "FAIL"


def _l2(zp, lp, ap):
    r = subprocess.run(
        [sys.executable, os.path.join(ROOT, "scripts", "verify_bundle.py"),
         "--zip", zp, "--ledger", lp, "--anchor-file", ap, "--json"],
        cwd=ROOT, capture_output=True, text=True)
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"status": "FAIL", "violations": ["L2 未输出可解析 JSON"]}


def _tamper_standards(case_dir):
    """攻击 A：删 3 个必做项，用包内引擎重算 digest 写回 result.json。"""
    p = os.path.join(case_dir, "standards", "templates_v0.json")
    with open(p, encoding="utf-8") as f:
        d = json.load(f)
    d["old_house"] = [x for x in d["old_house"]
                      if x not in ("protect_door", "water_test", "material_lift")]
    with open(p, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)

    sys.path.insert(0, os.path.join(case_dir, "src"))
    for m in list(sys.modules):
        if m == "pipeline":
            del sys.modules[m]
    import pipeline as TP  # noqa: E402
    TP.DATA = os.path.join(case_dir, "standards")
    rec = json.load(open(os.path.join(case_dir, "result.json"), encoding="utf-8"))
    onto = TP.Ontology(os.path.join(TP.DATA, "ontology_v0.json"))
    tpl = json.load(open(os.path.join(TP.DATA, "templates_v0.json"), encoding="utf-8"))
    rules = json.load(open(os.path.join(TP.DATA, "rules_v0.json"), encoding="utf-8"))
    cov = json.load(open(os.path.join(TP.DATA, "coverage_v0.json"), encoding="utf-8"))
    q = TP.build_line_index(TP.load_quote(os.path.join(case_dir, "input.csv"),
                                          rec["label"]), onto)
    res = TP.audit([q], onto, tpl, rules, cov, template_key=rec["template_key"],
                   scope=rec["scope"])[q["label"]]
    rec["digest"] = TP.result_digest(q, res)
    with open(os.path.join(case_dir, "result.json"), "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=2)
    sys.path.pop(0)
    for m in list(sys.modules):
        if m == "pipeline":
            del sys.modules[m]


def _refresh_hash_map(case_dir):
    """攻击 C：把 result.json 里的标准哈希表改成篡改后的实际值。"""
    rec = json.load(open(os.path.join(case_dir, "result.json"), encoding="utf-8"))
    base = os.path.join(case_dir, "standards")
    rec["standards_sha256"] = {
        n: hashlib.sha256(open(os.path.join(base, n), "rb").read()).hexdigest()
        for n in rec["standards_sha256"]}
    with open(os.path.join(case_dir, "result.json"), "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=2)


def _rewrite_all(case_dir):
    """攻击 B：哈希表、账本、锚点快照一起重写，让离线校验自洽。"""
    _refresh_hash_map(case_dir)
    base = os.path.join(case_dir, "standards")
    rec = json.load(open(os.path.join(case_dir, "result.json"), encoding="utf-8"))
    rec["standards_sha256"] = {
        n: hashlib.sha256(open(os.path.join(base, n), "rb").read()).hexdigest()
        for n in rec["standards_sha256"]}
    with open(os.path.join(case_dir, "result.json"), "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=2)

    def mdg(m):
        return hashlib.sha256(L.canonical(m).encode("utf-8")).hexdigest()

    pl = {"input_sha256": rec["input_sha256"],
          "standards_digest": mdg(rec["standards_sha256"]),
          "code_digest": mdg(rec["code_sha256"]),
          "result_digest": rec["digest"], "label": rec["label"],
          "template_key": rec["template_key"], "scope": rec["scope"]}
    entries = L.load(os.path.join(case_dir, "ledger.jsonl"))
    prev, new = L.GENESIS, []
    for i, e in enumerate(entries, 1):
        payload = pl if e["seq"] == 1 else e["payload"]
        e2 = {"seq": i, "ts": e["ts"], "kind": e["kind"],
              "payload": payload, "prev": prev}
        e2["hash"] = L.entry_hash(i, e2["ts"], e2["kind"], payload, prev)
        new.append(e2)
        prev = e2["hash"]
    with open(os.path.join(case_dir, "ledger.jsonl"), "w", encoding="utf-8") as f:
        f.write(L.dumps(new))
    with open(os.path.join(case_dir, "ledger_entry.json"), "w", encoding="utf-8") as f:
        json.dump(new[0], f, ensure_ascii=False, indent=2)
    with open(os.path.join(case_dir, "anchor_snapshot.json"), "w", encoding="utf-8") as f:
        json.dump({"schema": "zmb-ledger-anchor/v1", "seq": len(new),
                   "head": new[-1]["hash"]}, f, ensure_ascii=False, indent=2)


def bundle_attacks():
    """包级攻击：A/C 离线必须拦住，B 离线会过但 L2 必须拦住。

    这三招是 2026-11-09 真实跑出来的，不是设想：
    A 就是上一版 verify.py 失守的那一招（改标准 + 重算 digest）。
    """
    cases = []
    tmp = tempfile.mkdtemp(prefix="zmb_bundle_atk_")
    try:
        zp, lp, ap = _bundle_build(tmp)
        base = _extract(zp, os.path.join(tmp, "base"))
        cases.append({"name": "干净包 L1 必须 PASS", "expected": "PASS",
                      "got": _l1(base), "ok": _l1(base) == "PASS", "why": []})

        for name, fn, expect, layer in (
                ("改标准 + 重算结论（上一版失守的那招）", _tamper_standards, "FAIL", "L1"),
                ("改标准 + 同步改 result.json 哈希表",
                 lambda d: (_tamper_standards(d), _refresh_hash_map(d)), "FAIL", "L1"),
                ("改标准 + 链 + 锚点 + 哈希表整包重写",
                 lambda d: (_tamper_standards(d), _rewrite_all(d)), "FAIL", "L2")):
            d = _extract(zp, os.path.join(tmp, "c_" + str(len(cases))))
            fn(d)
            if layer == "L1":
                got = _l1(d)
                cases.append({"name": "%s【%s】" % (name, layer), "expected": expect,
                              "got": got, "ok": got == expect, "why": []})
            else:
                l1 = _l1(d)
                rz = os.path.join(tmp, "rewritten.zmb.zip")
                with zipfile.ZipFile(rz, "w", zipfile.ZIP_DEFLATED) as z:
                    for root_, _, files in os.walk(d):
                        for fn_ in files:
                            full = os.path.join(root_, fn_)
                            z.write(full, os.path.relpath(full, d))
                rep = _l2(rz, lp, ap)
                ok = (l1 == "PASS" and rep["status"] == "FAIL")
                cases.append({
                    "name": "%s（离线 %s / L2 %s）【L2】" % (name, l1, rep["status"]),
                    "expected": expect, "got": rep["status"], "ok": ok,
                    "why": (rep.get("violations") or [])[:2]})
    except Exception as e:  # noqa: BLE001
        cases.append({"name": "包级攻击套件自身跑不起来", "expected": "PASS",
                      "got": "ERROR", "ok": False, "why": [str(e)[:200]]})
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    blocked = sum(1 for c in cases if c["expected"] != "PASS" and c["ok"])
    return {"cases": cases, "attacks": len(cases) - 1, "blocked": blocked,
            "status": "PASS" if all(c["ok"] for c in cases) else "FAIL"}


def self_test():
    cases = []
    for name, mutate, expect in attacks():
        if expect == "PASS":
            # 干净 fixture 无攻击
            tmp = tempfile.mkdtemp(prefix="zmb_ledger_clean_")
            try:
                lp, ap = fixture(tmp)
                es = L.load(lp)
                ok_c, _ = L.verify(es)
                with open(ap, encoding="utf-8") as f:
                    ok_a, _ = L.check_anchor(es, json.load(f))
                got = "PASS" if (ok_c and ok_a) else "FAIL"
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
            cases.append({"name": name, "expected": "PASS", "got": got,
                          "ok": got == "PASS", "why": []})
        else:
            cases.append(run_case(name, mutate, expect))
    blocked = sum(1 for c in cases if c["expected"] == "拦截" and c["ok"])
    attacks_n = sum(1 for c in cases if c["expected"] == "拦截")
    gaps = sum(1 for c in cases if c["expected"] == "得手" and c["ok"])
    return {"status": "PASS" if all(c["ok"] for c in cases) else "FAIL",
            "cases": cases, "attacks": attacks_n, "blocked": blocked,
            "known_gaps": gaps, "total_cases": len(cases)}


def real_ledger():
    entries = L.load(LEDGER)
    ok_c, v_c = L.verify(entries)
    anchor = None
    if os.path.isfile(ANCHOR):
        with open(ANCHOR, encoding="utf-8") as f:
            anchor = json.load(f)
    ok_a, v_a = L.check_anchor(entries, anchor)
    return {"entries": len(entries),
            "head": entries[-1]["hash"] if entries else None,
            "chain_ok": ok_c, "anchor_ok": ok_a,
            "violations": v_c + v_a,
            "anchor_seq": (anchor or {}).get("seq")}


def render(real, st, bt=None):
    lines = ["# 哈希链账本 · 攻击套件报告", "",
             "> 由 `python3 eval/ledger_chain.py` 生成。链级 9 个用例"
             "（8 招 + 1 个干净基线）、包级 4 个用例（3 招 + 1 个干净基线）。", "",
             "## 一、真实账本", "",
             "| 项 | 值 |", "|---|---|",
             "| 条数 | %d |" % real["entries"],
             "| 链头 | `%s` |" % (str(real["head"])[:32] + "…" if real["head"] else "—"),
             "| 锚点 seq | %s |" % real["anchor_seq"],
             "| 链内自洽 | %s |" % ("✅" if real["chain_ok"] else "❌"),
             "| 锚点一致 | %s |" % ("✅" if real["anchor_ok"] else "❌"),
             ""]
    if real["violations"]:
        lines += ["```"] + ["! " + v for v in real["violations"]] + ["```", ""]
    lines += ["## 二、攻击套件", "",
              "| 用例 | 期望 | 实际 | 判定 |", "|---|---|---|---|"]
    for c in st["cases"]:
        lines.append("| %s | %s | %s | %s |" % (
            c["name"], c["expected"], c["got"], "✅" if c["ok"] else "❌"))
    lines += ["", "**拦住 %d/%d 招；已知盲点 %d 招（期望就是得手）**"
              % (st["blocked"], st["attacks"], st["known_gaps"]), ""]
    if bt and bt.get("cases"):
        lines += ["## 三、包级攻击（离线包本身）", "",
                  "| 用例 | 期望 | 实际 | 判定 |", "|---|---|---|---|"]
        for c in bt["cases"]:
            lines.append("| %s | %s | %s | %s |" % (
                c["name"], c["expected"], c["got"], "✅" if c["ok"] else "❌"))
        lines += ["", "**L1/L2 合计 %d 招，拦住 %d 招**" % (bt["attacks"], bt["blocked"]), ""]
        lines += ["## 四、已知盲点：为什么它必须留在报告里", ""]
    else:
        lines += ["## 三、已知盲点：为什么它必须留在报告里", ""]
    lines += [
              "第 8 招把链和锚点**一起**重写。离线包里没有「链外的真相」，",
              "所以任何自洽性校验都会被一起说服——这不是实现缺陷，是离线校验的边界。", "",
              "能发现它的只有 L2：拿包里的 entry hash 去", "",
              "1. 仓库 `data/ledger.jsonl` 的同一 seq；",
              "2. `data/ledger_anchor.json` 的 head；",
              "3. git 历史（锚点那次 commit 之后，第 seq 条 hash 就不能变了）。", "",
              "三处任一不符即视为篡改。**我们只承诺 L1 能拦住前 7 招**，",
              "不承诺离线包能独自证明历史。", ""]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-report", action="store_true")
    ap.add_argument("--skip-bundle", action="store_true",
                    help="跳过包级攻击（只验链，调试用）")
    args = ap.parse_args()

    st = self_test()
    bt = bundle_attacks() if not args.skip_bundle else {"status": "PASS", "cases": [],
                                                       "attacks": 0, "blocked": 0}
    real = real_ledger()
    if not args.no_report:
        os.makedirs(os.path.dirname(OUT), exist_ok=True)
        with open(OUT, "w", encoding="utf-8") as f:
            f.write(render(real, st, bt))

    overall = "PASS" if (st["status"] == "PASS" and bt["status"] == "PASS"
                        and real["chain_ok"] and real["anchor_ok"]) else "FAIL"
    if args.json:
        print(json.dumps({"status": overall, "self_test": st["status"],
                          "cases": st["total_cases"], "attacks": st["attacks"],
                          "blocked": st["blocked"], "known_gaps": st["known_gaps"],
                          "bundle_attacks": bt["attacks"],
                          "bundle_blocked": bt["blocked"],
                          "bundle_self_test": bt["status"],
                          "entries": real["entries"], "head": real["head"],
                          "chain_ok": real["chain_ok"],
                          "anchor_ok": real["anchor_ok"],
                          "violations": real["violations"][:3]},
                         ensure_ascii=False))
    else:
        print("真实账本 %d 条 ｜ 链 %s ｜ 锚点 %s"
              % (real["entries"], "OK" if real["chain_ok"] else "FAIL",
                 "OK" if real["anchor_ok"] else "FAIL"))
        for c in st["cases"]:
            print("  %s %-34s 期望 %-4s 实际 %s"
                  % ("✅" if c["ok"] else "❌", c["name"], c["expected"], c["got"]))
        print("拦住 %d/%d 招，已知盲点 %d 招" % (st["blocked"], st["attacks"], st["known_gaps"]))
        for c in bt["cases"]:
            print("  %s %s（实际 %s）" % ("✅" if c["ok"] else "❌", c["name"], c["got"]))
        print("包级攻击：L1/L2 共 %d 招，拦住 %d 招" % (bt["attacks"], bt["blocked"]))
    return 0 if overall == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
