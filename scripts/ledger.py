# -*- coding: utf-8 -*-
"""哈希链账本 CLI：追加 / 校验 / 锚定。

用法：
    python3 scripts/ledger.py seed                 # 把 data/real/*.csv 都记一遍（幂等）
    python3 scripts/ledger.py append --csv x.csv --label 某某
    python3 scripts/ledger.py verify --json        # 链 + 锚点
    python3 scripts/ledger.py anchor               # 把当前链头写进 data/ledger_anchor.json
    python3 scripts/ledger.py show --limit 5

为什么锚点要单独一步、而且写进仓库文件：
哈希链只能防「改一条」。把整条链连同记录一起重写，链内依然自洽。
唯一的破法是让链头出现在链外——这里落在 `data/ledger_anchor.json`，
它随 git 提交、被主张台账 C24 反查、并被 CI 每次推送校验。
攻击者要改，就得同时改链、改锚点、改 git 历史，还要让两个远端都接受。
"""
import argparse
import glob
import json
import os
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
import audit_record as AR  # noqa: E402
import ledger as L  # noqa: E402

LEDGER = os.path.join(ROOT, "data", "ledger.jsonl")
ANCHOR = os.path.join(ROOT, "data", "ledger_anchor.json")


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def load_anchor(path=ANCHOR):
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_anchor(entries, path=ANCHOR, git_commit=None):
    head = entries[-1]
    doc = {
        "schema": "zmb-ledger-anchor/v1",
        "seq": head["seq"],
        "head": head["hash"],
        "anchored_at": now(),
        "git_commit": git_commit or AR.git_commit(),
        # 云端镜像：代码与 DDL 就绪，实例未开通。这里如实写 not_run，
        # 不给「已上云」留想象空间。
        "mirrors": {
            "rds_table": {"status": "not_run",
                          "ddl": "src/ledger.py:RDS_DDL",
                          "why": "实例未开通；表结构已就绪，写入路径复用同一 canonical 序列化"},
            "obs_object_lock": {"status": "not_run",
                                "why": "OBS AK 于 2026-11-07 失效；锚点当前落仓库文件 + git 历史"},
        },
        "note": ("锚点只保证「第 seq 条 hash 等于 head」。整条链是否被重写，"
                 "要比对仓库 data/ledger.jsonl 与 git 历史；离线包内只能做到自洽 + 本锚点快照。"),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return doc


def verify(ledger_path=LEDGER, anchor_path=ANCHOR):
    entries = L.load(ledger_path)
    ok_chain, v_chain = L.verify(entries)
    anchor = load_anchor(anchor_path)
    ok_anchor, v_anchor = L.check_anchor(entries, anchor)
    violations = v_chain + v_anchor
    return {
        "status": "PASS" if (ok_chain and ok_anchor) else "FAIL",
        "entries": len(entries),
        "head": entries[-1]["hash"] if entries else None,
        "anchor_seq": (anchor or {}).get("seq"),
        "anchor_head": (anchor or {}).get("head"),
        "chain_ok": ok_chain,
        "anchor_ok": ok_anchor,
        "violations": violations,
    }


def is_quote_csv(path):
    """报价单的硬契约：必须有数量与单价。只有「项目名称」的是术语映射表。

    data/real/ 里既有 6 份真实单据，也有外部标准清单和术语映射。
    靠文件名区分太脆，所以按**表头契约**判：没有数量/单价就不是报价单。
    """
    import csv
    with open(path, encoding="utf-8-sig") as f:
        head = next(csv.reader(f), [])
    names = {h.strip() for h in head}
    has = lambda *alts: any(a in names for a in alts)  # noqa: E731
    return has("数量", "工程量") and has("单价", "综合单价")


def cmd_seed(args):
    files = sorted(glob.glob(os.path.join(AR.DATA, "real", "*.csv")))
    if not files:
        print("data/real/ 下没有 CSV")
        return 1
    added = skipped = not_quote = 0
    for f in files:
        label = os.path.splitext(os.path.basename(f))[0]
        if not is_quote_csv(f):
            not_quote += 1
            print("  - %-22s 不是报价单，跳过（表头缺数量/单价）" % label)
            continue
        try:
            rec, _, _ = AR.build_record(f, label, args.template, args.scope, now())
        except Exception as e:  # noqa: BLE001
            not_quote += 1
            print("  - %-22s 不是报价单，跳过（%s）"
                  % (label, str(e).split("｜")[0][:52]))
            continue
        e, is_new = L.append(args.ledger, "audit", AR.ledger_payload(rec), now(),
                        dedupe_key=AR.ledger_identity(rec))
        if is_new:
            added += 1
            print("  + seq=%-3d %-22s digest=%s" % (e["seq"], label, rec["digest"]))
        else:
            skipped += 1
            print("  = seq=%-3d %-22s 已在链上，跳过" % (e["seq"], label))
    print("追加 %d 条，已存在 %d 条，非报价单跳过 %d 个，当前共 %d 条"
          % (added, skipped, not_quote, len(L.load(args.ledger))))
    return 0


def cmd_append(args):
    label = args.label or os.path.splitext(os.path.basename(args.csv))[0]
    rec, _, _ = AR.build_record(os.path.abspath(args.csv), label,
                                args.template, args.scope, now())
    e, is_new = L.append(args.ledger, "audit", AR.ledger_payload(rec), now(),
                         dedupe_key=AR.ledger_identity(rec), force=args.force)
    print("%s seq=%d digest=%s hash=%s"
          % ("追加" if is_new else "已存在", e["seq"], rec["digest"], e["hash"][:16]))
    return 0


def cmd_verify(args):
    rep = verify(args.ledger, args.anchor_file)
    if args.json:
        print(json.dumps(rep, ensure_ascii=False))
    else:
        print("账本：%s（%d 条）" % (rep["status"], rep["entries"]))
        print("  链内自洽 %s ｜ 锚点 %s（seq=%s）"
              % ("OK" if rep["chain_ok"] else "FAIL",
                 "OK" if rep["anchor_ok"] else "FAIL", rep["anchor_seq"]))
        print("  链头 %s" % (rep["head"] or "—"))
        for x in rep["violations"]:
            print("  ! %s" % x)
    return 0 if rep["status"] == "PASS" else 1


def cmd_anchor(args):
    entries = L.load(args.ledger)
    ok, v = L.verify(entries)
    if not ok:
        print("链本身不通过，拒绝锚定：")
        for x in v:
            print("  ! %s" % x)
        return 1
    doc = write_anchor(entries, args.anchor_file, git_commit=args.git_commit)
    print("锚定 seq=%d head=%s… → %s"
          % (doc["seq"], doc["head"][:16], os.path.relpath(args.anchor_file, ROOT)))
    print("  记得 commit：锚点不进版本库，就等于没有锚点。")
    return 0


def cmd_show(args):
    entries = L.load(args.ledger)
    for e in entries[-args.limit:]:
        p = e.get("payload") or {}
        print("seq=%-3d %-8s %-20s digest=%-18s %s"
              % (e["seq"], e["kind"], str(p.get("label"))[:20],
                 str(p.get("result_digest"))[:18], e["hash"][:12]))
    print("共 %d 条" % len(entries))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", default=LEDGER)
    ap.add_argument("--anchor-file", default=ANCHOR)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("seed")
    s.add_argument("--template", default="old_house")
    s.add_argument("--scope", default="full")
    s.set_defaults(func=cmd_seed, template="old_house", scope="full")

    s = sub.add_parser("append")
    s.add_argument("--csv", required=True)
    s.add_argument("--label", default=None)
    s.add_argument("--template", default="old_house")
    s.add_argument("--scope", default="full")
    s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_append)

    s = sub.add_parser("verify")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_verify)

    s = sub.add_parser("anchor")
    s.add_argument("--git-commit", default=None)
    s.set_defaults(func=cmd_anchor)

    s = sub.add_parser("show")
    s.add_argument("--limit", type=int, default=5)
    s.set_defaults(func=cmd_show)

    args = ap.parse_args()
    if args.cmd == "verify":
        pass
    elif not hasattr(args, "template"):
        args.template = "old_house"
        args.scope = "full"
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
