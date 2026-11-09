# -*- coding: utf-8 -*-
"""L2 校验：拿审计包里的条目，去仓库账本 / 锚点 / git 三处对照。

## 为什么需要 L2

离线 `verify.py` 能拦住：改一条、删一条、换序、截尾、整条重写链。
拦不住：**把链和包内锚点快照一起重写**——2026-11-09 实测过，那种包 L1 会 PASS。

根因是离线包里没有「链外的真相」。L2 就是去找链外的真相：

1. 包里 `ledger_entry.json` 的 hash，必须等于仓库 `data/ledger.jsonl` 第 seq 条的 hash；
2. 仓库账本自己必须链内自洽，且与 `data/ledger_anchor.json` 一致；
3. 锚点那次 commit 之后，第 seq 条 hash 就不能再变——`git log -p` 可查。

只要 1 或 2 不成立，这个包就是「自己重写了一套历史」。
第 3 步是给人核的（git 历史不可能被 `--force` 之外的方式改写，而 force push
会同时影响两个远端），脚本里只提示怎么查，不假装能自动证明。

用法：
    python3 scripts/verify_bundle.py --zip /tmp/x.zmb.zip --run-l1
    python3 scripts/verify_bundle.py --zip /tmp/x.zmb.zip --json
"""
import argparse
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


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def check_bundle(zip_path, ledger_path=LEDGER, anchor_path=ANCHOR, run_l1=False):
    v = []
    checks = 0
    tmp = tempfile.mkdtemp(prefix="zmb_l2_")
    try:
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(tmp)
        ep = os.path.join(tmp, "ledger_entry.json")
        if not os.path.isfile(ep):
            return {"status": "FAIL", "checks": 0,
                    "violations": ["包内没有 ledger_entry.json：这个包没进过账本，无法 L2"]}
        entry = load_json(ep)
        repo = L.load(ledger_path)
        anchor = load_json(anchor_path) if os.path.isfile(anchor_path) else None

        ok_chain, v_chain = L.verify(repo)
        checks += 1
        if not ok_chain:
            v += ["仓库账本本身不通过：" + x for x in v_chain[:2]]

        ok_anchor, v_anchor = L.check_anchor(repo, anchor)
        checks += 1
        if not ok_anchor:
            v += ["仓库锚点不通过：" + x for x in v_anchor[:2]]

        seq = entry.get("seq")
        checks += 1
        if not isinstance(seq, int) or seq < 1 or seq > len(repo):
            v.append("包内条目 seq=%r 超出仓库账本（%d 条）：历史被重写过" % (seq, len(repo)))
        else:
            on_chain = repo[seq - 1]
            checks += 1
            if on_chain.get("hash") != entry.get("hash"):
                v.append("包内条目 hash 在仓库账本第 %d 条找不到（整条可能被重写）" % seq)
            checks += 1
            if L.canonical(on_chain.get("payload")) != L.canonical(entry.get("payload")):
                v.append("包内条目 payload 与仓库账本第 %d 条不一致" % seq)

        l1 = None
        if run_l1:
            r = subprocess.run([sys.executable, os.path.join(tmp, "verify.py"), "--quiet"],
                               cwd=tmp, capture_output=True, text=True)
            l1 = {"rc": r.returncode, "out": (r.stdout or "").strip()[-400:]}
            checks += 1
            if r.returncode != 0:
                v.append("离线 L1 未通过")

        return {"status": "PASS" if not v else "FAIL", "checks": checks,
                "violations": v, "seq": seq, "entry_hash": entry.get("hash"),
                "repo_entries": len(repo), "l1": l1}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip", required=True)
    ap.add_argument("--ledger", default=LEDGER)
    ap.add_argument("--anchor-file", default=ANCHOR)
    ap.add_argument("--run-l1", action="store_true", help="同时跑包内离线校验")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    rep = check_bundle(os.path.abspath(args.zip), args.ledger,
                       args.anchor_file, run_l1=args.run_l1)
    if args.json:
        print(json.dumps(rep, ensure_ascii=False))
    else:
        print("L2：%s（%d 项检查）" % (rep["status"], rep["checks"]))
        print("  包内条目 seq=%s ｜ 仓库账本 %d 条"
              % (rep.get("seq"), rep.get("repo_entries", 0)))
        for x in rep["violations"]:
            print("  ! %s" % x)
        if rep.get("l1"):
            print("  离线 L1 rc=%d" % rep["l1"]["rc"])
        if rep["status"] == "PASS":
            print("  提示：第三步是人核的——`git log --oneline -- data/ledger.jsonl`，")
            print("        锚点那次 commit 之后，这条的 hash 不该再变。")
    return 0 if rep["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
