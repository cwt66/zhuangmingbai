# -*- coding: utf-8 -*-
"""单据图片 → 标准报价 CSV（感知层 CLI）。

    # 1) 华为云「表格识别」（需开通服务）
    python3 scripts/parse_doc.py ../装修报价单/xxx.jpg --provider ocr_table --out /tmp/x.csv

    # 2) ModelArts Studio 视觉模型（需 API Key）
    MAAS_API_KEY=xxx python3 scripts/parse_doc.py xxx.jpg --out /tmp/x.csv

    # 3) 自检（固定样例，不识别图片）
    python3 scripts/parse_doc.py --selftest

输出三样东西：CSV、解析报告（JSON）、以及一条明确的"下一步"提示。
**感知层失败必须失败得明显**——静默返回空表会被上层当成"没有漏项"。
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
import perception as P  # noqa: E402


def print_report(rows, meta, report, out_csv):
    print("provider   :", meta.get("provider"), "|", {k: v for k, v in meta.items()
                                                      if k not in ("provider",)} or "")
    if meta.get("warning"):
        print("!! 警告     :", meta["warning"])
    print("表头行     :", report.get("header_row"), "| 列映射:", report.get("mapped_columns"))
    print("行项       :", report.get("rows"), "（丢弃空名行", report.get("dropped_no_name"), "）")
    print("算术自校验 :", report.get("arith_checked"), "行可验，",
          "全部通过" if not report.get("arith_bad") else "%d 行不符" % len(report["arith_bad"]))
    for b in (report.get("arith_bad") or [])[:5]:
        print("   ! %-22s %s × %s = %s，表上写 %s" % (b["name"], b["qty"], b["price"],
                                                       b["calc"], b["amount"]))
    mult = report.get("multipliers") or {}
    if mult:
        for ratio, cnt in sorted(mult.items(), key=lambda kv: -kv[1]):
            pct = (float(ratio) - 1) * 100
            print("系统性系数 : %d 行的「合价 ÷（数量×单价）」= %.3f（+%.0f%%）"
                  % (cnt, float(ratio), pct))
        print("             → 单据对某类行乘了系数但表内未声明（常见是损耗/管理费），"
              "不是识别错误，是口径问题。")
    if report.get("total") is not None:
        print("合价合计   :", "%.2f" % report["total"])
    print("已写入     :", out_csv)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image", nargs="?")
    ap.add_argument("--provider", default=os.environ.get("PERCEPTION_PROVIDER"),
                    choices=["file", "maas", "ocr_table", "mock"],
                    help="默认按扩展名推断：csv/xlsx/pdf→file，图片→maas")
    ap.add_argument("--out", default="out/parsed.csv")
    ap.add_argument("--report", default=None, help="解析报告 JSON 路径")
    ap.add_argument("--selftest", action="store_true", help="不读图片，只验代码路径")
    args = ap.parse_args()

    if args.selftest:
        rows, meta, report = P.perceive("__selftest__", provider="mock")
        print_report(rows, meta, report, "(未写文件)")
        print("\n自检结论：感知层代码路径可用。真实识别需开通 MaaS 或 OCR。")
        return 0

    if not args.image:
        ap.error("需要图片路径，或用 --selftest")
    if not os.path.isfile(args.image):
        ap.error("找不到图片：%s" % args.image)

    provider = args.provider
    if not provider:
        ext = os.path.splitext(args.image)[1].lower()
        provider = "file" if ext in (".csv", ".tsv", ".xlsx", ".xlsm", ".pdf") else "maas"
        print("按扩展名选定 provider：%s" % provider)

    try:
        rows, meta, report = P.perceive(args.image, provider=provider)
    except P.PerceptionError as e:
        print("感知层失败：", e, file=sys.stderr)
        print("\n失败不等于「这张单据没问题」——上层不得据此输出审计结论。", file=sys.stderr)
        return 2

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    P.write_csv(rows, args.out)
    print_report(rows, meta, report, args.out)

    rp = args.report or (os.path.splitext(args.out)[0] + ".report.json")
    with open(rp, "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "report": report}, f, ensure_ascii=False, indent=2)
    print("解析报告   :", rp)

    if report.get("arith_bad"):
        print("\n注意：算术不符的行要么是原件错、要么是识别错。"
              "**必须回原图核对**，不能直接进审计。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
