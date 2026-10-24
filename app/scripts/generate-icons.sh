#!/bin/bash
# 从 scripts/icons 下的 SVG 源文件重新生成全部 App 图标资源。
# 依赖：macOS 自带的 qlmanage 与 sips，无需安装其他工具。
# 用法：npm run generate-icons
set -euo pipefail

DIR="$(cd "$(dirname "$0")/icons" && pwd)"
ROOT="$(cd "$DIR/../.." && pwd)"
IMAGES="$ROOT/assets/images"

render() { # render <svg> <size>
  qlmanage -t -s "$2" -o "$DIR" "$1" >/dev/null 2>&1
  # qlmanage 偶发因缩略图缓存渲染失败，重试一次
  [ -f "$1.svg.png" ] || qlmanage -t -s "$2" -o "$DIR" "$1" >/dev/null 2>&1
  [ -f "$1.svg.png" ] || { echo "渲染失败：$1" >&2; exit 1; }
  echo "$1.svg.png"
}

cd "$DIR"
FULL=$(render icon-full 1024)
PAW=$(render paw-only 1024)
BG=$(render bg-only 1024)

cp "$DIR/$FULL" "$IMAGES/icon.png"

# splash：白色爪印（背景色由 app.json 的 splash 配置决定）
sips -z 213 228 "$DIR/$PAW" --out "$IMAGES/splash-icon.png" >/dev/null

# Android 自适应图标
sips -z 512 512 "$DIR/$PAW" --out "$IMAGES/android-icon-foreground.png" >/dev/null
sips -z 512 512 "$DIR/$BG" --out "$IMAGES/android-icon-background.png" >/dev/null
sips -z 432 432 "$DIR/$PAW" --out "$IMAGES/android-icon-monochrome.png" >/dev/null

# Web favicon
sips -z 48 48 "$DIR/$FULL" --out "$IMAGES/favicon.png" >/dev/null

# iOS .icon 目录：替换符号 SVG，渐变填充色在 icon.json 中维护
cp "$DIR/paw-only.svg" "$ROOT/assets/expo.icon/Assets/expo-symbol 2.svg"

echo "✅ 图标已生成到 $IMAGES"
