# -*- coding: utf-8 -*-
"""打一个「审计包」：把结论连同复算它所需的一切打包，交给任何第三方离线复现。

## 为什么需要

「可复现」这个说法谁都会讲，但评委真要验的时候会遇到三个问题：
① 数据可能被事后改动；② 规则/模板/本体可能是事后调的；③ 环境不同跑不出同样结果。

审计包把这三件事一次性解决：**输入 + 当时的全部标准 + 当时的代码 + 判定结论 +
账本快照 + 校验脚本**装进一个 zip。任何人解压后运行 `python3 verify.py`：

- 输入、**每一份标准**、**每一个引擎文件**逐字节校验（sha256）；
- 用包里附带的**当时版本**的标准复算，不受仓库后续改动影响；
- 复算结论与包里记录的 digest 比对；
- 重算包内账本快照，与锚点快照比对，并确认本包是链上第 N 条。

## 2026-11-09 修的一次真实失守

上一版 `verify.py` 只校验输入哈希，`result.json` 里虽然记了
`standards_sha256` / `code_sha256`，但**从来没被用过**。
实际攻击：从 `templates_v0.json` 删掉 3 个必做项 → 漏项 12 掉到 10 →
用包里引擎重算新 digest 写回 `result.json` → `verify.py` 照样 `[PASS]`。

修法不是「再补一个 if」，而是三层：文件哈希 + 链式账本 + 外部锚点。

用法：
    python3 scripts/make_bundle.py --csv data/real/feitai_budget.csv --label 翡翠台 \
        --template old_house --scope full --out /tmp/feitai_audit.zmb.zip
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import zipfile
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
import audit_record as AR  # noqa: E402
import ledger as L  # noqa: E402

DATA = AR.DATA
# 兼容别名：清单的单一来源在 src/audit_record.py（打包与账本共用），
# 这里保留同名符号，避免既有测试与调用方失效。
STANDARDS = AR.STANDARDS
CODE = AR.CODE
LEDGER = os.path.join(ROOT, "data", "ledger.jsonl")
ANCHOR = os.path.join(ROOT, "data", "ledger_anchor.json")

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


def load_ledger(fails):
    path = os.path.join(HERE, "ledger.jsonl")
    if not os.path.isfile(path):
        return None
    out = []
    for i, line in enumerate(open(path, encoding="utf-8"), 1):
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
    rec = json.load(open(os.path.join(HERE, "result.json"), encoding="utf-8"))
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
    tpl = json.load(open(os.path.join(P.DATA, "templates_v0.json"), encoding="utf-8"))
    rules = json.load(open(os.path.join(P.DATA, "rules_v0.json"), encoding="utf-8"))
    cov = json.load(open(os.path.join(P.DATA, "coverage_v0.json"), encoding="utf-8"))

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
        entry = json.load(open(ep, encoding="utf-8"))
    if entries is not None and entry is not None:
        verify_chain(entries, fails)
        ap = os.path.join(HERE, "anchor_snapshot.json")
        if not os.path.isfile(ap):
            fails.append("缺锚点快照：没有锚点的账本防不了整条重写")
        else:
            anchor = json.load(open(ap, encoding="utf-8"))
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

README = """装明白 · 审计包（auditable bundle · 含哈希链账本）
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


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True, help="被审计的报价单 CSV")
    ap.add_argument("--label", default=None, help="单据名（默认取文件名）")
    ap.add_argument("--template", default="old_house", choices=["old_house", "new_house"])
    ap.add_argument("--scope", default="full", choices=["full", "base"])
    ap.add_argument("--out", default=None, help="输出 zip 路径")
    ap.add_argument("--ledger", default=LEDGER, help="账本 JSONL")
    ap.add_argument("--anchor-file", default=ANCHOR, help="锚点 JSON")
    ap.add_argument("--no-ledger", action="store_true",
                    help="不写账本（仅用于临时试打包；正式包必须入链）")
    args = ap.parse_args()

    csv_path = os.path.abspath(args.csv)
    label = args.label or os.path.splitext(os.path.basename(csv_path))[0]
    out = args.out or os.path.join("/tmp", "%s_audit.zmb.zip" % label)
    created = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    record, q, res = AR.build_record(csv_path, label, args.template,
                                     args.scope, created)

    entry = None
    entries = []
    anchor = None
    if not args.no_ledger:
        entry, is_new = L.append(args.ledger, "audit", AR.ledger_payload(record),
                                 datetime.now().astimezone().isoformat(timespec="seconds"),
                                 dedupe_key=AR.ledger_identity(record))
        entries = L.load(args.ledger)
        anchor = load_json(args.anchor_file) if os.path.isfile(args.anchor_file) else None
        if not anchor or anchor.get("seq") != len(entries):
            anchor = {
                "schema": "zmb-ledger-anchor/v1",
                "seq": len(entries),
                "head": entries[-1]["hash"],
                "anchored_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "git_commit": AR.git_commit(),
                "mirrors": {
                    "rds_table": {"status": "not_run", "ddl": "src/ledger.py:RDS_DDL"},
                    "obs_object_lock": {"status": "not_run"},
                },
                "note": "由 make_bundle.py 自动锚定；正式发布前请 commit，锚点不进版本库等于没有锚点。",
            }
            write_json(args.anchor_file, anchor)
        if not is_new:
            entry = next((e for e in entries
                          if e["seq"] == entry["seq"]), entry)

    tmp = tempfile.mkdtemp(prefix="zmb_bundle_")
    try:
        os.makedirs(os.path.join(tmp, "standards"))
        os.makedirs(os.path.join(tmp, "src"))
        shutil.copy2(csv_path, os.path.join(tmp, "input.csv"))
        for n in AR.STANDARDS:
            src = os.path.join(DATA, n)
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(tmp, "standards", n))
        for n in AR.CODE:
            src = os.path.join(ROOT, "src", n)
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(tmp, "src", n))
        write_json(os.path.join(tmp, "result.json"), record)
        with open(os.path.join(tmp, "verify.py"), "w", encoding="utf-8") as f:
            f.write(VERIFY_TMPL)
        with open(os.path.join(tmp, "README.txt"), "w", encoding="utf-8") as f:
            f.write(README)
        if entry is not None:
            write_json(os.path.join(tmp, "ledger_entry.json"), entry)
            with open(os.path.join(tmp, "ledger.jsonl"), "w", encoding="utf-8") as f:
                f.write(L.dumps(entries))
            write_json(os.path.join(tmp, "anchor_snapshot.json"), anchor)

        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for base, _, files in os.walk(tmp):
                for fn in files:
                    full = os.path.join(base, fn)
                    z.write(full, os.path.relpath(full, tmp))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("审计包：%s（%.0f KB）" % (out, os.path.getsize(out) / 1024))
    print("  单据 %s｜模板 %s｜范围 %s" % (label, args.template, args.scope))
    print("  明细 %d 行 + 汇总 %d 行｜漏项 %d｜重复 %d｜风险 %d" % (
        len(q["rows"]), len(q["summary_rows"]), len(res["missing"]),
        len(res["duplicates"]), len(res["risks"])))
    print("  digest %s" % record["digest"])
    if entry is not None:
        print("  账本 seq=%d ｜ 链头 %s…" % (entry["seq"], anchor["head"][:16]))
    print("  验证：unzip %s -d /tmp/v && python3 /tmp/v/verify.py" % os.path.basename(out))


if __name__ == "__main__":
    main()
