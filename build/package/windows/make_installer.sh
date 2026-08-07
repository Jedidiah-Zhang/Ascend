#!/usr/bin/env bash
# Windows 安装器（Inno Setup）：舞台目录 → 安装程序
#
# 用法: bash build/package/windows/make_installer.sh
# 前置: 舞台目录已组装；ISCC.exe 在 PATH（CI: choco install innosetup imagemagick）
# 产物: build/dist/release/ascend-windows-setup.exe
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
VERSION="$(cat "$ROOT/build/nuitka/version.txt")"
VER_NUM="${VERSION%%-*}"
STAGE="$ROOT/build/work/staging/Ascend-windows"
RELEASE_DIR="$ROOT/build/dist/release"

command -v ISCC.exe >/dev/null 2>&1 || { echo "跳过安装器：缺少 ISCC.exe"; exit 0; }
[ -d "$STAGE" ] || { echo "缺少舞台目录: $STAGE" >&2; exit 1; }

mkdir -p "$RELEASE_DIR"

# SVG → ICO（ImageMagick；windows runner 预装）
command -v magick >/dev/null 2>&1 || { echo "跳过安装器：缺少 ImageMagick"; exit 0; }
magick "$ROOT/build/assets/ascend.svg" \
  -define icon:auto-resize=16,24,32,48,64,128,256 \
  "$ROOT/build/work/ascend.ico"

STAGE_W="$(cygpath -w "$STAGE")"
ISS_W="$(cygpath -w "$ROOT/build/package/windows/ascend.iss")"
OUT_W="$(cygpath -w "$RELEASE_DIR")"
ICO_W="$(cygpath -w "$ROOT/build/work/ascend.ico")"

# MSYS2_ARG_CONV_EXCL：Git Bash 会把 /Dxxx 参数误转为路径（MSYS 路径
# 转换），禁用后 /D 定义原样传给 ISCC
MSYS2_ARG_CONV_EXCL="*" ISCC.exe "$ISS_W" \
  /DStage="$STAGE_W" /DVersion="$VERSION" /DVerNum="$VER_NUM" \
  /DOutDir="$OUT_W" /DIcon="$ICO_W" >/dev/null

echo "已生成: $RELEASE_DIR/ascend-windows-setup.exe"
