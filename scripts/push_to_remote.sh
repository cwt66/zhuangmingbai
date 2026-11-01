#!/usr/bin/env bash
# 一键推送到远程仓库（Gitee / GitHub 通用）
#
# 用法:
#   bash scripts/push_to_remote.sh git@gitee.com:你的用户名/zhuangmingbai.git
#   bash scripts/push_to_remote.sh https://gitee.com/你的用户名/zhuangmingbai.git
#   bash scripts/push_to_remote.sh https://<TOKEN>@github.com/你的用户名/zhuangmingbai.git
#
# 说明:
# - 已经在网页端建好空仓库（不要再勾选自动生成 README，否则需要先 pull）
# - 本脚本只加 remote 并推送当前分支；不改历史、不 force
set -euo pipefail

URL="${1:-}"
if [ -z "$URL" ]; then
  echo "用法: bash scripts/push_to_remote.sh <仓库地址>"
  echo "示例: bash scripts/push_to_remote.sh git@gitee.com:user/zhuangmingbai.git"
  exit 1
fi

case "$URL" in
  *gitee.com*|*github.com*) ;;
  *) echo "!! 地址不像 Gitee/GitHub: $URL"; exit 1 ;;
esac

cd "$(dirname "$0")/.."
BRANCH="$(git rev-parse --abbrev-ref HEAD)"

if git remote | grep -qx origin; then
  echo "== origin 已存在，更新地址"
  git remote set-url origin "$URL"
else
  echo "== 添加 origin"
  git remote add origin "$URL"
fi

echo "== 推送分支 $BRANCH（$(git rev-list --count HEAD) 个 commit）"
git push -u origin "$BRANCH"

echo
echo "== 完成。打开仓库确认 README 渲染与 License 识别："
echo "   $URL"
