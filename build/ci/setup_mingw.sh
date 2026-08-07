#!/usr/bin/env bash
# CI 工具：安装 mingw-w64（winlibs，Windows runner 专用）
#
# 用法: bash build/ci/setup_mingw.sh
#   - 解压到 ~/mingw64，bin 目录写入 GITHUB_PATH
#   - 用途：交叉编译 C 加速模块为 .dll；Nuitka --mingw64 亦可复用
#
# 依赖: curl + python3（解压 zip）。
set -euo pipefail

# winlibs x86_64-posix-seh（msvcrt），版本与本地 wine 构建一致
ZIP_URL="https://github.com/brechtsanders/winlibs_mingw/releases/download/16.1.0posix-14.0.0-msvcrt-r4/winlibs-x86_64-posix-seh-gcc-16.1.0-mingw-w64msvcrt-14.0.0-r4.zip"
DEST="$HOME/mingw64"

if [ ! -f "$DEST/bin/gcc.exe" ]; then
  echo "下载 mingw-w64 ..."
  curl -sL -o /tmp/mingw.zip "$ZIP_URL"
  rm -rf "$DEST"
  python3 -m zipfile -e /tmp/mingw.zip "$HOME/"
fi

echo "$DEST/bin" >> "$GITHUB_PATH"
echo "mingw-w64 就绪: $DEST/bin/gcc.exe"
