#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Linux aarch64 原生实测记录：把「鲲鹏同生态」从推论再往前推一格。

## 为什么做这个

前几轮把「ARM64 判定确定性」做成了可复算证据：macOS arm64 采集存档，
x86_64 Linux CI 当场比对。但 macOS 的 arm64 不是鲲鹏的运行时环境——
鲲鹏跑的是 **Linux + glibc**，openEuler 也是。

GitHub 从 2026-10 起对公开仓库免费提供原生 arm64 Linux runner
（`ubuntu-24.04-arm`，4C/16G）。这不是 QEMU 模拟，是真的 aarch64 Linux。
所以我们可以在**同一操作系统家族 + 同一指令集**上跑一遍判定链路，
把「同为 ARM64 理论上能跑」变成「Linux aarch64 上真的跑过」。

## 这份记录证明什么、不证明什么

证明：某一次 GitHub Actions run（job `aarch64-linux`，runner `ubuntu-24.04-arm`）
在原生 aarch64 Linux 上跑通了判定链路，workflow 结论 success。
不证明：鲲鹏物理机、openEuler 发行版、昇腾 NPU —— 这三条仍是 ③ 档欠账。

## 为什么用「run 记录」而不是二进制存档

CI 跑在 GitHub 的机器上，本地拿不到它的文件系统快照；
但 `GET /repos/.../actions/runs/<id>` 是**公开只读**的，任何人可核对。
所以这里存的是 run_id + head_sha，判据当场查 API 验证 conclusion。
这是 ② 档读档：值来自命令生成的产物，不是本地重算。

用法：
    python3 scripts/verify_aarch64.py --check       # 联网只读校验（本地 ② 档）
    python3 scripts/verify_aarch64.py --self-test   # 不联网的 7 例双向断言
"""

import argparse
import json
import os
import platform
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "data" / "aarch64_linux_v0.json"
MACOS_ARCHIVE = ROOT / "data" / "determinism_arm64_v0.json"
SCHEMA = "zmb-aarch64-linux/v1"
RUN_API = "https://api.github.com/repos/cwt66/zhuangmingbai/actions/runs/%s"
EXPECT_RUNNER = "ubuntu-24.04-arm"
EXPECT_JOB = "aarch64-linux"
REQUIRED_DECLARES = ("鲲鹏实例未实测", "openEuler 未实测", "昇腾 NPU 未涉及")


def load(path):
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def evaluate(doc, run, macos):
    """纯函数：给存档、GitHub run 记录、macOS 存档，判 PASS / FAIL。"""
    v = []
    checks = 0
    if not doc:
        return {"status": "FAIL", "violations": ["缺少 data/aarch64_linux_v0.json"],
                "checks": 0}
    if doc.get("schema") != SCHEMA:
        v.append("schema 不是 %s" % SCHEMA)
    checks += 1
    if doc.get("runner") != EXPECT_RUNNER:
        v.append("runner 不是 %s，实际 %r" % (EXPECT_RUNNER, doc.get("runner")))
    checks += 1
    if doc.get("job") != EXPECT_JOB:
        v.append("job 不是 %s，实际 %r" % (EXPECT_JOB, doc.get("job")))
    checks += 1
    if not isinstance(doc.get("run_id"), int):
        v.append("run_id 必须是整数（实际 %r）" % doc.get("run_id"))
    checks += 1
    for d in REQUIRED_DECLARES:
        if d not in (doc.get("declares") or []):
            v.append("缺少边界声明「%s」——不许拿这份记录换适配认证" % d)
    checks += 1
    if not macos or not macos.get("digest"):
        v.append("缺少 macOS arm64 存档，无法比对判定指纹")
    else:
        checks += 1
        if doc.get("digest") != macos.get("digest"):
            v.append("aarch64 判定指纹与 macOS 存档不一致：%r ≠ %r" %
                     (doc.get("digest"), macos.get("digest")))
    if run is None:
        v.append("没有拿到 GitHub run 记录（网络失败或 run_id 无效）")
        return {"status": "FAIL", "violations": v, "checks": checks}
    checks += 1
    if run.get("conclusion") != "success":
        v.append("run conclusion 不是 success，实际 %r" % run.get("conclusion"))
    checks += 1
    sha = (run.get("head_sha") or "")[:7]
    if not sha or not doc.get("head_sha", "").startswith(sha):
        v.append("run head_sha %r 与存档 head_sha %r 不匹配" %
                 (sha, doc.get("head_sha")))
    checks += 1
    if run.get("name") not in ("ci", "CI"):
        v.append("run 不是 ci workflow，实际 %r" % run.get("name"))
    return {"status": "FAIL" if v else "PASS", "violations": v, "checks": checks}


def fetch_run(run_id):
    req = urllib.request.Request(RUN_API % run_id,
                                 headers={"Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, ValueError, OSError) as e:
        print("查询 run 失败：%s" % e, file=sys.stderr)
        return None


def capture():
    """在原生 aarch64 Linux 上采集证据，并把结果写进 GitHub job annotation。

    为什么用 annotation：Actions 日志需要登录才能看，而
    `GET /check-runs/<id>/annotations` 是公开只读的。把 arch/libc/digest
    写进 annotation，评委和判据都能不登录核对。
    """
    system, machine = platform.system(), platform.machine()
    if system != "Linux" or machine not in ("aarch64", "arm64"):
        print("拒绝采集：需要 Linux aarch64，实际 %s/%s" % (system, machine))
        return 2
    r = subprocess.run([sys.executable, "scripts/audit_one.py",
                        "data/real/feitai_budget.csv", "--json"],
                       cwd=str(ROOT), capture_output=True, text=True)
    try:
        d = json.loads(r.stdout)
    except (ValueError, TypeError):
        print("audit_one 未输出可解析 JSON")
        return 1
    libc = "_".join([x for x in platform.libc_ver() if x]) or "unknown"
    msg = ("arch=%s system=%s libc=%s python=%s digest=%s verdict=%s "
           "missing_cost=%s" % (machine, system, libc, platform.python_version(),
                                d.get("digest"), d.get("verdict"),
                                d.get("missing_cost")))
    print(msg)
    print("::notice title=aarch64-evidence::" + msg)
    # 同时写到 step summary，网页上无需登录即可看到
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write("### aarch64 evidence\n\n`%s`\n" % msg)
    return 0


def self_test():
    """干净必须 PASS；7 种「把记录写成适配认证」的做法必须 FAIL。"""
    macos = {"digest": "52bf7b635204fd96"}
    good = {"schema": SCHEMA, "runner": EXPECT_RUNNER, "job": EXPECT_JOB,
            "run_id": 123456, "head_sha": "abcdef1234567",
            "digest": "52bf7b635204fd96", "declares": list(REQUIRED_DECLARES)}
    run = {"name": "ci", "conclusion": "success", "head_sha": "abcdef1234567890"}
    cases = []

    def case(name, doc, r, expect):
        got = evaluate(doc, r, macos)["status"]
        cases.append({"name": name, "expected": expect, "got": got,
                      "ok": got == expect})

    case("干净记录 → PASS", good, run, "PASS")
    case("删掉存档 → FAIL", None, run, "FAIL")
    case("runner 写成 ubuntu-latest → FAIL", dict(good, runner="ubuntu-latest"), run, "FAIL")
    case("run 失败 → FAIL", good, dict(run, conclusion="failure"), "FAIL")
    case("head_sha 不匹配 → FAIL", good, dict(run, head_sha="0000000"), "FAIL")
    case("指纹与 macOS 存档不一致 → FAIL", dict(good, digest="deadbeefdeadbeef"), run, "FAIL")
    case("抹掉「鲲鹏实例未实测」→ FAIL",
         dict(good, declares=["openEuler 未实测", "昇腾 NPU 未涉及"]), run, "FAIL")
    return {"status": "PASS" if all(c["ok"] for c in cases) else "FAIL",
            "cases": cases}


def render(rep, doc, selftest=None):
    lines = ["# Linux aarch64 原生实测记录", "",
             "> GitHub 托管 `ubuntu-24.04-arm`（原生 aarch64 Linux，不是 QEMU）。",
             "> 判据当场查 `GET /repos/.../actions/runs/<id>` 核对 conclusion 与 head_sha。", "",
             "**%s** ｜ %d 项检查" % (rep["status"], rep["checks"]), ""]
    if doc:
        lines += ["| 项 | 值 |", "|---|---|",
                  "| runner | `%s` |" % doc.get("runner"),
                  "| job | `%s` |" % doc.get("job"),
                  "| run_id | `%s` |" % doc.get("run_id"),
                  "| head_sha | `%s` |" % doc.get("head_sha"),
                  "| 判定指纹 | `%s` |" % doc.get("digest"), ""]
    lines += ["## 边界", ""]
    for d in (doc or {}).get("declares", []):
        lines.append("- **%s**：本记录不改变这条口径。" % d)
    lines += ["- 记录的是**某一次 run** 在原生 aarch64 Linux 上跑通；"
              "不是鲲鹏物理机，也不是 openEuler 发行版。",
              "- 判据联网只读；CI 上不跑它，避免限流。", ""]
    if rep["violations"]:
        lines += ["## 违规", ""] + ["- %s" % x for x in rep["violations"]] + [""]
    if selftest:
        lines += ["## 双向断言", "", "| 故障注入 | 期望 | 实际 | 通过 |",
                  "|---|---|---|---|"]
        for c in selftest["cases"]:
            lines.append("| %s | %s | %s | %s |" %
                         (c["name"], c["expected"], c["got"], "✅" if c["ok"] else "❌"))
        lines += ["", "**自检：%s**" % selftest["status"], ""]
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(description="Linux aarch64 原生实测记录")
    ap.add_argument("--check", action="store_true", help="联网只读校验（默认）")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-report", action="store_true")
    ap.add_argument("--capture", action="store_true",
                    help="在 Linux aarch64 上采集证据并写 annotation")
    args = ap.parse_args()
    if args.capture:
        return capture()

    st = self_test() if args.self_test else None
    doc = load(ARCHIVE)
    run = fetch_run(doc["run_id"]) if doc and isinstance(doc.get("run_id"), int) else None
    rep = evaluate(doc, run, load(MACOS_ARCHIVE))
    if st:
        rep["self_test"] = st["status"]
    if not args.no_report and not args.self_test:
        out = ROOT / "out" / "aarch64_linux.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render(rep, doc, st), encoding="utf-8")
    if args.json:
        # --self-test 模式下，status 只反映双向断言本身；
        # 缺存档是「还没采集」，不该让自检模式红。
        status = st["status"] if (args.self_test and st) else rep["status"]
        print(json.dumps({"status": status, "run_id": (doc or {}).get("run_id"),
                          "violations": [] if args.self_test else rep["violations"][:5],
                          "self_test": st["status"] if st else "not_run"},
                         ensure_ascii=False))
    elif st:
        for c in st["cases"]:
            print("%s %s（%s / %s）" % ("✅" if c["ok"] else "❌", c["name"],
                                       c["got"], c["expected"]))
        print("\n自检：%s" % st["status"])
    else:
        print(render(rep, doc, st))
    bad = ((st["status"] != "PASS") if (args.self_test and st)
           else (rep["status"] != "PASS"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
