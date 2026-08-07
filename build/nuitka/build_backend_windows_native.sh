#!/usr/bin/env bash
# Ascend 后端 Windows 原生编译脚本（GitHub Actions windows-latest）
#
# 用法: bash build/nuitka/build_backend_windows_native.sh
#
# 与 wine 版（build_backend_windows.sh）等价，但无需 wine：
#   - Python 由 CI 的 setup-python 提供（需 ≤3.12，Nuitka --mingw64 限制）
#   - mingw-w64 需已安装并在 PATH（或经 MINGW_GCC 指定）
#
# 输出到 build/work/nuitka-win/server/（standalone 目录形态）。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VERSION="$(cat "$ROOT/build/nuitka/version.txt")"
PRODUCT_VERSION="${VERSION%%-*}"
MINGW_GCC="${MINGW_GCC:-gcc}"
OUT_DIR="$ROOT/build/work/nuitka-win"
# Nuitka（Windows Python）需要 Windows 风格路径；参数统一用正斜杠
WIN_ROOT="$(cygpath -w "$ROOT" | tr '\\' '/')"

# 1. C 加速模块 → .dll（原生 gcc/mingw）
cd "$ROOT/backend/ascend/space"
for c in _perlin _hydrology _streamlines; do
  if [ ! -f "$c.dll" ] || [ "$c.c" -nt "$c.dll" ]; then
    echo "编译 $c.dll ..."
    "$MINGW_GCC" -O3 -funroll-loops -shared -fPIC -o "$c.dll" "$c.c" -lm
  fi
done
cd "$ROOT"

# 2. Nuitka 编译（原生 Windows；--mingw64 与 wine 版产物同源同构）
rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR"
python -m nuitka \
  --standalone \
  --mingw64 \
  --output-dir="$WIN_ROOT/build/work/nuitka-win" \
  --output-filename="server" \
  --assume-yes-for-downloads \
  --jobs=8 \
  --include-package=cryptography \
  --include-data-files="$WIN_ROOT/backend/ascend/space/*.dll=ascend/space/" \
  --include-data-files="$WIN_ROOT/backend/ascend/world_tree/schema.sqlite.sql=ascend/world_tree/" \
  --nofollow-import-to=pytest \
  --nofollow-import-to=tests \
  --product-name="Ascend" \
  --product-version="$PRODUCT_VERSION" \
  "$WIN_ROOT/backend/run_server.py"

# Nuitka 的 dist 目录名取自脚本名（run_server.dist），统一改为 server/
mv "$OUT_DIR/run_server.dist" "$OUT_DIR/server"

echo "构建完成: $OUT_DIR/server/server.exe"
