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

DATA = AR.DATA
# 兼容别名：清单与打包实现统一放在 src/audit_record.py（CLI 与 Web 共用），
# 这里保留同名符号，避免既有测试与调用方失效。
STANDARDS = AR.STANDARDS
CODE = AR.CODE
VERIFY_TMPL = AR.VERIFY_TMPL
README = AR.BUNDLE_README
LEDGER = os.path.join(ROOT, "data", "ledger.jsonl")
ANCHOR = os.path.join(ROOT, "data", "ledger_anchor.json")


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

    record, q, res, entry, anchor = AR.package_bundle(
        csv_path, label, out,
        template=args.template, scope=args.scope,
        ledger_path=args.ledger, anchor_path=args.anchor_file,
        write_ledger=not args.no_ledger,
    )

    print("审计包：%s（%.0f KB）" % (out, os.path.getsize(out) / 1024))
    print("  单据 %s｜模板 %s｜范围 %s" % (label, args.template, args.scope))
    print("  明细 %d 行 + 汇总 %d 行｜漏项 %d｜重复 %d｜风险 %d" % (
        len(q["rows"]), len(q["summary_rows"]), len(res["missing"]),
        len(res["duplicates"]), len(res["risks"])))
    print("  digest %s" % record["digest"])
    if entry is not None and anchor is not None:
        print("  账本 seq=%d ｜ 链头 %s…" % (entry["seq"], anchor["head"][:16]))
    print("  验证：unzip %s -d /tmp/v && python3 /tmp/v/verify.py" % os.path.basename(out))


if __name__ == "__main__":
    main()
