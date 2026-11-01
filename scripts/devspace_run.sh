#!/usr/bin/env bash
# 在「华为云 AI 开发者空间」沙箱内一键启动装明白 Demo，并给出公网访问步骤
#
# 前置：沙箱里已克隆仓库
#   git clone https://gitee.com/Cwt66/zhuangmingbai.git && cd zhuangmingbai
#
# 用法：
#   bash scripts/devspace_run.sh          # 默认 8080 端口
#   bash scripts/devspace_run.sh 9000     # 指定端口
#
# 重要（踩过的坑，官方文档也写）：
#   必须绑定 0.0.0.0。绑 127.0.0.1 时 DevBridge 隧道"通但 502"。
set -euo pipefail
PORT="${1:-8080}"

echo "== 1/3 安装依赖（沙箱已带 python3）"
python3 -m pip install -q --upgrade pip
python3 -m pip install -q -r requirements.txt

echo "== 2/3 自检（26 个单测，约 1 秒）"
python3 -m unittest discover -s tests 2>&1 | tail -2

echo "== 3/3 启动服务（0.0.0.0:${PORT}）"
echo "   启动后：在沙箱左侧/顶部找到 DevBridge（开发者隧道）→ 映射 ${PORT} 端口 → 得到"
echo "   https://<随机串>-${PORT}.devbridge-s2.hwtunnel.com 的 HTTPS 公网地址"
echo "   验证：curl -sI https://<上述域名>/ 应返回 HTTP 200/307"
echo "   首次浏览器打开会弹安全警告页 → 点「继续访问」"
echo
exec python3 -m uvicorn main:app --host 0.0.0.0 --port "${PORT}"
