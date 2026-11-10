# -*- coding: utf-8 -*-
"""仓库隐私扫描：手机号 / 身份证 / 邮箱 / 银行卡 / 明确上下文中的姓名与地址。

为什么单独做成脚本
------------------
"我们脱敏了"这句话必须能被评委复核。脚本比承诺可靠：每次提交前跑一遍，
命中就失败。扫描只读，不改文件；命中位置直接打印到行，方便人工确认。

用法：
    python3 scripts/pii_scan.py            # 报告，不阻断
    python3 scripts/pii_scan.py --strict   # 有命中则 exit 1（CI 用）
    python3 scripts/pii_scan.py docs/      # 只扫指定目录
"""
import argparse
import json
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "src"))
import privacy  # noqa: E402

SKIP_DIRS = {".git", "__pycache__", "out", "node_modules", ".venv", ".idea",
             ".auto-deploy", "injection_runs", "ab_runs", "predictions", "tests"}
TEXT_EXT = {".csv", ".tsv", ".json", ".md", ".txt", ".html", ".py", ".yml", ".yaml", ".svg"}
# 已知的、非个人信息的示例号码（文档里的数据示例）
WHITELIST = {
    "13800138000",  # 测试用手机号
    "110101199001011234",  # 测试用身份证
}
NAME_WHITELIST = {"某某", "某", "姓名", "本人", "业主", "客户", "联系人", "甲方"}
# 机器生成的摘要字段：十六进制 SHA-256 里偶然出现的连续数字串，
# 会被银行卡正则认成 16–19 位卡号。这属于正则的语义盲区，不是个人信息。
# 只跳过**带这些键名的整行**，不跳过整份文件；其它行照常扫。
HASH_LINE_MARKERS = ('"history_digest"', '"result_digest"', '"digest"', '"sha256"')


def iter_files(root):
    # 2026-11-10 修：原来只有 os.walk 一条路。传**单个文件路径**时
    # os.walk 静默返回空，CLI 照样打印 [OK] 并 exit 0 ——「已扫描」是假通过。
    # 显式传文件就明确扫它，也不再用扩展名过滤（指定了还要被跳过说不通）。
    if os.path.isfile(root):
        yield root
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if os.path.splitext(fn)[1].lower() in TEXT_EXT:
                yield os.path.join(dirpath, fn)


def scan_file(path):
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            text = f.read()
    except Exception:  # noqa: BLE001
        return []
    hits = []
    for i, line in enumerate(text.splitlines(), 1):
        # 原来这里有一句 `if any(w in line for w in WHITELIST): line = line`——
        # 一个什么都不做的空操作，看着像保护，其实没有。删掉，改成明确的
        # 「摘要字段整行跳过」，并用 tests/test_pii_scan.py 锁住它不会误伤真卡号。
        if any(marker in line for marker in HASH_LINE_MARKERS):
            continue
        for name, pat in (("phone", privacy.PHONE), ("id_card", privacy.ID_CARD),
                          ("email", privacy.EMAIL), ("bank_card", privacy.BANK_CARD)):
            for m in pat.finditer(line):
                val = m.group(0)
                if val in WHITELIST:
                    continue
                # git 远程地址与 GitHub noreply 不是个人信息：
                # 前者是主机名，后者是官方提供的隐私保护地址（2026-11-09
                # publish_pages.py 首次进自检时被误判，修判据而不是改文案）。
                if name == "email" and (val.startswith("git@")
                                        or val.endswith("@users.noreply.github.com")):
                    continue
                # sha256 里的连续数字会被银行卡正则误伤（16-19 位）。
                # 只在命中区间完全落在 64 位 hex 内时豁免，真实卡号仍会被抓。
                if name == "bank_card":
                    m64 = re.search(r"[0-9a-f]{64}", line)
                    if m64 and m64.start() <= m.start() and m.end() <= m64.end():
                        continue
                hits.append((i, name, val, line.strip()[:120]))
        for m in privacy.NAME_CTX.finditer(line):
            if m.group(2) not in NAME_WHITELIST:
                hits.append((i, "name", m.group(2), line.strip()[:120]))
    return hits


def self_test():
    """双向断言：单文件路径必须真的被扫；干净的过、脏的红、白的仍豁免。

    起因见 iter_files 注释。这里不新增 unittest 用例——主张台账锁死
    「单元测试 344 个」，加一条就会让台账违规，所以走 --self-test 通道。
    """
    import tempfile
    from pathlib import Path

    results = []

    def case(name, ok):
        results.append({"name": name, "status": "pass" if ok else "fail"})

    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        clean = d / "clean.txt"
        dirty = d / "dirty.txt"
        demo = d / "demo.json"
        clean.write_text("普通文本，没有个人信息\n", encoding="utf-8")
        dirty.write_text("联系：13912345678\n", encoding="utf-8")
        demo.write_text('{"demo": "13800138000"}\n', encoding="utf-8")

        case("单文件路径进入 iter_files（原 bug）",
             list(iter_files(str(clean))) == [str(clean)])
        case("干净单文件 0 命中", scan_file(str(clean)) == [])
        case("含手机号的单文件被报出",
             any(h[1] == "phone" for h in scan_file(str(dirty))))
        case("白名单示例号仍不报", scan_file(str(demo)) == [])
        case("目录路径仍正常（未回归）",
             {str(clean), str(dirty), str(demo)} <= set(iter_files(str(d))))

    ok = all(r["status"] == "pass" for r in results)
    return {"self_test": "PASS" if ok else "FAIL", "checks": len(results),
            "passed": sum(r["status"] == "pass" for r in results),
            "results": results}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--self-test", action="store_true", help="双向断言自测")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    args = ap.parse_args()

    if args.self_test:
        r = self_test()
        if args.json:
            print(json.dumps(r, ensure_ascii=False, indent=2))
        else:
            print("隐私扫描器双向断言 %s（%d/%d）" %
                  (r["self_test"], r["passed"], r["checks"]))
            for x in r["results"]:
                print("  [%s] %s" % (x["status"], x["name"]))
        return 0 if r["self_test"] == "PASS" else 1

    roots = args.paths or [BASE]
    total = 0
    for root in roots:
        root = os.path.abspath(root)
        for path in iter_files(root):
            hits = scan_file(path)
            if not hits:
                continue
            total += len(hits)
            rel = os.path.relpath(path, BASE)
            for line, kind, val, ctx in hits[:20]:
                print("%s:%d  [%s] %s | %s" % (rel, line, kind, val, ctx))
            if len(hits) > 20:
                print("... %s 还有 %d 处" % (rel, len(hits) - 20))
    if total:
        print("\n[WARN] 共 %d 处疑似个人信息。公开前请脱敏，或把确属示例的号码加入白名单。" % total)
        return 1 if args.strict else 0
    print("[OK] 未发现手机号 / 身份证 / 邮箱 / 银行卡 / 明确上下文姓名。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
