#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""跨环境判定确定性矩阵：同一份报价单，换环境也不许换结论。

## 为什么做这个

前面几个创新点分别回答了「结果可被复核」「规则可被证伪」「说过的话可被查」
「云依赖挂了会怎样」。但还有一句一直只是**推理**：判定引擎是纯标准库，
所以换台机器、换个 Python、换个 locale 应该也是同一个结果。

这句话在国产化语境里尤其值钱：`docs/华为云IaC实测与硬件边界.md` 里
「鲲鹏 ARM ECS 未实测」是主动交代的欠账（台账 C18，③ 档声明）。
我们不能把「ARM64 理论上能跑」包装成「已适配鲲鹏」，但可以把它收敛成
**一条能被任何人复算的证据**：在 ARM64/aarch64 上采集判定指纹存档，
再让 x86_64 的 GitHub Actions（多个 Python 版本）当场算出同一个指纹。

方法来自 GitHub 上两个可核对的参照：
- `FlorianStuettgen/SOC_Replay` docs/27-Reproducible-Builds.md：
  干净源树两次独立执行、固定 SOURCE_DATE_EPOCH / PYTHONHASHSEED、
  比对完整 SHA-256、失败时保留产物；**并主动写明可复现构建证明不了什么**。
- `MEP-51` Cross-target byte-equal differential gate：
  跨 x86_64 / aarch64 / macOS-arm64 / Windows 比对输出 SHA-256，
  并逐条列出历史上真正导致过分歧的原因（hash(str) 随机种子、
  set 迭代顺序、locale 排序、float 打印、路径分隔符、时间字段）。

所以这里把那些「导致过分歧的原因」逐项注入一遍，看判定指纹会不会动。

## 三条设计纪律

1. **不靠设一个固定种子糊过去**：三个 PYTHONHASHSEED 全试，而不是只跑 seed=0。
2. **不把「同环境重跑」冒充「跨环境」**：报告里写清当前指令集 / Python 版本
   与存档是否不同；CI 的 x86_64 与存档 arm64 不同，才算跨指令集比对。
3. **不拿这份证据换「已适配鲲鹏」的绿勾**：存档自带 `declares` 边界声明，
   少一条判据就 FAIL。C18 保持 ③ 档声明，不升级。

用法：
    python3 eval/determinism_matrix.py                  # 人读报告 + out/determinism_matrix.md
    python3 eval/determinism_matrix.py --self-test --json  # 自检 / CI / 台账探针
    python3 eval/determinism_matrix.py --capture         # 重新采集 ARM64 存档（只在 arm64 机器上允许）
只用标准库；不联网、不要凭据、不连真云。
"""

import argparse
import json
import os
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = "data/real/feitai_budget.csv"
ARCHIVE = ROOT / "data" / "determinism_arm64_v0.json"
OUT = ROOT / "out" / "determinism_matrix.md"
FAKE_DEPS = ROOT / "tests" / "fixtures" / "fake_cloud_deps"

# 判定结果里参与逐字节比对的字段。刻意不含 label/template 等输入描述，
# 因为我们要比的是「结论」，不是「输入」。
FIELDS = ("digest", "verdict", "missing_cost", "duplicate_cut", "rows", "total")

# (id, 维度, 环境变量, python 前置参数, 注入说明)
VARIANTS = (
    ("baseline", "基准", {}, [], "当前环境原样，作为本次比对基准"),
    ("seed_0", "哈希种子", {"PYTHONHASHSEED": "0"}, [], "固定 seed=0"),
    ("seed_1", "哈希种子", {"PYTHONHASHSEED": "1"}, [], "固定 seed=1（换一个种子）"),
    ("seed_random", "哈希种子", {"PYTHONHASHSEED": "random"}, [], "每次进程随机种子"),
    ("tz_utc", "时区", {"TZ": "UTC"}, [], "UTC"),
    ("tz_shanghai", "时区", {"TZ": "Asia/Shanghai"}, [], "Asia/Shanghai"),
    ("locale_c", "locale", {"LC_ALL": "C", "LANG": "C"}, [], "C locale"),
    ("locale_zh", "locale", {"LC_ALL": "zh_CN.UTF-8", "LANG": "zh_CN.UTF-8"}, [],
     "中文 UTF-8 locale"),
    ("utf8_off", "编码", {"PYTHONUTF8": "0"}, [], "关闭 UTF-8 mode"),
    ("optimize_2", "优化级别", {"PYTHONOPTIMIZE": "2"}, [], "-O 等价，剥掉断言与 docstring"),
    ("fake_cloud_deps", "可选依赖", {"__FAKE_DEPS__": "1"}, [],
     "PYTHONPATH 注入假 psycopg/redis（模拟 CI 装了驱动但服务不可达）"),
    ("no_site_packages", "零第三方包", {}, ["-S"],
     "python -S：完全不加载 site-packages"),
)

EXPECTED_IDS = tuple(v[0] for v in VARIANTS)
ARCHIVE_SCHEMA = "zmb-determinism-arm64/v1"
REQUIRED_DECLARES = ("鲲鹏实例未实测", "openEuler 未实测", "昇腾 NPU 未涉及")


def norm_machine(m):
    """aarch64 与 arm64 是同一指令集的不同叫法（Linux vs macOS 惯例）。
    不归一化会把 Linux arm64 runner 误报成「跨指令集」，这是 2026-11-09 的发现。"""
    return {"aarch64": "arm64", "arm64": "arm64"}.get(m.lower(), m.lower())


def current_env():
    return {"machine": norm_machine(platform.machine()),
            "raw_machine": platform.machine(),
            "system": platform.system(),
            "platform": platform.platform(),
            "python": platform.python_version()}


def run_one(variant):
    """在一个被扰动过的子进程里跑一次完整审计，返回结论字段。"""
    vid, dim, env_extra, py_prefix, how = variant
    env = dict(os.environ)
    for k, v in env_extra.items():
        if k == "__FAKE_DEPS__":
            env["PYTHONPATH"] = os.pathsep.join(
                [str(FAKE_DEPS), str(ROOT / "src")])
        else:
            env[k] = v
    cmd = [sys.executable] + py_prefix + ["scripts/audit_one.py", FIXTURE, "--json"]
    p = subprocess.run(cmd, cwd=str(ROOT), env=env, capture_output=True, text=True)
    rec = {"id": vid, "dimension": dim, "how": how, "rc": p.returncode}
    try:
        d = json.loads(p.stdout)
    except (ValueError, TypeError):
        rec["error"] = (p.stdout + p.stderr).strip()[:160]
        return rec
    for f in FIELDS:
        rec[f] = d.get(f)
    return rec


def run_all():
    return {v[0]: run_one(v) for v in VARIANTS}


def load_archive():
    if not ARCHIVE.is_file():
        return None
    try:
        return json.loads(ARCHIVE.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def evaluate(records, archive, expected_ids=EXPECTED_IDS):
    """纯函数：给一批实测记录和一份 ARM64 存档，判 PASS / FAIL。

    刻意不碰子进程，这样双向断言可以精确构造故障输入，
    而不是靠「把真代码改坏再改回来」。
    """
    v = []
    env = current_env()
    if not archive:
        v.append("ARM64 采集存档缺失：跨指令集比对无从谈起")
        return {"status": "FAIL", "violations": v, "checks": 0,
                "cross_arch": False, "cross_python": False, "current": env,
                "reference": {}}

    if archive.get("schema") != ARCHIVE_SCHEMA:
        v.append("存档 schema 不是 %s" % ARCHIVE_SCHEMA)
    ref = archive.get("reference", {})
    if ref.get("machine") != "arm64":
        v.append("存档必须来自 arm64 采集，实际 machine=%r" % ref.get("machine"))
    dec = archive.get("declares", [])
    for d in REQUIRED_DECLARES:
        if d not in dec:
            v.append("存档缺少边界声明「%s」——不许拿这份证据换适配认证" % d)

    missing = [i for i in expected_ids if i not in records]
    if missing:
        v.append("维度缺失：%s" % ", ".join(missing))

    base = records.get("baseline")
    if not base or base.get("rc") != 0 or "digest" not in base:
        v.append("基准实验没跑出结论")
        return {"status": "FAIL", "violations": v, "checks": 0,
                "cross_arch": False, "cross_python": False, "current": env,
                "reference": ref}

    ncheck = 0
    for vid, rec in records.items():
        if rec.get("rc") != 0 or "digest" not in rec:
            v.append("%s 没跑出结论（rc=%s, error=%s）" %
                     (vid, rec.get("rc"), rec.get("error", "")[:60]))
            continue
        for f in FIELDS:
            ncheck += 1
            if rec.get(f) != base.get(f):
                v.append("%s 的 %s 与基准不一致：%r ≠ %r" %
                         (vid, f, rec.get(f), base.get(f)))
        for f in FIELDS:
            ncheck += 1
            if rec.get(f) != archive.get(f):
                v.append("%s 的 %s 与 arm64 存档不一致：%r ≠ %r" %
                         (vid, f, rec.get(f), archive.get(f)))

    # 跨环境是否真的发生：本机 arm64 与存档同架构时，诚实标注为同架构复算
    cross_arch = env["machine"] != ref.get("machine")
    cross_python = env["python"] != ref.get("python")

    return {"status": "FAIL" if v else "PASS", "violations": v,
            "checks": ncheck, "cross_arch": cross_arch,
            "cross_python": cross_python, "current": env,
            "reference": ref}


def capture():
    """只在 arm64 开发机上重新采集存档。x86_64 CI 不许覆盖它。"""
    env = current_env()
    if env["machine"] != "arm64":
        print("拒绝采集：当前是 %s，存档必须来自 arm64 机器。" % env["machine"])
        return 2
    rec = run_one(VARIANTS[0])
    if rec.get("rc") != 0:
        print("基准实验失败：%s" % rec.get("error", ""))
        return 1
    doc = {
        "schema": ARCHIVE_SCHEMA,
        "captured_at": "2026-11-08",
        "captured_by": "python3 eval/determinism_matrix.py --capture",
        "reference": {
            "machine": env["machine"], "platform": env["platform"],
            "python": env["python"],
            "instruction_set": "ARM64 / AArch64（与鲲鹏、openEuler aarch64 同指令集）",
        },
        "fixture": FIXTURE,
        "declares": list(REQUIRED_DECLARES),
        "note": ("本存档只证明「判定结论在这台 arm64 机器上是什么」。"
                 "跨指令集结论由 x86_64 CI 当场算出同一 digest 后成立；"
                 "它不是鲲鹏实例实测，也不是 openEuler 兼容性认证，更不涉及昇腾 NPU。"),
    }
    for f in FIELDS:
        doc[f] = rec[f]
    ARCHIVE.parent.mkdir(parents=True, exist_ok=True)
    ARCHIVE.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
    print(json.dumps(doc, ensure_ascii=False, indent=2))
    return 0


def self_test():
    """干净必须 PASS；7 种「把判据变成橡皮图章」的做法必须 FAIL。"""
    def good_records():
        base = {"rc": 0, "digest": "52bf7b635204fd96", "verdict": "pass",
                "missing_cost": 618.4, "duplicate_cut": 182.0,
                "rows": 123, "total": 153384.0, "dimension": "基准", "how": ""}
        return {i: dict(base, id=i) for i in EXPECTED_IDS}

    def good_archive():
        return {"schema": ARCHIVE_SCHEMA,
                "reference": {"machine": "arm64", "python": "3.13.12"},
                "declares": list(REQUIRED_DECLARES),
                "digest": "52bf7b635204fd96", "verdict": "pass",
                "missing_cost": 618.4, "duplicate_cut": 182.0,
                "rows": 123, "total": 153384.0}

    cases = []

    def case(name, recs, arch, ids, expect):
        got = evaluate(recs, arch, ids)["status"]
        cases.append({"name": name, "expected": expect, "got": got,
                      "ok": got == expect})

    case("干净记录 + 完整存档 → PASS", good_records(), good_archive(),
         EXPECTED_IDS, "PASS")

    r = good_records(); r["seed_random"]["digest"] = "deadbeefdeadbeef"
    case("某个哈希种子下指纹漂移 → FAIL", r, good_archive(), EXPECTED_IDS, "FAIL")

    case("删掉 arm64 采集存档 → FAIL", good_records(), None, EXPECTED_IDS, "FAIL")

    a = good_archive(); a["digest"] = "0000000000000000"
    case("存档 digest 被改 → FAIL", good_records(), a, EXPECTED_IDS, "FAIL")

    a = good_archive(); a["declares"] = ["openEuler 未实测", "昇腾 NPU 未涉及"]
    case("抹掉「鲲鹏实例未实测」边界声明 → FAIL", good_records(), a,
         EXPECTED_IDS, "FAIL")

    r = good_records(); del r["no_site_packages"]
    case("少跑一个维度（判据空转）→ FAIL", r, good_archive(), EXPECTED_IDS, "FAIL")

    r = good_records(); r["locale_c"]["verdict"] = "fail"
    case("locale 变了结论也变 → FAIL", r, good_archive(), EXPECTED_IDS, "FAIL")

    a = good_archive(); a["reference"]["machine"] = "x86_64"
    case("拿 x86_64 结果冒充 arm64 存档 → FAIL", good_records(), a,
         EXPECTED_IDS, "FAIL")

    return {"status": "PASS" if all(c["ok"] for c in cases) else "FAIL",
            "cases": cases}


def render(rep, records, archive, selftest=None):
    env = rep["current"]; ref = rep.get("reference", {})
    lines = [
        "# 跨环境判定确定性矩阵 · 可执行版", "",
        "> 同一份 `data/real/feitai_budget.csv`，换哈希种子 / 时区 / locale / 编码 /",
        "> 优化级别 / 可选依赖 / 指令集 / Python 版本，结论必须逐字节一样。",
        "> 参照 GitHub 上的 `SOC_Replay` reproducible-builds 与 `MEP-51`",
        "> cross-target byte-equal differential gate。", "",
        "**%s** ｜ %d 个环境变体 ｜ %d 次字段比对 ｜ 双向断言 %s" %
        (rep["status"], len(records), rep["checks"],
         selftest["status"] if selftest else "未跑"), "",
        "## 本次环境 vs ARM64 采集存档", "",
        "| | 本次 | 存档（采集于 arm64 开发机） |", "|---|---|---|",
        "| 指令集 | `%s`（%s） | `%s` |" % (env["machine"], env.get("system","?"), ref.get("machine", "?")),
        "| Python | `%s` | `%s` |" % (env["python"], ref.get("python", "?")),
        "| 判定指纹 | `%s` | `%s` |" %
        (records.get("baseline", {}).get("digest", "—"), archive.get("digest", "?")),
        "| 跨指令集比对 | %s | — |" % ("**是**" if rep["cross_arch"] else "否（同架构复算）"),
        "| 跨 Python 版本比对 | %s | — |" %
        ("**是**" if rep["cross_python"] else "否（同版本复算）"), "",
        "> 本机 arm64 跑这条只能证明「同架构可复算」；**x86_64 的 CI 跑绿，",
        "> 才让「跨指令集确定性」成立**。CI 另外用 Python 版本矩阵覆盖版本维度。", "",
        "## 环境扰动逐项结果", "",
        "| 变体 | 维度 | 注入方式 | digest | verdict | 核减 | 与存档 |",
        "|---|---|---|---|---|---|---|",
    ]
    for v in VARIANTS:
        r = records.get(v[0], {})
        same = all(r.get(f) == archive.get(f) for f in FIELDS) and r.get("rc") == 0
        lines.append("| `%s` | %s | %s | `%s` | %s | %s | %s |" %
                     (v[0], v[1], v[4].replace("|", "/"),
                      r.get("digest", "—"), r.get("verdict", "—"),
                      r.get("missing_cost", "—"), "✅" if same else "❌"))
    lines += ["", "## 边界（这一段不许删）", ""]
    for d in (archive.get("declares") or []):
        lines.append("- **%s**：本矩阵不改变这条口径。" % d)
    lines += [
        "- 比对的是**判定结论**（digest / verdict / 金额 / 行数），不是构建产物字节；"
        "不是 reproducible build。",
        "- 证明的是**代码在环境变化下的确定性**，不是华为云服务 SLA，"
        "也不是鲲鹏/openEuler 的兼容性认证。",
        "- 存档来自 arm64 开发机（macOS），不是华为云 aarch64 实例；"
        "CI 是 x86_64 Linux——两者不同，才叫跨环境。",
        "- 环境变量扰动不等于真的换一台国产服务器；要补那条，仍需在真鲲鹏 ECS",
        "  上跑 `uname -m` 与 `bash scripts/check_all.sh` 并存证。", "",
    ]
    if selftest:
        lines += ["## 双向断言（判据自己也要被查）", "",
                  "| 故障注入 | 期望 | 实际 | 通过 |", "|---|---|---|---|"]
        for c in selftest["cases"]:
            lines.append("| %s | %s | %s | %s |" %
                         (c["name"], c["expected"], c["got"], "✅" if c["ok"] else "❌"))
        lines += ["", "**自检：%s**" % selftest["status"], ""]
    if rep["violations"]:
        lines += ["## 违规", ""] + ["- %s" % x for x in rep["violations"]] + [""]
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(description="跨环境判定确定性矩阵")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--no-report", action="store_true")
    ap.add_argument("--capture", action="store_true",
                    help="重新采集 arm64 存档；只允许在 arm64 机器上执行")
    args = ap.parse_args()
    if args.capture:
        return capture()

    st = self_test() if args.self_test else None
    records = run_all()
    archive = load_archive() or {}
    rep = evaluate(records, load_archive())
    if st:
        rep["self_test"] = st["status"]
    if not args.no_report:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(render(rep, records, archive, st), encoding="utf-8")

    if args.json:
        print(json.dumps({
            "status": rep["status"], "variants": len(records),
            "checks": rep["checks"], "cross_arch": rep.get("cross_arch"),
            "cross_python": rep.get("cross_python"),
            "current": rep.get("current"), "reference": rep.get("reference"),
            "violations": rep["violations"][:6],
            "self_test": st["status"] if st else "not_run"}, ensure_ascii=False))
    else:
        print(render(rep, records, archive, st))
    bad = rep["status"] != "PASS" or (st and st["status"] != "PASS")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
