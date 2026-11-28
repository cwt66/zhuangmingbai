# -*- coding: utf-8 -*-
"""一次审计的「可复算记录」：打包和记账共用同一份构造逻辑。

## 为什么单独抽出来

2026-11-05 踩过一次：新增 `addon_forecast.py` 后忘了加进打包清单，
审计包里的 `verify.py` 直接 ModuleNotFoundError。
如果「要打哪些文件」在打包脚本和账本脚本里各写一份，这类 bug 会翻倍。

所以文件清单、哈希口径、记录结构只在这里定义一次：
- `scripts/make_bundle.py` 用它生成 `result.json`；
- `scripts/ledger.py` 用它生成账本 payload。

## 记录里放什么、不放什么

放：输入哈希、**每份标准**的哈希、**每个引擎文件**的哈希、判定摘要 digest、
结论摘要（供人读）。
不放：绝对路径、时间戳进入 digest、文件名进入 digest——
否则同一份单据换个名字就对不上，校验器会变成噪声源。
"""
import hashlib
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")

sys.path.insert(0, os.path.join(ROOT, "src"))
import pipeline as P  # noqa: E402

STANDARDS = ["templates_v0.json", "ontology_v0.json", "rules_v0.json",
             "coverage_v0.json", "reference_prices.json", "template_scopes.json"]
CODE = ["pipeline.py", "obligations.py", "pricing_system.py",
        "addon_forecast.py", "doc_gate.py"]


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def git_commit(cwd=None):
    try:
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                           cwd=cwd or ROOT, capture_output=True, text=True)
        return r.stdout.strip() or "no-git"
    except OSError:
        return "no-git"


def build_record(csv_path, label, template, scope, created_at,
                 data_dir=None, src_dir=None):
    """返回 (record, q, res)。record 里的哈希全部当场重算。"""
    data_dir = data_dir or DATA
    src_dir = src_dir or os.path.join(ROOT, "src")

    onto = P.Ontology(os.path.join(data_dir, "ontology_v0.json"))
    with open(os.path.join(data_dir, "templates_v0.json"), encoding="utf-8") as f:
        tpl = json.load(f)
    with open(os.path.join(data_dir, "rules_v0.json"), encoding="utf-8") as f:
        rules = json.load(f)
    with open(os.path.join(data_dir, "coverage_v0.json"), encoding="utf-8") as f:
        cov = json.load(f)

    q = P.build_line_index(P.load_quote(csv_path, label), onto)
    res = P.audit([q], onto, tpl, rules, cov,
                  template_key=template, scope=scope)[q["label"]]

    record = {
        "label": label,
        "template_key": template,
        "scope": scope,
        "created_at": created_at,
        "git_commit": git_commit(),
        "input_sha256": sha256_file(csv_path),
        "standards_sha256": {n: sha256_file(os.path.join(data_dir, n))
                             for n in STANDARDS
                             if os.path.isfile(os.path.join(data_dir, n))},
        "code_sha256": {n: sha256_file(os.path.join(src_dir, n))
                        for n in CODE
                        if os.path.isfile(os.path.join(src_dir, n))},
        "digest": P.result_digest(q, res),
        "summary": {
            "rows": len(q["rows"]), "summary_rows": len(q["summary_rows"]),
            "total": round(q["total"], 2),
            "missing": [{"concept": m["concept"], "tier": m["tier"],
                         "ref_amount": m.get("ref_amount")} for m in res["missing"]],
            "duplicates": [{"concept": d["concept"], "lines": d["lines"],
                            "amount": d["amount"]} for d in res["duplicates"]],
            "risks": [{"rule": x["rule"], "concept": x["concept"],
                       "issue": x["issue"], "line": x.get("line")} for x in res["risks"]],
            "recon": res.get("recon"),
            "pricing": {k: res["pricing"][k] for k in ("system", "label", "confidence")},
            "obligations": [{"id": o["id"], "title": o["title"],
                             "severity": o["severity"], "amount": o["amount"]}
                            for o in res.get("obligations") or []],
        },
    }
    return record, q, res


def ledger_payload(record):
    """账本里存什么：只存「能定位到这份记录」的最小充分集。

    刻意不存整份结论——账本是日志，不是数据库。
    要复算就按 record 里的哈希去还原输入、标准、代码。
    """
    return {
        "input_sha256": record["input_sha256"],
        "standards_digest": _map_digest(record["standards_sha256"]),
        "code_digest": _map_digest(record["code_sha256"]),
        "result_digest": record["digest"],
        "label": record["label"],
        "template_key": record["template_key"],
        "scope": record["scope"],
    }


def ledger_identity(record):
    """链上身份：由**内容**决定，不含显示名。

    同一份 CSV 标签换成「翡翠台」或「feitai_budget」，判定是同一个，
    链上也应该是同一条——否则换个名字就能造出一条新记录。
    """
    return "|".join([
        record["input_sha256"],
        _map_digest(record["standards_sha256"]),
        _map_digest(record["code_sha256"]),
        record["digest"],
    ])


def _map_digest(m):
    body = json.dumps(m, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


VERIFY_TMPL = '''# -*- coding: utf-8 -*-
"""离线校验器：三层校验本审计包。

    python3 verify.py            # 全部检查
    python3 verify.py --quiet    # 只打印结论

退出码：0 = 全部通过；1 = 有任一检查失败

三层：
  L1-a 文件完整性：input.csv / standards/* / src/* 逐字节比对 result.json 记录的 sha256
  L1-b 结论一致：用包内标准与代码重算，digest 必须等于记录值
  L1-c 链内自洽：重算包内账本快照，链头必须等于锚点快照，且本包是链上第 N 条

L2（本脚本做不到，必须靠仓库）：把本包的 entry hash 拿去
`data/ledger.jsonl` 同一 seq、`data/ledger_anchor.json` 的 head、git 历史三处对照。
"""
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "src"))
import pipeline as P

P.DATA = os.path.join(HERE, "standards")     # 强制用包内标准，不读外部仓库
GENESIS = "0" * 64


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def ehash(seq, ts, kind, payload, prev, dedupe_key=None):
    body = {"seq": seq, "ts": ts, "kind": kind, "payload": payload}
    if dedupe_key is not None:
        body["dk"] = dedupe_key
    return hashlib.sha256((canonical(body) + "|" + prev).encode("utf-8")).hexdigest()


def check_map(rec, key, base, fails):
    """逐份文件比对 sha256。少一个文件也算失败——删掉标准同样是篡改。"""
    m = rec.get(key) or {}
    if not m:
        fails.append("%s：记录里没有哈希清单（包不完整）" % key)
        return
    for name, want in sorted(m.items()):
        path = os.path.join(base, name)
        if not os.path.isfile(path):
            fails.append("%s：缺文件 %s" % (key, name))
            continue
        got = sha(path)
        if got != want:
            fails.append("%s：%s 被改动（记录 %s…，实际 %s…）"
                         % (key, name, want[:12], got[:12]))


def _split_n(n):
    k = 1
    while k * 2 < n:
        k *= 2
    return k


def _leaf_hash(d):
    return hashlib.sha256(b"\\x00" + d).digest()


def _node_hash(a, b):
    return hashlib.sha256(b"\\x01" + a + b).digest()


def merkle_root_hex(hashes):
    """RFC 6962 §2.1：叶子 0x00 / 节点 0x01 域分隔，树形由叶子数唯一决定。"""
    def mth(hs):
        if not hs:
            return hashlib.sha256(b"").digest()
        if len(hs) == 1:
            return _leaf_hash(hs[0])
        k = _split_n(len(hs))
        return _node_hash(mth(hs[:k]), mth(hs[k:]))
    return mth([bytes.fromhex(h) for h in hashes]).hex()


def verify_inclusion(proof, expect_root, expect_tree_size):
    """RFC 6962-bis §2.1.3.2 的 fn/sn 位运算验证；返回 None 或错误原因。"""
    try:
        idx, n = proof["leaf_index"], proof["tree_size"]
        leaf, root = bytes.fromhex(proof["leaf"]), bytes.fromhex(proof["root"])
        path = [bytes.fromhex(h) for h in proof["audit_path"]]
        if bytes.fromhex(expect_root) != root:
            return "证明里的 root 与锚点快照不符"
    except (KeyError, ValueError, TypeError):
        return "证明字段不合法"
    if len(leaf) != 32 or len(root) != 32 or any(len(x) != 32 for x in path):
        return "证明里的哈希不是 32 字节"
    if n < 1 or idx < 0 or idx >= n:
        return "leaf_index / tree_size 越界"
    if n != expect_tree_size:
        return "tree_size 与包内账本条数不符（%d vs %d）" % (n, expect_tree_size)
    fn, sn, r = idx, n - 1, _leaf_hash(leaf)
    for sib in path:
        if sn == 0:
            return "审计路径过长"
        if (fn & 1) == 1 or fn == sn:
            r = _node_hash(sib, r)
            while (fn & 1) == 0 and fn != 0:
                fn >>= 1
                sn >>= 1
        else:
            r = _node_hash(r, sib)
        fn >>= 1
        sn >>= 1
    if sn != 0:
        return "审计路径过短"
    if r != root:
        return "由审计路径算出的根与证明里的 root 不符"
    return None


def load_ledger(fails):
    path = os.path.join(HERE, "ledger.jsonl")
    if not os.path.isfile(path):
        return None
    out = []
    with open(path, encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                fails.append("ledger.jsonl 第 %d 行不是合法 JSON" % i)
    return out


def verify_chain(entries, fails):
    for i, e in enumerate(entries):
        want_seq = i + 1
        if e.get("seq") != want_seq:
            fails.append("账本第 %d 条 seq=%r（断号或重排）" % (want_seq, e.get("seq")))
            continue
        want_prev = GENESIS if i == 0 else entries[i - 1]["hash"]
        if e.get("prev") != want_prev:
            fails.append("账本第 %d 条 prev 断链" % want_seq)
        got = ehash(e["seq"], e["ts"], e["kind"], e["payload"], e["prev"],
                    e.get("dedupe_key"))
        if got != e["hash"]:
            fails.append("账本第 %d 条内容被改" % want_seq)


def main():
    quiet = "--quiet" in sys.argv
    with open(os.path.join(HERE, "result.json"), encoding="utf-8") as f:
        rec = json.load(f)
    fails = []

    # --- L1-a 文件完整性 ---
    got = sha(os.path.join(HERE, "input.csv"))
    if got != rec["input_sha256"]:
        fails.append("input.csv 被改动（记录 %s…，实际 %s…）"
                     % (rec["input_sha256"][:12], got[:12]))
    check_map(rec, "standards_sha256", os.path.join(HERE, "standards"), fails)
    check_map(rec, "code_sha256", os.path.join(HERE, "src"), fails)
    if fails:
        # 文件都不完整就直接停：拿缺了标准的包去复算，只会得到一个 traceback，
        # 那不是「校验」，是把错误藏进异常里。
        print("\\n[FAIL] %d 项文件完整性检查未通过：" % len(fails))
        for x in fails:
            print("  - %s" % x)
        print("\\n结论：包内文件与记录不符，后续结论复算已跳过。")
        return 1

    # --- L1-b 结论一致 ---
    onto = P.Ontology(os.path.join(P.DATA, "ontology_v0.json"))
    with open(os.path.join(P.DATA, "templates_v0.json"), encoding="utf-8") as f:
        tpl = json.load(f)
    with open(os.path.join(P.DATA, "rules_v0.json"), encoding="utf-8") as f:
        rules = json.load(f)
    with open(os.path.join(P.DATA, "coverage_v0.json"), encoding="utf-8") as f:
        cov = json.load(f)

    q = P.build_line_index(P.load_quote(os.path.join(HERE, "input.csv"), rec["label"]), onto)
    res = P.audit([q], onto, tpl, rules, cov,
                  template_key=rec["template_key"], scope=rec["scope"])[q["label"]]
    dg = P.result_digest(q, res)
    if dg != rec["digest"]:
        fails.append("复算结论与记录不一致（记录 %s，复算 %s）" % (rec["digest"], dg))

    # --- L1-c 账本自洽 + 锚点 ---
    entries = load_ledger(fails)
    entry = None
    ep = os.path.join(HERE, "ledger_entry.json")
    if os.path.isfile(ep):
        with open(ep, encoding="utf-8") as f:
            entry = json.load(f)
    if entries is not None and entry is not None:
        verify_chain(entries, fails)
        ap = os.path.join(HERE, "anchor_snapshot.json")
        if not os.path.isfile(ap):
            fails.append("缺锚点快照：没有锚点的账本防不了整条重写")
        else:
            with open(ap, encoding="utf-8") as f:
                anchor = json.load(f)
            if len(entries) < anchor["seq"]:
                fails.append("账本快照被截断（%d 条 < 锚点 seq %d）"
                             % (len(entries), anchor["seq"]))
            elif entries[anchor["seq"] - 1]["hash"] != anchor["head"]:
                fails.append("锚点不符：账本可能被整条重写")
        # 本包必须是链上那一条，且 payload 与 result.json 完全对得上
        if entry["seq"] > len(entries):
            fails.append("本包的条目 seq %d 超出账本快照" % entry["seq"])
        else:
            on_chain = entries[entry["seq"] - 1]
            if on_chain["hash"] != entry["hash"]:
                fails.append("本包条目 hash 与账本快照第 %d 条不一致" % entry["seq"])
            pl = entry.get("payload") or {}
            if pl.get("input_sha256") != rec.get("input_sha256"):
                fails.append("账本 payload 的 input_sha256 与 result.json 不一致")
            if pl.get("result_digest") != rec.get("digest"):
                fails.append("账本 payload 的 result_digest 与 result.json 不一致")
            # 标准与代码的整张哈希表也要对得上——否则可以换一套标准再补记录
            for key, plkey in (("standards_sha256", "standards_digest"),
                               ("code_sha256", "code_digest")):
                if pl.get(plkey) != hashlib.sha256(
                        canonical(rec.get(key) or {}).encode("utf-8")).hexdigest():
                    fails.append("账本 payload 的 %s 与 result.json 不一致" % plkey)

            # --- L1-d Merkle 包含证明：只需锚点快照里的 32 字节根 ---
            pp = os.path.join(HERE, "inclusion_proof.json")
            if not os.path.isfile(pp):
                fails.append("缺 inclusion_proof.json（当前版审计包必须带证明）")
            else:
                with open(pp, encoding="utf-8") as f:
                    proof = json.load(f)
                if proof.get("leaf") != entry.get("hash"):
                    fails.append("包含证明的叶子不是本包条目 hash")
                if proof.get("tree_size") != len(entries):
                    fails.append("包含证明的 tree_size 与包内账本条数不符")
                root = anchor.get("merkle_root")
                if not root:
                    fails.append("锚点快照缺 merkle_root")
                else:
                    if merkle_root_hex([e["hash"] for e in entries]) != root:
                        fails.append("包内账本算出的 Merkle 根与锚点快照不符")
                    err = verify_inclusion(proof, root, len(entries))
                    if err:
                        fails.append("包含证明验证失败：%s" % err)

    if not quiet:
        print("单据：%s（%d 行明细 + %d 行汇总）" % (
            rec["label"], len(q["rows"]), len(q["summary_rows"])))
        print("口径：模板 %s ｜ 范围 %s ｜ 生成于 %s ｜ 代码 %s" % (
            rec["template_key"], rec["scope"], rec["created_at"], rec["git_commit"]))
        print("结论：漏项 %d（确定 %d / 复核 %d）｜重复 %d ｜风险 %d ｜明细合计 %s" % (
            len(res["missing"]),
            sum(1 for m in res["missing"] if m["tier"] == "auto"),
            sum(1 for m in res["missing"] if m["tier"] != "auto"),
            len(res["duplicates"]), len(res["risks"]), "%.2f" % q["total"]))
        if res.get("recon") and res["recon"].get("diff") is not None:
            print("对账：印刷合计 %.2f ｜ 差 %.2f ｜ %s" % (
                res["recon"]["grand"]["amount"], res["recon"]["diff"],
                "对平" if res["recon"]["ok"] else "不符"))
        print("校验：输入 + %d 份标准 + %d 个引擎文件 逐字节比对"
              % (len(rec.get("standards_sha256") or {}), len(rec.get("code_sha256") or {})))
        if entries is not None and entry is not None:
            print("账本：包内快照 %d 条，本包是第 %d 条（%s…）"
                  % (len(entries), entry["seq"], entry["hash"][:12]))

    if fails:
        print("\\n[FAIL] %d 项检查未通过：" % len(fails))
        for x in fails:
            print("  - %s" % x)
        print("\\n结论：这个包在离开我们之前就已经不可信，或离开之后被动过。")
        return 1

    print("\\n[PASS] L1 全部通过：输入、标准、代码、结论、账本五处一致")
    if entries is not None and entry is not None:
        print("       本包 = 账本第 %d 条，链头 %s" % (entry["seq"], entries[-1]["hash"][:16]))
    print("       边界：离线只能证明「包内自洽」。要证明「这条记录真的在历史上存在过」，")
    print("       请把上面的 entry hash 与仓库 data/ledger.jsonl、data/ledger_anchor.json")
    print("       以及 git 历史三处对照（L2）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''

BUNDLE_README = """装明白 · 审计包（auditable bundle · 含哈希链账本）
=====================================================

这个包里有什么
--------------
input.csv             被审计的报价单（逐字节冻结）
standards/            当时的全部标准 + 参照价库 + 范围标签
src/                  当时的判定引擎（零第三方依赖）
result.json           判定结论 + 输入/每份标准/每个引擎文件的 sha256 + 结论摘要 digest
ledger.jsonl          生成时刻的账本快照（哈希链，每条带 prev）
ledger_entry.json     本包在链上的那一条
anchor_snapshot.json  生成时刻的链头（外部锚点的快照）
verify.py             离线校验器

怎么验（30 秒）
--------------
    python3 verify.py

看到 [PASS] 就表示这五处一致：输入没改、标准没改、代码没改、结论复算一致、
账本链自洽且与锚点快照对得上。

为什么这次比上一版硬
--------------------
上一版只校验输入哈希。2026-11-09 实测过一次失守：
从 templates_v0.json 删掉 3 个必做项，漏项从 12 掉到 10，
再让包里引擎重算 digest 写回 result.json —— verify.py 照样打印 [PASS]。

现在的三层：
1. 文件完整性：input.csv + 每份标准 + 每个引擎文件，逐个 sha256 比对；
2. 结论一致：用包内标准与代码重算，digest 必须与记录相同；
3. 链内自洽：重算 ledger.jsonl 的哈希链，链头必须等于 anchor_snapshot，
   且本包的条目必须真的在链上第 N 条。

这个包**做不到**什么（必须说清）
--------------------------------
如果把账本和锚点快照**一起**重写，离线校验发现不了 —— 包里没有「链外的真相」。
要发现它，得把 ledger_entry.json 的 hash 拿去仓库 data/ledger.jsonl 的同一 seq、
data/ledger_anchor.json 的 head、以及 git 历史三处对照。
我们只承诺：离线包能独自拦住「改一条 / 删一条 / 换序 / 截尾 / 整条重写链」，
不承诺它能独自证明历史。

这个包不包含什么
----------------
不含 Web 界面与 OCR 感知层——审计结论不依赖它们。包越小，越可能被真的跑一遍。
"""


def _write_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


def _load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def package_bundle(csv_path, label, out_zip, template="old_house", scope="full",
                   ledger_path=None, anchor_path=None, write_ledger=True,
                   attach_readonly_ledger=False, created_at=None):
    """生成离线可复算审计包（.zmb.zip）；CLI 与 Web 共用同一实现。"""
    import shutil
    import tempfile
    import zipfile
    from datetime import datetime
    import ledger as L
    import merkle as MK

    csv_path = os.path.abspath(csv_path)
    created = created_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    record, q, res = build_record(csv_path, label, template, scope, created)

    ledger_path = ledger_path or os.path.join(DATA, "ledger.jsonl")
    anchor_path = anchor_path or os.path.join(DATA, "ledger_anchor.json")
    entry, entries, anchor = None, [], None

    if write_ledger:
        entry, is_new = L.append(
            ledger_path, "audit", ledger_payload(record),
            datetime.now().astimezone().isoformat(timespec="seconds"),
            dedupe_key=ledger_identity(record))
        entries = L.load(ledger_path)
        anchor = _load_json(anchor_path) if os.path.isfile(anchor_path) else None
        if not anchor or anchor.get("seq") != len(entries):
            anchor = {
                "schema": "zmb-ledger-anchor/v1",
                "seq": len(entries),
                "head": entries[-1]["hash"],
                "merkle_root": MK.root_hex([e["hash"] for e in entries]),
                "anchored_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "git_commit": git_commit(),
                "mirrors": {
                    "rds_table": {"status": "not_run", "ddl": "src/ledger.py:RDS_DDL"},
                    "obs_object_lock": {"status": "not_run"},
                },
                "note": "由 make_bundle.py 自动锚定；正式发布前请 commit，锚点不进版本库等于没有锚点。",
            }
            _write_json(anchor_path, anchor)
        if not is_new:
            entry = next((e for e in entries if e["seq"] == entry["seq"]), entry)
    elif attach_readonly_ledger and os.path.isfile(ledger_path) and os.path.isfile(anchor_path):
        entries = L.load(ledger_path)
        anchor = _load_json(anchor_path)
        ident = ledger_identity(record)
        entry = next((e for e in entries if e.get("dedupe_key") == ident), None)

    tmp = tempfile.mkdtemp(prefix="zmb_bundle_")
    try:
        os.makedirs(os.path.join(tmp, "standards"))
        os.makedirs(os.path.join(tmp, "src"))
        shutil.copy2(csv_path, os.path.join(tmp, "input.csv"))
        for n in STANDARDS:
            src = os.path.join(DATA, n)
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(tmp, "standards", n))
        src_dir = os.path.join(ROOT, "src")
        for n in CODE:
            src = os.path.join(src_dir, n)
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(tmp, "src", n))
        _write_json(os.path.join(tmp, "result.json"), record)
        if entry is not None and anchor is not None:
            entry_hashes = [e["hash"] for e in entries]
            proof = MK.make_proof(entry_hashes, entry["seq"] - 1)
            snap = dict(anchor)
            snap["merkle_root"] = MK.root_hex(entry_hashes)
            _write_json(os.path.join(tmp, "inclusion_proof.json"), proof)
            _write_json(os.path.join(tmp, "anchor_snapshot.json"), snap)
            _write_json(os.path.join(tmp, "ledger_entry.json"), entry)
            with open(os.path.join(tmp, "ledger.jsonl"), "w", encoding="utf-8") as f:
                f.write(L.dumps(entries))
        with open(os.path.join(tmp, "verify.py"), "w", encoding="utf-8") as f:
            f.write(VERIFY_TMPL)
        with open(os.path.join(tmp, "README.txt"), "w", encoding="utf-8") as f:
            f.write(BUNDLE_README)
        with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as z:
            for base, _, files in os.walk(tmp):
                for fn in files:
                    full = os.path.join(base, fn)
                    z.write(full, os.path.relpath(full, tmp))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return record, q, res, entry, anchor
