# -*- coding: utf-8 -*-
"""生成 RenoPit 对照实验的输入包（给两系统严格等量的信息）

为什么单独生成：对照实验的前提是「信息对称」——
给 RenoPit 的输入，必须是装明白引擎能看到的同一批行项（项目/规格/单位/数量/单价/合价/备注），
不能给 RenoPit 原图（我们没有给装明白原图），也不能隐藏备注（引擎读备注判证据词）。

产物：
  eval/reno_input/<slug>.txt      带行号的纯文本表（直接粘贴给 RenoPit 在线 Demo）
  eval/reno_input/manifest.json   每份文件的 sha256（证明两系统输入同一份文本）

用法：
  python3 eval/build_reno_input.py
"""
import csv
import hashlib
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "eval", "reno_input")

DOCS = [
    ("sh_zhuoguan", "data/real/sh_zhuoguan_halfpack.csv", "上海卓观·两房半包（篱笆网公开晒单）"),
    ("huaxibeian", "data/real/huaxibeian_newhouse.csv", "花溪北岸·5 页施工报价"),
    ("feitai", "data/real/feitai_budget.csv", "上海翡翠台·老房全案"),
]

HEADER = "序号 | 项目名称 | 规格工艺 | 单位 | 数量 | 单价 | 合价 | 备注"


def main():
    os.makedirs(OUT, exist_ok=True)
    manifest = {"note": "两系统输入必须完全一致；本文件用于核对。", "docs": []}
    for slug, rel, doc in DOCS:
        path = os.path.join(ROOT, rel)
        rows = list(csv.DictReader(open(path, encoding="utf-8-sig")))
        lines = [
            "【单据】%s" % doc,
            "【说明】以下为施工方报价单的逐行明细（原文转录）。请审计：漏项、重复计价、口径问题。",
            "",
            HEADER,
        ]
        for r in rows:
            cells = [r.get("序号", ""), r.get("项目名称", ""), r.get("规格工艺", ""),
                     r.get("单位", ""), r.get("数量", ""), r.get("单价", ""),
                     r.get("合价", ""), r.get("备注", "")]
            lines.append(" | ".join(cells))
        text = "\n".join(lines) + "\n"
        dst = os.path.join(OUT, slug + ".txt")
        open(dst, "w", encoding="utf-8").write(text)
        sha = hashlib.sha256(text.encode()).hexdigest()[:16]
        manifest["docs"].append({"slug": slug, "doc": doc, "rows": len(rows),
                                 "chars": len(text), "sha256_16": sha,
                                 "file": os.path.basename(dst)})
        print("%-14s %3d 行  %6d 字  sha %s" % (slug, len(rows), len(text), sha))

    open(os.path.join(OUT, "manifest.json"), "w", encoding="utf-8").write(
        json.dumps(manifest, ensure_ascii=False, indent=2))
    print("\n输出:", OUT)
    print("下一步：把 3 个 txt 分别提交给 RenoPit 在线 Demo（每份跑 3 次，间隔≥1 小时），存原始输出。")


if __name__ == "__main__":
    main()
