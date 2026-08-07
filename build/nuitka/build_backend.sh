#!/usr/bin/env bash
# Ascend 后端编译脚本（Nuitka，Linux，standalone 目录模式）
#
# 用法: bash build/nuitka/build_backend.sh
#
# 输出到 build/work/nuitka/server/（目录形态：二进制 + 依赖库）。
# 不用 onefile：其一 file 在 Linux 上会 fork 出子进程（bootstrap 监督
# 进程 + 真实服务），前端按 PID 无法可靠终止；standalone 下二进制即
# 服务本身，PID/SIGTERM 语义与前端进程模型一致。
#
# 版本号仅发布时使用（见 build/ci/publish_release.sh）。
#
# 前置: ../.venv/bin/pip install -r build/nuitka/requirements-build.txt
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VENV_PY="$ROOT/.venv/bin/python"
VERSION="$(cat "$ROOT/build/nuitka/version.txt")"
PRODUCT_VERSION="${VERSION%%-*}"  # 版本资源仅取数字段（0.0.1-alpha → 0.0.1）

OUT_DIR="$ROOT/build/work/nuitka"
DIST_NAME="server"

EXCLUDES=()
while IFS= read -r line; do
  [[ -z "$line" || "$line" == \#* ]] && continue
  EXCLUDES+=(--nofollow-import-to="$line")
done < "$ROOT/build/nuitka/excludes.txt"

# C 加速模块（ctypes 加载）：先确保 .so 为最新（缺失/过期自动重编译）。
cd "$ROOT/backend"
PYTHONPATH="$ROOT/backend" "$VENV_PY" -c \
  "from ascend.space import noise, hydrology, streamlines; print('C 扩展就绪')"
cd "$ROOT"

rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR"

"$VENV_PY" -m nuitka \
  --standalone \
  --output-dir="$OUT_DIR" \
  --output-filename="$DIST_NAME" \
  --assume-yes-for-downloads \
  --jobs=8 \
  --clang \
  --include-package=cryptography \
  --include-data-files="$ROOT/backend/ascend/space/*.so=ascend/space/" \
  --include-data-files="$ROOT/backend/ascend/world_tree/schema.sqlite.sql=ascend/world_tree/" \
  "${EXCLUDES[@]}" \
  --product-name="Ascend" \
  --product-version="$PRODUCT_VERSION" \
  "$ROOT/backend/run_server.py"

# Nuitka 的 dist 目录名取自脚本名（run_server.dist），统一改为 server/
# （与发行布局 <根>/server/server 一致，前端按此路径探测）
mv "$OUT_DIR/run_server.dist" "$OUT_DIR/server"

echo "构建完成: $OUT_DIR/server/"
