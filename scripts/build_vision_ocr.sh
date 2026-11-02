#!/usr/bin/env bash
# 编译 macOS Vision OCR 小工具（仅 macOS；其他平台不编译，provider=vision 不可用）。
# 用法：bash scripts/build_vision_ocr.sh
set -e
cd "$(dirname "$0")"
mkdir -p bin

# 选择与本机 clang 匹配的 SDK：优先 26.5，其次 15.4，最后默认
SDK=""
for s in MacOSX26.5.sdk MacOSX26.sdk MacOSX15.4.sdk MacOSX15.sdk; do
  if [ -d "/Library/Developer/CommandLineTools/SDKs/$s" ]; then
    SDK="/Library/Developer/CommandLineTools/SDKs/$s"
    break
  fi
done

if [ -z "$SDK" ]; then
  echo "未找到 CommandLineTools SDK，尝试默认 SDK"
  clang -O2 -fobjc-arc -o bin/vision_ocr vision_ocr.m \
    -framework Foundation -framework Vision -framework AppKit
else
  echo "使用 SDK: $SDK"
  clang -O2 -fobjc-arc -isysroot "$SDK" -o bin/vision_ocr vision_ocr.m \
    -framework Foundation -framework Vision -framework AppKit
fi

chmod +x bin/vision_ocr
echo "OK -> $(pwd)/bin/vision_ocr"
"$(pwd)/bin/vision_ocr" 2>&1 | head -1 || true
