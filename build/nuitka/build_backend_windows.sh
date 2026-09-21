#!/usr/bin/env bash
# Ascend 后端 Windows 交叉编译脚本（wine + mingw-w64）
#
# 用法: bash build/nuitka/build_backend_windows.sh
#
# 原理: wine 运行 Windows Python + Nuitka（Windows 版）；C 编译用
#   mingw-w64（Windows 版 gcc.exe，亦在 wine 下运行）。
#   Nuitka 本体编译使用其自行下载的 winlibs gcc（忽略外部 mingw），
#   本机 mingw 仅用于交叉编译 C 加速模块为 .dll。
#
# 世界区与游戏区是两个独立包（olam / miskhak）：
# 编译时以仓库根作为 PYTHONPATH（两个包均在根）。
# 输出到 build/work/nuitka-win/（构建前清空，非版本化）。
#
# 前置:
#   1. wine 可用；wine 内安装 Windows Python（如 C:\Python312，需 ≤3.12）
#   2. wine 内 pip install nuitka cryptography
#   3. mingw-w64 解压至任意目录（如 ~/mingw64，路径经 MINGW_GCC 指定）
#   4. 本脚本先交叉编译 C 加速模块为 .dll，再打进包
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# 版本资源取共享核心版本（后端二进制为两类包共用；产品版本在包层）
VERSION="$(tr -d '[:space:]' < "$ROOT/build/version/core.txt")"
PRODUCT_VERSION="${VERSION%%-*}"
# 注意: --mingw64 仅支持 Windows Python ≤ 3.12
WIN_PYTHON="${WIN_PYTHON:-C:\\Python312\\python.exe}"
MINGW_GCC="${MINGW_GCC:-$HOME/mingw64/bin/gcc.exe}"
OUT_DIR="$ROOT/build/work/nuitka-win"

# 环境净化：编译进程不得继承 shell 凭据（Nuitka 会把整个环境写进
# build/work/*/run_server.build/scons-debug.py）
source "$ROOT/build/lib/build_env.sh"
WINE_ROOT="Z:$(echo "$ROOT" | sed 's|/|\\|g')"
WINE_PYPATH="$WINE_ROOT"

if ! command -v wine >/dev/null 2>&1; then
  echo "需要 wine" >&2
  exit 1
fi

# 1. C 加速模块 → .dll（交叉编译，Windows 加载用）
cd "$ROOT/olam/generation"
for c in _perlin _hydrology _streamlines; do
  if [ ! -f "$c.dll" ] || [ "$c.c" -nt "$c.dll" ]; then
    echo "编译 $c.dll ..."
    wine "$MINGW_GCC" -O3 -funroll-loops -shared -fPIC -o "$c.dll" "$c.c" -lm 2>/dev/null
  fi
done
cd "$ROOT/olam/modules/terrain"
if [ ! -f _state.dll ] || [ _state.c -nt _state.dll ]; then
  echo "编译 _state.dll ..."
  wine "$MINGW_GCC" -O3 -funroll-loops -shared -fPIC -o _state.dll _state.c -lm 2>/dev/null
fi
cd "$ROOT"

# 2. Nuitka 编译（wine 下运行 Windows Python；standalone 目录模式）
rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR"

# 构建中间目录含环境快照，无论成败都不留在磁盘上
trap 'rm -rf "$OUT_DIR/run_server.build" "$OUT_DIR/server.build"' EXIT

PYTHONPATH="$WINE_PYPATH" ascend_build_env wine "$WIN_PYTHON" -m nuitka \
  --standalone \
  --mingw64 \
  --output-dir="$WINE_ROOT\\build\\work\\nuitka-win" \
  --output-filename="server" \
  --assume-yes-for-downloads \
  --experimental=force-dependencies-pefile \
  --lto=no \
  --jobs=4 \
  --include-package=cryptography \
  --include-data-files="$WINE_ROOT\\olam\\generation\\*.dll=olam/generation/" \
  --include-data-files="$WINE_ROOT\\olam\\modules\\terrain\\*.dll=olam/modules/terrain/" \
  --include-data-files="$WINE_ROOT\\miskhak\\events\\schema.sqlite.sql=miskhak/events/" \
  --include-data-files="$WINE_ROOT\\olam\\declarations\\*.json=olam/declarations/" \
  --nofollow-import-to=pytest \
  --nofollow-import-to=testbench \
  --product-name="Ascend" \
  --product-version="$PRODUCT_VERSION" \
  "$WINE_ROOT\\miskhak\\run_server.py"

# Nuitka 的 dist 目录名取自脚本名（run_server.dist），统一改为 server/
# （与发行布局 <根>/server/server.exe 一致，前端按此路径探测）
mv "$OUT_DIR/run_server.dist" "$OUT_DIR/server"

echo "构建完成: $OUT_DIR/server/server.exe"
