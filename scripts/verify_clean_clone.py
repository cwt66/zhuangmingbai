#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""干净 clone 全量复验：证明「别人 clone 下来也是绿的」，而不是只有本机绿。

## 为什么单独做一个脚本

`data/claim_history_v0.json` 的 H05 记着一次真实的翻车：
冻结基线补到 5/5 之后，本地自检永远是绿的——因为 `.gitignore` 把
`eval/predictions/*` 整目录排除了，**干净 clone 里只有 3 份**，跑起来必红。
「本地有文件」和「仓库里有文件」是两件事，只有真的 clone 一次才分得清。

上一轮靠我手动 clone 到 /tmp 验了一次，但**人总会忘**。所以把它做成脚本，
并接进 GitHub Actions 的独立 job：每次推送都自动从 HEAD 建一个干净 clone，
在里面跑完整 `bash scripts/check_all.sh`，比对 `[ALL GREEN]`。

## 为什么不进 check_all.sh

把「clone 一个自己 → 跑 check_all」塞进 check_all，会无限递归。
所以这条**刻意不进 ① 档主张台账**，也不占一步自检编号；
它由 CI 的第二个 job 保证，并在 `docs/CLAIM-LEDGER.md` 里主动交代这个边界。
脚本自带护栏：若已经在 clone 子进程里（ZMB_IN_CLEAN_CLONE=1）又被调起，
直接报错退出，而不是安静地递归下去。

用法：
    python3 scripts/verify_clean_clone.py            # 人读报告 + 写 out/clean_clone_report.md
    python3 scripts/verify_clean_clone.py --json     # CI 用，一行 JSON
    python3 scripts/verify_clean_clone.py --keep     # 保留临时目录供排查
只用标准库；不需要凭据、不联网。
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "out" / "clean_clone_report.md"
GUARD = "ZMB_IN_CLEAN_CLONE"
TIMEOUT = 900  # 全量自检约 1 分钟，留足 CI 慢机器的余量


def git(args, cwd=ROOT):
    r = subprocess.run(["git"] + list(args), cwd=str(cwd),
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("git %s 失败：%s" % (" ".join(args), r.stderr.strip()))
    return r.stdout.strip()


def self_test():
    """静态双向断言：不跑 clone，只锁住「这套保障不会悄悄消失」。

    真正的干净 clone 复验由 --json / CI job 执行（约 1 分钟）；
    这里查的是防止它被绕过、被删掉、或者被误塞进 check_all 造成递归。
    """
    cases = []

    def case(name, ok, detail=""):
        cases.append({"name": name, "expected": "PASS",
                      "got": "PASS" if ok else "FAIL", "ok": bool(ok),
                      "detail": detail})

    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    case("CI 有独立的 clean-clone job", "clean-clone:" in ci)
    case("该 job 调用 verify_clean_clone.py", "verify_clean_clone.py" in ci)

    sh = (ROOT / "scripts" / "check_all.sh").read_text(encoding="utf-8")
    case("check_all.sh 不调用本脚本（防无限递归）",
         "verify_clean_clone" not in sh)

    # H05 的根因：冻结基线曾被 .gitignore 整目录排除
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    case("冻结基线白名单仍在 .gitignore 里",
         "!eval/predictions/*.json" in ignore,
         "H05：本地 5/5、clone 3/5")

    env = dict(os.environ)
    env[GUARD] = "1"
    r = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--json"],
                       cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=30)
    try:
        payload = json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        payload = {}
    case("护栏：clone 子进程里再次调起 → 拒绝递归",
         r.returncode == 2 and payload.get("status") == "FAIL",
         "exit=%s" % r.returncode)

    return {"status": "PASS" if all(c["ok"] for c in cases) else "FAIL",
            "cases": cases}


def main():
    ap = argparse.ArgumentParser(description="干净 clone 全量复验")
    ap.add_argument("--json", action="store_true", help="只输出一行 JSON")
    ap.add_argument("--keep", action="store_true", help="保留临时 clone 目录")
    ap.add_argument("--source", default=str(ROOT), help="源仓库目录（默认当前仓库）")
    ap.add_argument("--self-test", action="store_true", help="静态双向断言（不跑 clone）")
    args = ap.parse_args()

    if args.self_test:
        st = self_test()
        if args.json:
            print(json.dumps(st, ensure_ascii=False))
        else:
            for c in st["cases"]:
                print("| %s | %s | %s | %s |" %
                      (c["name"], "PASS", c["got"], "✅" if c["ok"] else "❌"))
            print("\n**自检：%s**" % st["status"])
        return 0 if st["status"] == "PASS" else 1

    # 防递归护栏：clone 里的 check_all 若误调本脚本，立刻报错，不要静默套娃
    if os.environ.get(GUARD) == "1":
        msg = ("检测到 %s=1：本脚本已在干净 clone 的子进程里被再次调起，"
               "拒绝递归。它只能由 CI 的独立 job 或人工从仓库根运行。" % GUARD)
        if args.json:
            print(json.dumps({"status": "FAIL", "error": msg}, ensure_ascii=False))
        else:
            print("[FAIL] " + msg)
        return 2

    src = Path(args.source).resolve()
    expected_head = git(["rev-parse", "HEAD"], cwd=src)
    expected_files = git(["-c", "core.quotepath=false", "ls-files"], cwd=src).splitlines()
    expected_n = len(expected_files)
    expected_baselines = [f for f in expected_files
                          if f.startswith("eval/predictions/") and f.endswith(".json")]

    tmp = tempfile.mkdtemp(prefix="zmb_clean_")
    checks, violations = [], []

    def check(name, ok, detail=""):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})
        if not ok:
            violations.append("%s：%s" % (name, detail))

    t0 = time.time()
    try:
        r = subprocess.run(["git", "clone", "--no-local", "--quiet", str(src), tmp],
                           capture_output=True, text=True, timeout=180)
        check("git clone 成功", r.returncode == 0, r.stderr.strip()[:200])
        if r.returncode != 0:
            raise RuntimeError("clone 失败，后续检查跳过")

        clone = Path(tmp)
        got_head = git(["rev-parse", "HEAD"], cwd=clone)
        check("HEAD 与源仓库一致", got_head == expected_head,
              "%s vs %s" % (got_head[:7], expected_head[:7]))

        got_files = git(["-c", "core.quotepath=false", "ls-files"], cwd=clone).splitlines()
        check("跟踪文件数一致", len(got_files) == expected_n,
              "%d vs %d" % (len(got_files), expected_n))
        check("跟踪文件清单逐条一致", got_files == expected_files,
              "差集 %s" % (sorted(set(expected_files) ^ set(got_files))[:5]))

        got_baselines = [f for f in got_files
                         if f.startswith("eval/predictions/") and f.endswith(".json")]
        check("冻结基线全部随 clone 入库", len(got_baselines) == len(expected_baselines) == 5,
              "clone 里 %d 份，期望 5 份（H05 的坑）" % len(got_baselines))

        dirty = git(["status", "--porcelain"], cwd=clone)
        check("clone 初始工作区干净", dirty == "", dirty[:200])

        env = dict(os.environ)
        env[GUARD] = "1"
        run = subprocess.run(["bash", "scripts/check_all.sh"], cwd=str(clone),
                             env=env, capture_output=True, text=True, timeout=TIMEOUT)
        out = (run.stdout or "") + (run.stderr or "")
        elapsed = time.time() - t0
        check("干净 clone 的 check_all 退出码为 0", run.returncode == 0,
              "exit=%s" % run.returncode)
        check("输出包含 [ALL GREEN]", "[ALL GREEN]" in out, "")
        m = re.search(r"Ran (\d+) tests", out)
        check("单元测试全部通过", bool(m) and bool(re.search(r"^OK", out, re.M)),
              ("Ran %s tests / OK" % m.group(1)) if m else "没找到 Ran N tests")
        check("第 16 步主张台账执行", "16/20 主张台账" in out, "")
        check("台账零违规", re.search(r"主张 25 条 / 违规 0", out) is not None, "")
    except (subprocess.TimeoutExpired, RuntimeError, OSError) as exc:
        elapsed = time.time() - t0
        check("执行过程无异常", False, str(exc)[:300])
    finally:
        if not args.keep:
            shutil.rmtree(tmp, ignore_errors=True)
        else:
            print("保留临时目录：%s" % tmp, file=sys.stderr)

    status = "PASS" if not violations else "FAIL"
    report = {
        "status": status,
        "head": expected_head[:7],
        "tracked_files": expected_n,
        "frozen_baselines": len(expected_baselines),
        "elapsed_seconds": round(elapsed, 1),
        "checks": checks,
        "violations": violations,
        "guard": "check_all.sh 刻意不调用本脚本；由 CI 的 clean-clone job 保证",
    }

    md = ["# 干净 clone 全量复验", "",
          "> 目的：证明「别人 clone 下来也是绿的」。本地绿 ≠ 仓库绿（见撤回档案 H05）。",
          "> 由 `scripts/verify_clean_clone.py` 从 HEAD 建干净 clone 后跑完整 `check_all.sh`。", "",
          "**%s** ｜ HEAD `%s` ｜ 跟踪文件 %d ｜ 冻结基线 %d/5 ｜ 耗时 %.1fs" %
          (status, report["head"], expected_n, len(expected_baselines), elapsed), "",
          "| 检查 | 结果 | 说明 |", "|---|---|---|"]
    for c in checks:
        md.append("| %s | %s | %s |" % (c["name"], "✅" if c["ok"] else "❌", c["detail"]))
    md += ["", "## 边界", "",
          "- 本脚本**不进** `check_all.sh`（会无限递归），也不占一步自检编号。",
          "- 因此它**不是** ① 档主张台账的条目，而是 CI 第二个 job 独立保证的。",
          "- 用 `git clone --no-local` 模拟「只有 git 跟踪文件」的状态；",
          "  未跟踪的 `out/`、`.auto-deploy/` 不会混进来。", ""]
    if violations:
        md += ["## 违规", ""] + ["- %s" % v for v in violations]
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(md) + "\n", encoding="utf-8")

    if args.json:
        print(json.dumps({"status": status, "head": report["head"],
                          "tracked_files": expected_n,
                          "frozen_baselines": len(expected_baselines),
                          "elapsed_seconds": round(elapsed, 1),
                          "checks": len(checks),
                          "violations": violations}, ensure_ascii=False))
    else:
        print("\n".join(md))
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
