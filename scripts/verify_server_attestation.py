#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""服务器复验证据：把「一次自检跑绿了」变成可复算的结构化声明。

## 为什么需要它

2026-11-10 服务器首次复验抓到一次**假通过**：git bundle 只含 `main`，
clone 时 remote HEAD 指向不存在的 `master`，工作区是空的，脚本却打印
`✓ T000 完成`、退出码 0。退出码 0 只说明「脚本没崩」，不说明「主张成立」。

同一天，`check_all.sh` 在干净环境里报了 `FAILED (errors=1)`——
但如果不看日志正文，只看「跑过了」三个字，也会被当成复验完成。

这个脚本把复验结论做成**可复算的证据对象**，规则只有一条：
> 每一条公开主张，都必须映射到本次日志的具体行号；日志字节变了，
> 或者任何一条主张在日志里找不到，证据立刻作废。

## 对标（borrowed，不宣称原创）

- **in-toto Statement v1 / SLSA v1.2 provenance**：`subject` 用 sha256 绑定产物，
  `buildDefinition.externalParameters`（命令 / commit）与
  `runDetails.builder`（谁跑的）分开写，`resolvedDependencies` 记录依赖及版本。
- **verification-gate 类 Agent Skill**：只认 fresh evidence、exit code 不算证据、
  禁止「Done!」式完成声明、主张必须逐条映射到输出行。
- **Agent Skills `testing` skill** 的证据形状：lane / given-should-actual / reproduce command。

## 不是什么（边界必须写死）

- 不是 Sigstore / DSSE 签名，**没有第三方不可伪造性**。
- 不是 SLSA L3：builder 是自建 x86_64 服务器，不是隔离的托管 CI。
- `resolvedDependencies` 只有 name/version，**没有 wheel 的 sha256**，
  因此不能证明依赖字节未被替换（这需要 lock + hash 或 Sigstore 验证）。
- `generatedOn` 是证据生成时间，**不是执行时间**；日志里没有可信时间戳，
  所以 `executionTime` 明确写 `not_recorded`，不编造。

只用 Python 标准库；不联网、不启动服务、不需要凭据。

## 双向断言

`--self-test` 里：干净日志必须 PASS，7 种故障注入必须全部 FAIL。
一个永远判 PASS 的校验器和一个永远判 FAIL 的校验器一样没用。
"""

import argparse
import hashlib
import json
import platform
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

STATEMENT_TYPE = "https://in-toto.io/Statement/v1"
PREDICATE_TYPE = "https://zhuangmingbai.local/attestation/server-reverify/v1"

# 每条主张 = 一条能在日志正文里逐行复算的断言。
# pattern 用正则捕获 observed；verify 时重新跑一遍，比对行号与观测值。
CLAIM_SPECS = (
    {
        "id": "C-STEPS-20",
        "desc": "20 步自检全部执行且编号 1..20 齐全",
        "pattern": r"^== (\d+)/20 ",
        "expect": "20 个步骤标记，编号集合恰为 1..20",
        "reproduce": "bash scripts/check_all.sh",
    },
    {
        "id": "C-ALLGREEN",
        "desc": "自检自己判定为全部通过",
        "pattern": r"^\[ALL GREEN\] 全部检查通过。$",
        "expect": "必须出现 ALL GREEN，且不得出现 FAILED / Traceback",
        "reproduce": "bash scripts/check_all.sh",
    },
    {
        "id": "C-UNITTESTS",
        "desc": "单元测试规模",
        "pattern": r"^Ran (\d+) tests in ",
        "expect": "Ran >= 340 tests",
        "reproduce": "python3 -m unittest discover -s tests",
    },
    {
        "id": "C-CLAIMS-LEDGER",
        "desc": "主张台账无违规",
        "pattern": r"^  主张 \d+ 条 / 违规 (\d+) /",
        "expect": "违规 = 0",
        "reproduce": "python3 scripts/claim_ledger.py --self-test --json",
    },
    {
        "id": "C-MERKLE",
        "desc": "哈希链 + Merkle 包含证明全部拦住",
        "pattern": r"Merkle (\d+)/(\d+)",
        "expect": "Merkle 12/12",
        "reproduce": "python3 eval/ledger_chain.py --json",
    },
)

FORBIDDEN = (r"^FAILED \(", r"^Traceback \(most recent call last\):", r"^ERROR: ")

# 公开前的确定性脱敏：只泛化「服务器/账号」形态，不写死任何真实主机名、IP 或账号，
# 也不改任何判定数字。规则公开，任何人都能对同一份原始日志重做变换并比对 sha256。
REDACT_PATTERNS = (
    (re.compile(r"/(?:disk|home|mnt|data)\d*/[A-Za-z0-9_.-]+"), "~"),
    (re.compile(r"\b[A-Za-z0-9_-]*SYS-\d+GP-[A-Za-z0-9_-]+\b"), "<host>"),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "<ip>"),
    (re.compile(r"\buser\d+@"), "<user>@"),
)


def redact_text(text: str) -> str:
    for rx, repl in REDACT_PATTERNS:
        text = rx.sub(repl, text)
    return text


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def _lines(text: str):
    return text.splitlines()


def analyze_log(text: str):
    """从日志正文重新计算所有主张。返回 (claims, violations)。

    claims 里每条都带 evidence_lines（1-based）与 evidence_text；
    verify 时这些字段要和证据文件里存的逐字比对，防止「补一行漂亮结论」。
    """
    lines = _lines(text)
    claims = []
    violations = []

    for spec in CLAIM_SPECS:
        rx = re.compile(spec["pattern"])
        hits = []
        for i, line in enumerate(lines, 1):
            m = rx.search(line)
            if m:
                hits.append({"line": i, "text": line, "groups": list(m.groups())})
        claims.append({
            "id": spec["id"],
            "desc": spec["desc"],
            "expect": spec["expect"],
            "reproduce": spec["reproduce"],
            "hits": hits,
        })

    for bad in FORBIDDEN:
        rx = re.compile(bad)
        for i, line in enumerate(lines, 1):
            if rx.search(line):
                violations.append("第 %d 行出现失败标记：%s" % (i, line.strip()[:100]))

    by_id = {c["id"]: c for c in claims}

    # C-STEPS-20：编号集合必须恰为 1..20，重复/缺号都算失败
    steps = by_id["C-STEPS-20"]["hits"]
    numbers = sorted({int(h["groups"][0]) for h in steps if h["groups"] and h["groups"][0].isdigit()})
    if numbers != list(range(1, 21)):
        violations.append("步骤编号不齐：%s" % numbers)

    if not by_id["C-ALLGREEN"]["hits"]:
        violations.append("缺少 [ALL GREEN] 判定行")

    tests = by_id["C-UNITTESTS"]["hits"]
    if not tests or int(tests[0]["groups"][0]) < 340:
        violations.append("单元测试数 < 340 或未记录：%s" % (tests[0]["groups"] if tests else None))

    ledger = by_id["C-CLAIMS-LEDGER"]["hits"]
    if not ledger or ledger[0]["groups"][0] != "0":
        violations.append("主张台账违规数不为 0：%s" % (ledger[0]["groups"] if ledger else None))

    merkle = by_id["C-MERKLE"]["hits"]
    if not merkle or merkle[0]["groups"] != ["12", "12"]:
        violations.append("Merkle 不是 12/12：%s" % (merkle[0]["groups"] if merkle else None))

    status = "pass" if not violations else "fail"
    for c in claims:
        c["status"] = status
    return claims, violations


def collect_dependencies(deps_dir):
    """从 `pip --target` 产生的 *.dist-info/METADATA 读 name/version。

    刻意不调用 pip：证据生成过程本身不应依赖包管理器的当前状态。
    """
    if not deps_dir:
        return []
    root = Path(deps_dir)
    if not root.is_dir():
        return []
    out = []
    for meta in sorted(root.glob("*.dist-info/METADATA")):
        name = version = None
        for line in meta.read_text(encoding="utf-8", errors="replace").splitlines()[:20]:
            if line.startswith("Name:") and name is None:
                name = line.split(":", 1)[1].strip()
            elif line.startswith("Version:") and version is None:
                version = line.split(":", 1)[1].strip()
            if name and version:
                break
        if name and version:
            out.append({"name": name, "version": version})
    return out


def build_statement(log_path: Path, commit: str, builder: str, deps_dir=None):
    raw = log_path.read_bytes()
    text = raw.decode("utf-8", errors="replace")
    claims, violations = analyze_log(text)
    if violations:
        raise SystemExit("✗ 日志本身就不满足主张，拒绝生成证据：\n  " + "\n  ".join(violations))

    deps = collect_dependencies(deps_dir)
    digest = sha256_bytes(raw)
    return {
        "_type": STATEMENT_TYPE,
        "subject": [{
            "name": "check_all.sh 运行日志",
            "digest": {"sha256": digest},
        }],
        "predicateType": PREDICATE_TYPE,
        "predicate": {
            "buildDefinition": {
                "buildType": PREDICATE_TYPE,
                "externalParameters": {
                    "command": "bash scripts/check_all.sh",
                    "repo_commit": commit,
                    "python": sys.version.split()[0],
                    # 只记系统与架构，不写内核细节 / 主机名 / 账号
                    "platform": "%s %s" % (platform.system(), platform.machine()),
                },
                "resolvedDependencies": deps,
            },
            "runDetails": {
                "builder": {"id": builder},
                "metadata": {
                    "generatedOn": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "executionTime": "not_recorded",
                    "invocationId": sha256_bytes((digest + commit).encode())[:16],
                },
            },
            "claims": claims,
            "claimCount": len(claims),
            "limits": [
                "无 DSSE / Sigstore 签名，不具备第三方不可伪造性",
                "builder 为自建 x86_64 服务器，不是隔离托管 CI，不构成 SLSA L3",
                "resolvedDependencies 只有 name/version，未锁定 wheel 字节",
                "executionTime 未记录；generatedOn 是证据生成时间",
            ],
        },
    }


def verify_statement(stmt, log_path: Path):
    """重新读日志、重算所有主张，逐字段对照证据文件。"""
    checks = []
    violations = []

    def check(name, ok, detail=""):
        # 通过时不写 detail：失败文案若出现在 pass 行会让人误读成「不符」
        checks.append({"name": name, "status": "pass" if ok else "fail",
                       "detail": "" if ok else detail})
        if not ok:
            violations.append("%s：%s" % (name, detail))

    check("statement._type", stmt.get("_type") == STATEMENT_TYPE, str(stmt.get("_type")))
    check("predicateType", stmt.get("predicateType") == PREDICATE_TYPE, str(stmt.get("predicateType")))

    subs = stmt.get("subject") or []
    digest = ((subs[0].get("digest") or {}).get("sha256") if subs else None) or ""
    check("subject.digest 格式", bool(re.fullmatch(r"[0-9a-f]{64}", digest)), digest[:16])
    actual = sha256_file(log_path)
    check("subject.digest == 日志字节", digest == actual,
          "证据 %s / 实际 %s" % (digest[:16], actual[:16]))

    pred = stmt.get("predicate") or {}
    ext = ((pred.get("buildDefinition") or {}).get("externalParameters") or {})
    check("externalParameters.command", ext.get("command") == "bash scripts/check_all.sh", str(ext.get("command")))
    check("externalParameters.repo_commit", bool(re.fullmatch(r"[0-9a-f]{40}", str(ext.get("repo_commit", "")))),
          str(ext.get("repo_commit", ""))[:12])
    check("runDetails.builder.id 非空", bool(((pred.get("runDetails") or {}).get("builder") or {}).get("id")))
    check("声明了 limits", len(pred.get("limits") or []) >= 4)

    fresh_claims, fresh_violations = analyze_log(log_path.read_text(encoding="utf-8", errors="replace"))
    check("日志当前仍满足全部主张", not fresh_violations, "; ".join(fresh_violations[:3]))

    stored = pred.get("claims") or []
    check("claimCount 一致", pred.get("claimCount") == len(stored) == len(fresh_claims),
          "%s / %s / %s" % (pred.get("claimCount"), len(stored), len(fresh_claims)))

    fresh_by_id = {c["id"]: c for c in fresh_claims}
    for c in stored:
        f = fresh_by_id.get(c.get("id"))
        if not f:
            check("主张 %s 存在" % c.get("id"), False, "证据里有、判据里没有")
            continue
        check("主张 %s 逐字一致" % c["id"], c == f, "证据行号/观测值与本次日志不符")
        check("主张 %s 命中证据" % c["id"], bool(c.get("hits")) and c.get("status") == "pass")

    return {
        "ok": not violations,
        "checks": checks,
        "violations": violations,
        "log_sha256": actual,
        "claims": len(stored),
        "dependencies": len((pred.get("buildDefinition") or {}).get("resolvedDependencies") or []),
    }


# --------------------------------------------------------------------------
# 自测：干净必须 PASS，7 种故障必须 FAIL
# --------------------------------------------------------------------------

def _synthetic_log(tests=344, steps=20, green=True, ledger_violations="0", merkle="12/12", failed=False):
    lines = ["== 1/20 语法编译"]
    for i in range(2, steps + 1):
        lines.append("== %d/20 占位步骤" % i)
    lines.append("Ran %d tests in 48.948s" % tests)
    if failed:
        lines.append("FAILED (errors=1, skipped=3)")
    lines.append("  主张 25 条 / 违规 %s / 欠账 3 / 撤回档案 13 条" % ledger_violations)
    lines.append("  账本 6 条 / 链 OK / 锚点 OK / 链 7/7 / 包 4/4 / Merkle %s" % merkle)
    if green:
        lines.append("[ALL GREEN] 全部检查通过。")
    return "\n".join(lines) + "\n"


def self_test():
    import copy
    import tempfile

    results = []
    clean = _synthetic_log()
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "clean.log"
        p.write_text(clean, encoding="utf-8")
        stmt = build_statement(p, "a" * 40, "selftest:builder")
        base = verify_statement(stmt, p)
        results.append({"name": "干净日志", "expect": "pass", "actual": "pass" if base["ok"] else "fail"})
        if not base["ok"]:
            return {"self_test": "FAIL", "results": results, "violations": base["violations"]}

        # bytes 字面量只能含 ASCII；中文日志行先 encode，别写进 b"" 里。
        step_line = "== 7/20 占位步骤\n".encode("utf-8")
        green_line = "[ALL GREEN] 全部检查通过。\n".encode("utf-8")

        def mutated(name, mutate_stmt=None, mutate_log=None):
            s = copy.deepcopy(stmt)
            lp = Path(td) / (name + ".log")
            lp.write_bytes(mutate_log(p.read_bytes()) if mutate_log else p.read_bytes())
            if mutate_stmt:
                mutate_stmt(s)
            v = verify_statement(s, lp)
            results.append({"name": name, "expect": "fail",
                            "actual": "pass" if v["ok"] else "fail"})
            return v["ok"]

        caught = 0
        caught += not mutated("删掉一个步骤标记",
                              mutate_log=lambda b: b.replace(step_line, b""))
        caught += not mutated("删掉 ALL GREEN",
                              mutate_log=lambda b: b.replace(green_line, b""))
        caught += not mutated("测试数改成 0",
                              mutate_log=lambda b: b.replace(b"Ran 344 tests", b"Ran 0 tests"))
        caught += not mutated("塞进 FAILED 标记",
                              mutate_log=lambda b: b.replace(b"[ALL GREEN]", b"FAILED (errors=1)\n[ALL GREEN]"))
        caught += not mutated("日志补一行（digest 失配）",
                              mutate_log=lambda b: b + b"# appended after attestation\n")
        caught += not mutated("篡改主张证据行号",
                              mutate_stmt=lambda s: s["predicate"]["claims"][0]["hits"].append(
                                  {"line": 999, "text": "fake", "groups": ["99"]}))
        caught += not mutated("篡改 subject.digest",
                              mutate_stmt=lambda s: s["subject"][0]["digest"].update({"sha256": "0" * 64}))

    total = len(results) - 1
    ok = (results[0]["actual"] == "pass") and (caught == total) and all(
        r["actual"] == r["expect"] for r in results)

    # 脱敏自锁：抹掉账号路径，但 5 条主张的观测值与违规结论必须逐字不变。
    # 用合成路径做样本，不写任何真实账号或磁盘名
    fake_home = "/disk9/tester99/zmb-tasks/repo"
    raw = clean.replace("== 1/20 语法编译", "== 1/20 语法编译 " + fake_home)
    red = redact_text(raw)
    a_claims, a_v = analyze_log(raw)
    b_claims, b_v = analyze_log(red)

    def sig(claims):
        return [[h["groups"] for h in c["hits"]] for c in claims]

    redaction_ok = (red != raw and fake_home not in red
                    and not a_v and not b_v and sig(a_claims) == sig(b_claims))
    results.append({"name": "脱敏不改判定", "expect": "pass",
                    "actual": "pass" if redaction_ok else "fail"})
    ok = ok and redaction_ok
    return {
        "self_test": "PASS" if ok else "FAIL",
        "clean": results[0]["actual"],
        "injections": total,
        "caught": caught,
        "redaction": "PASS" if redaction_ok else "FAIL",
        "results": results,
        "violations": [] if ok else ["双向断言不完整"],
    }


def main():
    ap = argparse.ArgumentParser(description="服务器复验证据（生成 / 复算 / 自测）")
    ap.add_argument("--self-test", action="store_true", help="双向断言自测")
    ap.add_argument("--redact", action="store_true", help="确定性脱敏日志（公开前）")
    ap.add_argument("--build", action="store_true", help="从日志生成证据")
    ap.add_argument("--verify", action="store_true", help="复算证据")
    ap.add_argument("--log", help="check_all.sh 全量日志路径")
    ap.add_argument("--out", help="--build 输出路径")
    ap.add_argument("--attestation", help="--verify 读取的证据路径")
    ap.add_argument("--commit", default="", help="被复验的 commit（40 位 sha）")
    ap.add_argument("--builder", default="", help="builder.id，如 cwt:hostname:arch")
    ap.add_argument("--deps", help="pip --target 目录，用于 resolvedDependencies")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    a = ap.parse_args()

    if a.self_test:
        r = self_test()
        if a.json:
            print(json.dumps(r, ensure_ascii=False, indent=2))
        else:
            print("双向断言：干净 %s / 注入 %d 招拦 %d / %s" %
                  (r["clean"], r["injections"], r["caught"], r["self_test"]))
            for x in r["results"]:
                print("  [%s] %s" % (x["actual"], x["name"]))
        return 0 if r["self_test"] == "PASS" else 1

    if a.redact:
        if not a.log or not a.out:
            ap.error("--redact 需要 --log 与 --out")
        text = Path(a.log).read_text(encoding="utf-8", errors="replace")
        before, bv = analyze_log(text)
        red = redact_text(text)
        after, av = analyze_log(red)
        sig = lambda cs: [[h["groups"] for h in c["hits"]] for c in cs]
        if bv or av or sig(before) != sig(after):
            raise SystemExit("✗ 脱敏改变了判定证据，拒绝输出")
        out = Path(a.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(red, encoding="utf-8")
        r = {"ok": True, "out": str(out), "sha256": sha256_bytes(red.encode("utf-8")),
             "claims_unchanged": True, "bytes": len(red.encode("utf-8"))}
        print(json.dumps(r, ensure_ascii=False, indent=2) if a.json else
              "✓ 脱敏完成：%s（%d 字节，sha256 %s，判定证据未变）" %
              (out, r["bytes"], r["sha256"][:16]))
        return 0

    if a.build:
        if not a.log or not a.out:
            ap.error("--build 需要 --log 与 --out")
        if not re.fullmatch(r"[0-9a-f]{40}", a.commit):
            ap.error("--commit 必须是 40 位 sha（不编造 commit）")
        if not a.builder:
            ap.error("--builder 必须显式给出（不编造执行者）")
        stmt = build_statement(Path(a.log), a.commit, a.builder, a.deps)
        out = Path(a.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(stmt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        r = {"ok": True, "out": str(out), "log_sha256": stmt["subject"][0]["digest"]["sha256"],
             "claims": stmt["predicate"]["claimCount"],
             "dependencies": len(stmt["predicate"]["buildDefinition"]["resolvedDependencies"])}
        print(json.dumps(r, ensure_ascii=False, indent=2) if a.json else
              "✓ 证据已生成：%s（日志 sha256 %s，主张 %d 条，依赖 %d 个）" %
              (out, r["log_sha256"][:16], r["claims"], r["dependencies"]))
        return 0

    if a.verify:
        if not a.attestation or not a.log:
            ap.error("--verify 需要 --attestation 与 --log")
        stmt = json.loads(Path(a.attestation).read_text(encoding="utf-8"))
        r = verify_statement(stmt, Path(a.log))
        if a.json:
            print(json.dumps(r, ensure_ascii=False, indent=2))
        else:
            print("%s 检查 %d 项 / 违规 %d / 日志 sha256 %s / 依赖 %d 个" %
                  ("✓ PASS" if r["ok"] else "✗ FAIL", len(r["checks"]), len(r["violations"]),
                   r["log_sha256"][:16], r["dependencies"]))
            for v in r["violations"]:
                print("  - " + v)
        return 0 if r["ok"] else 1

    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
