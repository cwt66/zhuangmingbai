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
                hits.append((i, name, val, line.strip()[:120]))
        for m in privacy.NAME_CTX.finditer(line):
            if m.group(2) not in NAME_WHITELIST:
                hits.append((i, "name", m.group(2), line.strip()[:120]))
    return hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args()
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
