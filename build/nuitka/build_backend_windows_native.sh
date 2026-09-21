#!/usr/bin/env bash
# Ascend 后端 Windows 原生编译脚本（GitHub Actions windows-latest）
#
# 用法: bash build/nuitka/build_backend_windows_native.sh
#
# 与 wine 版（build_backend_windows.sh）等价，但无需 wine：
#   - Python 由 CI 的 setup-python 提供（需 ≤3.12，Nuitka --mingw64 限制）
#   - mingw-w64 需已安装并在 PATH（或经 MINGW_GCC 指定）
#
# 世界区与游戏区是两个独立包（olam / miskhak）：
# 编译时以仓库根作为 PYTHONPATH（两个包均在根）。
#
# 输出到 build/work/nuitka-win/server/（standalone 目录形态）。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# 版本资源取共享核心版本（后端二进制为两类包共用；产品版本在包层）
VERSION="$(tr -d '[:space:]' < "$ROOT/build/version/core.txt")"
PRODUCT_VERSION="${VERSION%%-*}"
MINGW_GCC="${MINGW_GCC:-gcc}"
OUT_DIR="$ROOT/build/work/nuitka-win"

# 环境净化：编译进程不得继承 shell 凭据（Nuitka 会把整个环境写进
# build/work/*/run_server.build/scons-debug.py）
source "$ROOT/build/lib/build_env.sh"
# Nuitka（Windows Python）需要 Windows 风格路径；参数统一用正斜杠
WIN_ROOT="$(cygpath -w "$ROOT" | tr '\\' '/')"

# 1. C 加速模块 → .dll（原生 gcc/mingw）
cd "$ROOT/olam/generation"
for c in _perlin _hydrology _streamlines; do
  if [ ! -f "$c.dll" ] || [ "$c.c" -nt "$c.dll" ]; then
    echo "编译 $c.dll ..."
    "$MINGW_GCC" -O3 -funroll-loops -shared -fPIC -o "$c.dll" "$c.c" -lm
  fi
done
cd "$ROOT/olam/modules/terrain"
if [ ! -f _state.dll ] || [ _state.c -nt _state.dll ]; then
  echo "编译 _state.dll ..."
  "$MINGW_GCC" -O3 -funroll-loops -shared -fPIC -o _state.dll _state.c -lm
fi
cd "$ROOT"

# 2. Nuitka 编译（原生 Windows；--mingw64 与 wine 版产物同源同构）
rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR"

# 构建中间目录含环境快照，无论成败都不留在磁盘上
trap 'rm -rf "$OUT_DIR/run_server.build" "$OUT_DIR/server.build"' EXIT

PYTHONPATH="$WIN_ROOT" ascend_build_env python -m nuitka \
  --standalone \
  --mingw64 \
  --output-dir="$WIN_ROOT/build/work/nuitka-win" \
  --output-filename="server" \
  --assume-yes-for-downloads \
  --jobs=8 \
  --include-package=cryptography \
  --include-data-files="$WIN_ROOT/olam/generation/*.dll=olam/generation/" \
  --include-data-files="$WIN_ROOT/olam/modules/terrain/*.dll=olam/modules/terrain/" \
  --include-data-files="$WIN_ROOT/miskhak/events/schema.sqlite.sql=miskhak/events/" \
  --include-data-files="$WIN_ROOT/olam/declarations/*.json=olam/declarations/" \
  --nofollow-import-to=pytest \
  --nofollow-import-to=testbench \
  --product-name="Ascend" \
  --product-version="$PRODUCT_VERSION" \
  "$WIN_ROOT/miskhak/run_server.py"

# Nuitka 的 dist 目录名取自脚本名（run_server.dist），统一改为 server/
mv "$OUT_DIR/run_server.dist" "$OUT_DIR/server"

echo "构建完成: $OUT_DIR/server/server.exe"
