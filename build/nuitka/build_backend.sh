#!/usr/bin/env bash
# Ascend 后端编译脚本（Nuitka，Linux，standalone 目录模式）
#
# 用法: bash build/nuitka/build_backend.sh
#
# 输出到 build/work/nuitka/server/（standalone 目录形态：二进制 + 依赖库）。
#
# 世界区与游戏区是两个独立包（olam / miskhak）：
# 编译时以仓库根作为 {PYTHONPATH}（两个包均在根）。
#
# 版本号仅发布时使用（见 build/ci/publish_release.sh）。
#
# 前置: ../.venv/bin/pip install -r build/nuitka/requirements-build.txt
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# 版本资源取共享核心版本（后端二进制为两类包共用；产品版本在包层）
VERSION="$(tr -d '[:space:]' < "$ROOT/build/version/core.txt")"
PRODUCT_VERSION="${VERSION%%-*}"  # 版本资源仅取数字段（0.0.3-alpha → 0.0.3）

# Python 解释器：本地开发用 .venv；CI 无 venv 时回退系统 python
if [ -x "$ROOT/.venv/bin/python" ]; then
  VENV_PY="$ROOT/.venv/bin/python"
else
  VENV_PY="$(command -v python3 || command -v python)"
fi

OUT_DIR="$ROOT/build/work/nuitka"
DIST_NAME="server"

# 环境净化：编译进程不得继承 shell 凭据（Nuitka 会把整个环境写进
# build/work/*/run_server.build/scons-debug.py）
source "$ROOT/build/lib/build_env.sh"

EXCLUDES=()
while IFS= read -r line; do
  [[ -z "$line" || "$line" == \#* ]] && continue
  EXCLUDES+=(--nofollow-import-to="$line")
done < "$ROOT/build/nuitka/excludes.txt"

PYPATH="$ROOT"
# C 加速模块（ctypes 加载）：先确保 .so 为最新（缺失/过期自动重编译）。
# generation 三件 + 地形状态内核（olam/modules/terrain/_state.c）。
cd "$ROOT/olam"
PYTHONPATH="$PYPATH" "$VENV_PY" -c \
  "from olam.generation import noise, hydrology, streamlines; from olam.adapters.terrain.tile_state import _N_STATES; print('C 扩展就绪')"
cd "$ROOT"

rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR"

# 构建中间目录含环境快照，无论成败都不留在磁盘上
trap 'rm -rf "$OUT_DIR/run_server.build" "$OUT_DIR/server.build"' EXIT

PYTHONPATH="$PYPATH" ascend_build_env "$VENV_PY" -m nuitka \
  --standalone \
  --output-dir="$OUT_DIR" \
  --output-filename="$DIST_NAME" \
  --assume-yes-for-downloads \
  --jobs=8 \
  --clang \
  --include-package=cryptography \
  --include-data-files="$ROOT/olam/generation/*.so=olam/generation/" \
  --include-data-files="$ROOT/olam/modules/terrain/*.so=olam/modules/terrain/" \
  --include-data-files="$ROOT/miskhak/events/schema.sqlite.sql=miskhak/events/" \
  --include-data-files="$ROOT/olam/declarations/*.json=olam/declarations/" \
  "${EXCLUDES[@]}" \
  --product-name="Ascend" \
  --product-version="$PRODUCT_VERSION" \
  "$ROOT/miskhak/run_server.py"

# Nuitka 的 dist 目录名取自脚本名（run_server.dist），统一改为 server/
# （与发行布局 <根>/server/server 一致，前端按此路径探测）
mv "$OUT_DIR/run_server.dist" "$OUT_DIR/server"

echo "构建完成: $OUT_DIR/server/"
