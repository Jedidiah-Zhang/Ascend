#!/usr/bin/env bash
# Ascend 后端 Windows 交叉编译脚本（wine + mingw-w64）
#
# 用法: bash build/nuitka/build_backend_windows.sh
#
# 原理: Nuitka 不支持 Linux→Windows 直接交叉编译，采用官方路线——
#   wine 运行 Windows Python + Nuitka（Windows 版），C 编译用
#   mingw-w64（Windows 版 gcc.exe，亦在 wine 下运行）。
#   Nuitka 本体编译使用其自行下载的 winlibs gcc（忽略外部 mingw），
#   本机 mingw 仅用于交叉编译 C 加速模块为 .dll。
#
# 输出到 build/work/nuitka-win/（构建前清空，非版本化）。
#
# 前置:
#   1. wine 可用；wine 内安装 Windows Python（如 C:\Python312，需 ≤3.12）
#   2. wine 内 pip install nuitka cryptography
#   3. mingw-w64 解压至任意目录（如 ~/mingw64，路径经 MINGW_GCC 指定）
#   4. 本脚本先交叉编译 C 加速模块为 .dll，再打进包
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VERSION="$(cat "$ROOT/build/nuitka/version.txt")"
PRODUCT_VERSION="${VERSION%%-*}"
# 注意: --mingw64 仅支持 Windows Python ≤ 3.12
WIN_PYTHON="${WIN_PYTHON:-C:\\Python312\\python.exe}"
MINGW_GCC="${MINGW_GCC:-$HOME/mingw64/bin/gcc.exe}"
OUT_DIR="$ROOT/build/work/nuitka-win"
WINE_ROOT="Z:$(echo "$ROOT" | sed 's|/|\\|g')"

if ! command -v wine >/dev/null 2>&1; then
  echo "需要 wine" >&2
  exit 1
fi

# 1. C 加速模块 → .dll（交叉编译，Windows 加载用）
cd "$ROOT/backend/ascend/space"
for c in _perlin _hydrology _streamlines _state; do
  if [ ! -f "$c.dll" ] || [ "$c.c" -nt "$c.dll" ]; then
    echo "编译 $c.dll ..."
    wine "$MINGW_GCC" -O3 -funroll-loops -shared -fPIC -o "$c.dll" "$c.c" -lm 2>/dev/null
  fi
done
cd "$ROOT"

# 2. Nuitka 编译（wine 下运行 Windows Python；standalone 目录模式——
#    不用 onefile：Linux 上 onefile 会 fork 子进程破坏前端 PID 语义）
rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR"
wine "$WIN_PYTHON" -m nuitka \
  --standalone \
  --mingw64 \
  --output-dir="$WINE_ROOT\\build\\work\\nuitka-win" \
  --output-filename="server" \
  --assume-yes-for-downloads \
  --experimental=force-dependencies-pefile \
  --lto=no \
  --jobs=4 \
  --include-package=cryptography \
  --include-data-files="$WINE_ROOT\\backend\\ascend\\space\\*.dll=ascend\\space\\" \
  --include-data-files="$WINE_ROOT\\backend\\ascend\\world_tree\\schema.sqlite.sql=ascend\\world_tree\\" \
  --nofollow-import-to=pytest \
  --nofollow-import-to=tests \
  --product-name="Ascend" \
  --product-version="$PRODUCT_VERSION" \
  "$WINE_ROOT\\backend\\run_server.py"

# Nuitka 的 dist 目录名取自脚本名（run_server.dist），统一改为 server/
# （与发行布局 <根>/server/server.exe 一致，前端按此路径探测）
mv "$OUT_DIR/run_server.dist" "$OUT_DIR/server"

echo "构建完成: $OUT_DIR/server/server.exe"
