#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把当前版评委复验页同步到 gh-pages，或只校验线上版本。

## 为什么需要这个脚本

2026-11-09 曾误判「Pages 部署源不在本仓库」：`git branch -a` 只显示 main，
`GET /repos/.../pages` 返回 404，于是得出了错误结论。
实际上 `gh-pages` 分支就在同一个仓库里，线上页正是从它发布的——
只是本地没 fetch 过那个分支，Pages API 对未认证请求也不给元数据。

这个脚本把那次的正确路径固定下来：
**fetch gh-pages → 临时 worktree 替换两个文件 → commit → push**。
另外提供 `--check`：只读比对线上 sha256 与仓库当前版，不写任何东西。

## 用法

    python3 scripts/publish_pages.py --check      # 线上 vs deliverables，逐字节
    python3 scripts/publish_pages.py --dry-run    # 准备 worktree，不 commit / push
    python3 scripts/publish_pages.py              # 同步并推送

## 凭据

推送沿用 git 的认证。GitHub 走 443 端口时：
    export GIT_SSH_COMMAND='ssh -i ~/.ssh/<deploy_key> -p 443 -o IdentitiesOnly=yes'
    export GH_PAGES_REMOTE='ssh://git@ssh.github.com:443/cwt66/zhuangmingbai.git'

只用标准库；`--check` 会联网（只读），其余模式需要推送权限。
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REMOTE = os.environ.get("GH_PAGES_REMOTE",
                        "ssh://git@ssh.github.com:443/cwt66/zhuangmingbai.git")
LIVE_URL = "https://cwt66.github.io/zhuangmingbai/verify/"
FETCH_REF = "refs/remotes/gh/gh-pages"

# (仓库内当前版, gh-pages 上的路径)
TARGETS = (
    ("deliverables/装明白_评委复验页.html", "verify/index.html"),
    ("deliverables/装明白_评委复验页_11页.pdf", "verify/装明白_评委复验页_11页.pdf"),
)


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def git(args, cwd=ROOT, check=True):
    r = subprocess.run(["git"] + list(args), cwd=str(cwd),
                       capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError("git %s 失败：%s" % (" ".join(args), r.stderr.strip()[:300]))
    return r


def check_live():
    """只读：拉线上 verify 页，与本地当前版比 sha256。"""
    local = (ROOT / TARGETS[0][0]).read_bytes()
    req = urllib.request.Request(LIVE_URL + "?cb=%d" % os.getpid(),
                                headers={"Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        live = resp.read()
    a, b = sha256_bytes(local), sha256_bytes(live)
    return {"status": "PASS" if a == b else "FAIL",
            "live_url": LIVE_URL, "local_sha256": a, "live_sha256": b,
            "local_bytes": len(local), "live_bytes": len(live)}


def remote_file_sha(rel):
    """读 gh-pages 分支上某个文件的 sha256（不联网，用已 fetch 的 ref）。"""
    r = git(["show", "%s:%s" % (FETCH_REF, rel)], check=False)
    if r.returncode != 0:
        return None
    return sha256_bytes(r.stdout.encode("utf-8"))


def sync(dry_run=False):
    """fetch → worktree 替换 → commit → push。dry_run 只做到替换。"""
    git(["fetch", "-q", REMOTE, "refs/heads/gh-pages:%s" % FETCH_REF])
    tmp = Path(tempfile.mkdtemp(prefix="zmb_pages_"))
    wt = tmp / "wt"
    branch = "pages-sync"
    try:
        git(["worktree", "add", "-q", str(wt), "-B", branch, FETCH_REF])
        changed = []
        for src, dst in TARGETS:
            s = ROOT / src
            if not s.is_file():
                raise SystemExit("缺少当前版文件：%s" % src)
            (wt / dst).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(s, wt / dst)
            changed.append(dst)
        # 旧版 10 页 PDF 若还在，删掉，避免两份并存
        old = wt / "verify" / "装明白_评委复验页_10页.pdf"
        if old.is_file():
            old.unlink()
            changed.append("删除 verify/装明白_评委复验页_10页.pdf")
        git(["add", "-A"], cwd=wt)
        diff = git(["status", "--short"], cwd=wt).stdout.strip()
        if not diff:
            print("gh-pages 已是当前版，无需提交。")
            return {"status": "PASS", "changed": [], "pushed": False}
        print("待提交改动：\n%s" % diff)
        if dry_run:
            print("--dry-run：worktree 保留在 %s（未 commit / push）" % wt)
            return {"status": "DRY_RUN", "changed": changed, "pushed": False,
                    "worktree": str(wt)}
        msg = ("pages: 同步当前版评委复验页\n\n"
               "由 scripts/publish_pages.py 生成；数字口径以仓库 data/claims_v0.json 为准。\n")
        git(["-c", "user.name=cwt66",
             "-c", "user.email=cwt66@users.noreply.github.com",
             "commit", "-q", "-m", msg], cwd=wt)
        git(["push", REMOTE, "%s:gh-pages" % branch], cwd=wt)
        head = git(["rev-parse", "--short", "HEAD"], cwd=wt).stdout.strip()
        print("已推送 %s:gh-pages（%s）" % (branch, head))
        return {"status": "PASS", "changed": changed, "pushed": True, "head": head}
    finally:
        git(["worktree", "remove", "--force", str(wt)], check=False)
        shutil.rmtree(tmp, ignore_errors=True)


def self_test():
    """不联网的静态断言：目标文件在、路径对、gh-pages ref 可读（若已 fetch）。"""
    cases = []
    for src, dst in TARGETS:
        p = ROOT / src
        cases.append({"name": "当前版存在：%s" % src, "ok": p.is_file()})
        cases.append({"name": "目标路径在 verify/ 下：%s" % dst,
                      "ok": dst.startswith("verify/")})
    html = (ROOT / TARGETS[0][0]).read_text(encoding="utf-8")
    cases.append({"name": "当前版是 11 页", "ok": html.count('<section class="slide') == 11})
    cases.append({"name": "当前版不写死 commit hash",
                  "ok": len(__import__("re").findall(r"`[0-9a-f]{7}`", html)) == 0})
    local_sha = sha256_bytes((ROOT / TARGETS[0][0]).read_bytes())
    remote_sha = remote_file_sha(TARGETS[0][1])
    cases.append({"name": "gh-pages 已 fetch 时可比对",
                  "ok": remote_sha is None or len(remote_sha) == 64,
                  "note": "未 fetch 不算失败"})
    return {"status": "PASS" if all(c["ok"] for c in cases) else "FAIL",
            "cases": cases, "local_sha256": local_sha, "remote_sha256": remote_sha}


def main():
    ap = argparse.ArgumentParser(description="同步 / 校验评委复验页的 gh-pages 版本")
    ap.add_argument("--check", action="store_true", help="只比对线上与本地（联网只读）")
    ap.add_argument("--dry-run", action="store_true", help="准备改动但不提交推送")
    ap.add_argument("--self-test", action="store_true", help="不联网的静态断言")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        st = self_test()
        print(json.dumps(st, ensure_ascii=False) if args.json
              else "\n".join("%s %s" % ("✅" if c["ok"] else "❌", c["name"])
                             for c in st["cases"]) + "\n\n自检：%s" % st["status"])
        return 0 if st["status"] == "PASS" else 1
    if args.check:
        rep = check_live()
        print(json.dumps(rep, ensure_ascii=False, indent=2) if args.json
              else "线上 %s\n本地 %s\n%s" % (rep["live_sha256"][:16], rep["local_sha256"][:16],
                                            "一致 ✅" if rep["status"] == "PASS" else "不一致 ❌"))
        return 0 if rep["status"] == "PASS" else 1
    rep = sync(dry_run=args.dry_run)
    print(json.dumps(rep, ensure_ascii=False) if args.json else rep["status"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
