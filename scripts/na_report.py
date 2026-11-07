# -*- coding: utf-8 -*-
"""把负向知识库渲染成一张可读的表（并给出可执行验证的入口）。

为什么单独出这个：`data/not_applicable_v0.json` 是机器读的，
但答辩时要说给人听。「我们判什么」有 13 条规则可讲，
「我们不判什么」如果只能口头说，就没有分量。

用法：
    python3 scripts/na_report.py              # 打到 stdout
    python3 scripts/na_report.py --out out/not_applicable.md
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "data", "not_applicable_v0.json")

STATUS = {"enforced": "有测试锁", "documented": "已记录"}


def build():
    with open(SRC, encoding="utf-8") as f:
        doc = json.load(f)
    items = doc["items"]
    lines = []
    lines.append("# 负向知识库：我们明确「不判」的 12 件事")
    lines.append("")
    lines.append("> 与 `data/rules_v0.json` 互为补集——那份说判什么，这份说不判什么、为什么。")
    lines.append("> 每条都有可执行验证（`tests/test_not_applicable.py`）；改坏了测试会红。")
    lines.append("")
    lines.append("## 为什么记这个")
    lines.append("")
    lines.append("正向规则改坏了会有人发现（结论少了）；**负向改坏了大都没人发现**——")
    lines.append("因为它的表现是「多了一条假阳性」，而假阳性长得和真结论一模一样。")
    lines.append("")
    lines.append("最典型：`find_duplicates` 里那句「变体不同就排除」。删掉它，5 份单据会多出")
    lines.append("**72 条**「重复计价」，报告看起来更丰富——不懂业务的人只会觉得引擎更敏锐了。")
    lines.append("")
    lines.append("## 清单")
    lines.append("")
    lines.append("| # | 情形 | 决定 | 为什么 | 若判了会怎样 | 保障 |")
    lines.append("|---|---|---|---|---|---|")
    for it in items:
        lines.append("| %s | %s | **%s** | %s | %s | %s |" % (
            it["id"], it["case"], it["decision"], it["why"], it["if_judged"],
            STATUS.get(it["status"], it["status"])))
    lines.append("")
    enforced = sum(1 for x in items if x["status"] == "enforced")
    lines.append("合计：%d 条，其中 **%d 条有测试锁**，%d 条已记录待补。"
                 % (len(items), enforced, len(items) - enforced))
    lines.append("")
    lines.append("## 依据")
    lines.append("")
    lines.append("JCCDB 把「证明不存在」的 608 条单列并给理由，而不是删掉了事——")
    lines.append("同一个道理：**知道什么不成立，和知道什么成立一样值钱。**")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    text = build()
    if args.out:
        path = os.path.join(ROOT, args.out) if not os.path.isabs(args.out) else args.out
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        print("wrote %s (%d chars)" % (args.out, len(text)))
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
