# -*- coding: utf-8 -*-
"""感知层闭环 demo：把「原始 OCR 输出」和「人工校对后」的审计结果摆在一起比。

## 为什么需要这个脚本

我们一直在说「校对关卡不能省」，但这是一句主张。这个脚本把它变成两个数字：
**跳过校对，会多出多少条假漏项、多报多少钱。**

输入是**真实模型输出**（华为云通用表格识别对 4 页照片的识别结果，117 行，
存在错字与单位噪声），不是构造的。当前 OCR 账号受限无法复跑，但这批产物是历史留痕，
足以回答「AI 接进来会怎样」。

## 它诚实的地方

- 不声称这是识别准确率（没有逐字 ground truth）；
- OCR 原始结果里第 2–4 页存在斜拍导致的列错位，所以「多出的假漏项」里
  有一部分是拍摄问题，不是模型问题——结论里分开写；
- 只比较「同一套判定、同一套模板、不同输入」的差异，不比较两套引擎。
"""
import csv
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
import pipeline as P  # noqa: E402

DATA = os.path.join(ROOT, "data")
OUT = os.path.join(ROOT, "out", "ocr_loop")


def _num(v):
    try:
        return float((v or "").replace(",", ""))
    except ValueError:
        return 0.0


def ocr_noise_stats(path):
    """不看审计，只看这张 OCR 表本身的质量。"""
    with open(path, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    quant = [r for r in rows if _num(r.get("数量")) > 0 and _num(r.get("单价")) > 0]
    ok = [r for r in quant
          if abs(_num(r["数量"]) * _num(r["单价"]) - _num(r.get("合价"))) <= 1]
    noisy_name = [r for r in rows
                  if (r.get("项目名称") or "").strip().isdigit()
                  or len((r.get("项目名称") or "").strip()) <= 1]
    weird_unit = [r for r in rows
                  if (r.get("单位") or "").strip() and (r.get("单位") or "").strip() not in
                  ("m2", "m", "㎡", "项", "个", "根", "套", "樘", "块", "张", "L", "支", "瓶", "次", "组")]
    return {
        "rows": len(rows),
        "quantified": len(quant),
        "arith_ok": len(ok),
        "arith_bad": len(quant) - len(ok),
        "arith_ok_ratio": round(len(ok) / max(1, len(quant)) * 100, 1),
        "noisy_name": len(noisy_name),
        "weird_unit": len(weird_unit),
        "amount_sum": round(sum(_num(r.get("合价")) for r in rows), 2),
        "bad_rows": [{"no": r.get("序号"), "name": (r.get("项目名称") or "")[:18],
                      "qty": r.get("数量"), "price": r.get("单价"), "amount": r.get("合价")}
                     for r in quant if r not in ok][:12],
        "noisy_examples": [(r.get("项目名称") or "")[:16] for r in noisy_name[:8]],
        "weird_unit_examples": [(r.get("单位") or "")[:6] for r in weird_unit[:8]],
    }


def audit_of(csv_path, label):
    onto = P.Ontology(os.path.join(DATA, "ontology_v0.json"))
    with open(os.path.join(DATA, "templates_v0.json"), encoding="utf-8") as f:
        templates = json.load(f)
    with open(os.path.join(DATA, "rules_v0.json"), encoding="utf-8") as f:
        rules = json.load(f)
    with open(os.path.join(DATA, "coverage_v0.json"), encoding="utf-8") as f:
        coverage = json.load(f)
    q = P.build_line_index(P.load_quote(csv_path, label), onto)
    res = P.audit([q], onto, templates, rules, coverage, template_key="old_house")[q["label"]]
    return q, res


def main():
    ocr_path = os.path.join(DATA, "real", "feitai_budget_ocr.csv")
    fix_path = os.path.join(DATA, "real", "feitai_budget.csv")
    if not (os.path.isfile(ocr_path) and os.path.isfile(fix_path)):
        raise SystemExit("缺少 OCR 产物或校正版 CSV")

    noise = ocr_noise_stats(ocr_path)
    q_ocr, r_ocr = audit_of(ocr_path, "原始 OCR 输出")
    q_fix, r_fix = audit_of(fix_path, "人工校对后")

    def row_of(q, r):
        return {
            "rows": len(q["rows"]), "summary": len(q["summary_rows"]),
            "aligned": sum(1 for x in q["rows"] if x["cid"]),
            "align_ratio": round(sum(1 for x in q["rows"] if x["cid"]) / max(1, len(q["rows"])) * 100, 1),
            "missing_auto": sum(1 for m in r["missing"] if m["tier"] == "auto"),
            "missing_review": sum(1 for m in r["missing"] if m["tier"] != "auto"),
            "dup": len(r["duplicates"]), "risks": len(r["risks"]),
            "total": round(q["total"], 2),
            "recon_diff": (r["recon"] or {}).get("diff"),
        }

    a, b = row_of(q_ocr, r_ocr), row_of(q_fix, r_fix)
    os.makedirs(OUT, exist_ok=True)

    L = ["# 感知层闭环：原始 OCR vs 人工校对（同一套判定）", "",
         "> 输入：`data/real/feitai_budget_ocr.csv`（华为云通用表格识别真实输出，4 页照片，117 行）",
         "> 对照：`data/real/feitai_budget.csv`（人工逐页转录 + OCR 双通道校正，123 行工序 + 8 行汇总）",
         "> 同模板 old_house / 同 13 条规则 / 同本体。**只换输入，不换判定。**", "",
         "## 1. OCR 产物本身的质量（不看审计）", "",
         "| 指标 | 数值 |", "|---|---:|",
         "| 网格行 | %d |" % noise["rows"],
         "| 量价齐全 | %d |" % noise["quantified"],
         "| 算术自洽（数量×单价=合价 ±1） | **%d（%.1f%%）** |" % (noise["arith_ok"], noise["arith_ok_ratio"]),
         "| 算术不符（校对台该标黄的位置） | %d |" % noise["arith_bad"],
         "| 项目名是噪声（纯数字/单字） | %d |" % noise["noisy_name"],
         "| 单位异常字符串（如 `m'`） | %d |" % noise["weird_unit"],
         "| 合价之和 | %s |" % "{:,.2f}".format(noise["amount_sum"]),
         ""]
    if noise["noisy_examples"]:
        L.append("噪声项目名示例：%s" % "、".join(x for x in noise["noisy_examples"] if x))
        L.append("")
    if noise["weird_unit_examples"]:
        L.append("异常单位示例：%s" % "、".join(noise["weird_unit_examples"]))
        L.append("")
    if noise["bad_rows"]:
        L.append("算术不符的行（就是校对台要标黄的）：")
        L.append("")
        L.append("| 序号 | 项目 | 数量 | 单价 | 合价 |")
        L.append("|---|---|---:|---:|---:|")
        for x in noise["bad_rows"]:
            L.append("| %s | %s | %s | %s | %s |" % (x["no"], x["name"], x["qty"], x["price"], x["amount"]))
        L.append("")

    L += ["## 2. 同一套判定，两种输入的结论差异", "",
          "| 指标 | 原始 OCR | 人工校对后 | 差异 |", "|---|---:|---:|---:|",
          "| 工序行 | %d | %d | %+d |" % (a["rows"], b["rows"], b["rows"] - a["rows"]),
          "| 汇总行 | %d | %d | %+d |" % (a["summary"], b["summary"], b["summary"] - a["summary"]),
          "| 对齐率 | %.1f%% | %.1f%% | %+.1f pp |" % (a["align_ratio"], b["align_ratio"],
                                                     b["align_ratio"] - a["align_ratio"]),
          "| 确定漏项（auto） | **%d** | **%d** | **%+d** |" % (a["missing_auto"], b["missing_auto"],
                                                          b["missing_auto"] - a["missing_auto"]),
          "| 建议复核（review） | %d | %d | %+d |" % (a["missing_review"], b["missing_review"],
                                                  b["missing_review"] - a["missing_review"]),
          "| 重复计价 | %d | %d | %+d |" % (a["dup"], b["dup"], b["dup"] - a["dup"]),
          "| 风险条目 | %d | %d | %+d |" % (a["risks"], b["risks"], b["risks"] - a["risks"]),
          "| 明细合计 | %s | %s | %s |" % ("{:,.2f}".format(a["total"]), "{:,.2f}".format(b["total"]),
                                       "{:+,.2f}".format(b["total"] - a["total"])),
          "| 对账差额 | %s | %s | — |" % (
              "—" if a["recon_diff"] is None else "{:,.2f}".format(a["recon_diff"]),
              "—" if b["recon_diff"] is None else "{:,.2f}".format(b["recon_diff"])),
          ""]

    extra = a["missing_auto"] - b["missing_auto"]
    L += ["## 3. 结论（可写进材料的原话）", "",
          "1. **跳过校对，确定漏项从 %d 条膨胀到 %d 条**（多出 %d 条），其中大部分是 OCR 把项目名读错、"
          "对齐失败造成的**假漏项**——这与「引擎误报」是两件不同的事，必须分开说。" % (
              b["missing_auto"], a["missing_auto"], extra),
          "2. **算术自校验能自动圈出可疑行**：%d 行「数量×单价≠合价」，校对台据此标黄，"
          "不需要人工逐行看全部 %d 行。" % (noise["arith_bad"], noise["rows"]),
          "3. **合价之和差 %s 元**（%s vs %s）：数字列整体可信，但分页/斜拍会丢行，"
          "所以对账层必须在 OCR 之后跑一遍，而不是只在人工录入后才跑。" % (
              "{:,.2f}".format(abs(noise["amount_sum"] - b["total"])),
              "{:,.2f}".format(noise["amount_sum"]), "{:,.2f}".format(b["total"])),
          "4. **这不是识别准确率**：没有逐字 ground truth；且第 2–4 页是斜拍，列错位放大了噪声。"
          "它能证明的是「校对关卡的必要性」，不是「模型好坏」。",
          ""]

    md_path = os.path.join(OUT, "ocr_vs_corrected.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    with open(os.path.join(OUT, "ocr_vs_corrected.json"), "w", encoding="utf-8") as f:
        json.dump({"noise": noise, "raw_ocr": a, "corrected": b}, f, ensure_ascii=False, indent=1)

    print("\n".join(L[:6]))
    print("...")
    print("原始 OCR：对齐 %.1f%%｜auto 漏项 %d｜合计 %.2f" % (a["align_ratio"], a["missing_auto"], a["total"]))
    print("校对后：  对齐 %.1f%%｜auto 漏项 %d｜合计 %.2f" % (b["align_ratio"], b["missing_auto"], b["total"]))
    print("→ 跳过校对会多出 %d 条确定漏项" % extra)
    print("报告：%s" % md_path)


if __name__ == "__main__":
    main()
