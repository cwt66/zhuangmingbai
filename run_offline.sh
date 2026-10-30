#!/usr/bin/env bash
# 断网演示入口：不依赖网络、不依赖第三方包也能看到完整审计报告
set -e
cd "$(dirname "$0")"

echo "== 1/2 运行审计引擎（零第三方依赖）=="
python3 src/pipeline.py

REPORT="out/audit_report.html"
if [ ! -f "$REPORT" ]; then
  echo "报告未生成，请检查上面的报错"; exit 1
fi
echo "== 2/2 报告已生成：$REPORT =="
echo
echo "打开方式（任选其一）："
echo "  1) 直接双击 $REPORT"
echo "  2) 命令行：open $REPORT          （macOS）"
echo "            xdg-open $REPORT       （Linux）"
echo
echo "如需交互式 Demo（上传 / 校对 / 真实单据），执行："
echo "  python3 -m pip install -r requirements.txt"
echo "  python3 -m uvicorn main:app --port 8000"
echo "  然后浏览器打开 http://127.0.0.1:8000"

# 有图形界面就自动打开
if command -v open >/dev/null 2>&1; then open "$REPORT" 2>/dev/null || true
elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$REPORT" >/dev/null 2>&1 || true
fi
