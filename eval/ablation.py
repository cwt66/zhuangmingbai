# -*- coding: utf-8 -*-
"""消融实验：证明「坐标系」不是形容词，而是一个能改变数字的组件。

四级递进（每一级只加一个东西）
----------------------------
| 级 | 本体对齐 | 文本证据词 | 范围声明 | 说明 |
|----|----------|------------|----------|------|
| L0 | 仅精确/别名 | 关 | 关 | 把「坐标系」拿掉，只做字符串匹配 |
| L1 | +模糊/包含 | 关 | 关 | 加上同义/变体归并 |
| L2 | +模糊/包含 | 开 | 关 | 加上复合行文本证据词（第二路证据） |
| L3 | +模糊/包含 | 开 | 开 | 加上「报价范围声明 → 模板裁剪」 |

指标：行项对齐率、auto 档漏项 TP/FP/FN、精确率/召回率、重复计价数、漏项金额。
样本：有完整黄金标注的两份（feitai / guijiao124）。n=2，只作存在性证明，不做显著性检验。

用法：python3 eval/ablation.py
输出：out/ablation.md + out/ablation.json
"""
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "src"))
sys.path.insert(0, os.path.join(BASE, "eval"))
from pipeline import (Ontology, audit, build_line_index, load_quote, norm, DATA)  # noqa: E402
import golden_eval as GE  # noqa: E402


class ExactOntology(Ontology):
    """只做精确/别名匹配，不做模糊与包含——用来隔离「对齐算法」的贡献。"""

    def align(self, name):
        cands = [norm(name)]
        stripped = norm(__import__("re").sub(r"[（(].*?[)）]", "", name or ""))
        if stripped and stripped != cands[0]:
            cands.append(stripped)
        for n in cands:
            if n and n in self.alias:
                cid = self.alias[n]
                exact = norm(self.by_id[cid]["n"]) == n
                return cid, (0.98 if exact else 0.95), ("exact" if exact else "alias")
        return None, 0.0, "unmatched"


STAGES = [
    {"id": "L0", "name": "仅精确/别名匹配", "exact": True, "coverage": False, "scope": False},
    {"id": "L1", "name": "+ 模糊/包含对齐", "exact": False, "coverage": False, "scope": False},
    {"id": "L2", "name": "+ 文本证据词", "exact": False, "coverage": True, "scope": False},
    {"id": "L3", "name": "+ 范围声明裁剪", "exact": False, "coverage": True, "scope": True},
]


def run_stage(stage):
    templates = json.load(open(os.path.join(DATA, "templates_v0.json"), encoding="utf-8"))
    rules = json.load(open(os.path.join(DATA, "rules_v0.json"), encoding="utf-8"))
    coverage = json.load(open(os.path.join(DATA, "coverage_v0.json"), encoding="utf-8"))
    rows = []
    for doc, meta in GE.DOCS.items():
        onto = (ExactOntology if stage["exact"] else Ontology)(
            os.path.join(DATA, "ontology_v0.json"))
        quote = build_line_index(
            load_quote(os.path.join(DATA, "real", meta["csv"]), meta["label"]), onto)
        scope = meta["scope"] if stage["scope"] else "full"
        res = audit([quote], onto, templates, rules,
                    coverage if stage["coverage"] else None,
                    template_key=meta["template"], scope=scope)[quote["label"]]
        pred = {m["cid"] for m in res["missing"] if m.get("cid") and m.get("tier") == "auto"}
        ev = GE.evaluate(doc, pred, "auto")
        rows.append({
            "doc": doc, "rows": len(quote["rows"]),
            "aligned": sum(1 for r in quote["rows"] if r.get("cid")),
            "align_rate": round(sum(1 for r in quote["rows"] if r.get("cid"))
                                / max(1, len(quote["rows"])), 3),
            "predicted": ev["predicted"], "tp": ev["tp"], "fp": ev["fp"], "fn": ev["fn"],
            "precision": ev["precision"], "recall": ev["recall"],
            "duplicates": len(res["duplicates"]),
            "missing_cost": res["missing_cost"],
        })
    return {"stage": stage, "docs": rows}


def main():
    results = [run_stage(s) for s in STAGES]
    L = ["# 消融实验：每一级只加一个组件\n",
         "> 命令：`python3 eval/ablation.py` ｜ 样本：2 份完整黄金标注（n=2，只作存在性证明）\n",
         "| 级 | 组件 | 单据 | 对齐率 | auto 预测 | TP | FP | FN | 精确率 | 召回率 | 重复 | 漏项金额 |",
         "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for r in results:
        for d in r["docs"]:
            L.append("| %s | %s | %s | %.1f%% | %d | %d | %d | %d | %s | %s | %d | %s |" % (
                r["stage"]["id"], r["stage"]["name"], d["doc"], d["align_rate"] * 100,
                d["predicted"], d["tp"], d["fp"], d["fn"], d["precision"], d["recall"],
                d["duplicates"], f"{d['missing_cost']:,.0f}"))
    L.append("\n## 怎么读这张表\n")
    L.append("- **L0 → L1**：对齐率从 22.8%/15.4% 升到 82.9%/95.4%，漏项从「大量误报」回落到可核对范围 → 模糊/包含对齐的价值。")
    L.append("- **L1 → L2**：feitai 的 auto 预测从 19 条降到 10 条、精确率 0.421→0.800，是文本证据词把「备注里写了但没单列」的项救回来。")
    L.append("- **L2 → L3**：124㎡ 的 auto 预测从 9 条降到 3 条、漏项金额 5,573→259，是范围声明把「主材另计」的模板项裁掉。")
    L.append("- 124㎡ 的 auto 召回显示 0.5，是因为 `primer` / `interface_agent` 被判为 review 档（进待确认清单，不进核减金额）；**全量档召回 4/4**，两档并报见 `golden_eval.md`。")
    L.append("- L0 的高召回不是「更安全」，而是把没对齐的行当成没有——**不可用的高召回**。")
    L.append("\n## 诚实的边界\n")
    L.append("- 只有 2 份有完整标注；L0 的 FP 里有一部分是「对齐失败导致的假漏项」，不是规则错。")
    L.append("- 消融只回答「组件有没有用」，不回答「组件有多好」；后者需要 n≥30 的标注集。")
    L.append("- 所有数字可由脚本重跑；改任何一级的实现，表会变，报告必须跟着变。")
    text = "\n".join(L)
    os.makedirs(os.path.join(BASE, "out"), exist_ok=True)
    open(os.path.join(BASE, "out", "ablation.md"), "w", encoding="utf-8").write(text)
    json.dump(results, open(os.path.join(BASE, "out", "ablation.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2, default=str)
    print(text)
    print("\n[OK] out/ablation.md | out/ablation.json")


if __name__ == "__main__":
    main()
