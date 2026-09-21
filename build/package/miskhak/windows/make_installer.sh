#!/usr/bin/env bash
# 游戏包 Windows 安装器（Inno Setup）→ build/dist/miskhak/ascend-game-windows-setup.exe
#
# 用法: bash build/package/miskhak/windows/make_installer.sh
# 前置: 舞台目录已组装；ISCC.exe 在 PATH（CI: choco install innosetup imagemagick）
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)/lib/common.sh"

CHANNEL=miskhak
PLATFORM=windows
VERSION="$(ascend_version "$CHANNEL")"
VER_NUM="$(ascend_numeric_version "$VERSION")"
STAGE="$(ascend_stage_dir "$CHANNEL" "$PLATFORM")"
RELEASE_DIR="$(ascend_dist_dir "$CHANNEL")"

command -v ISCC.exe >/dev/null 2>&1 || { echo "跳过安装器：缺少 ISCC.exe"; exit 0; }
[ -d "$STAGE" ] || ascend_die "舞台目录不存在: $STAGE（先运行 build/package/miskhak/assemble.sh windows）"

mkdir -p "$RELEASE_DIR"

# SVG → ICO（ImageMagick；windows runner 预装）
command -v magick >/dev/null 2>&1 || { echo "跳过安装器：缺少 ImageMagick"; exit 0; }
magick "$ASCEND_ROOT/build/assets/ascend.svg" \
  -define icon:auto-resize=16,24,32,48,64,128,256 \
  "$ASCEND_ROOT/build/work/ascend.ico"

STAGE_W="$(cygpath -w "$STAGE")"
ISS_W="$(cygpath -w "$ASCEND_ROOT/build/package/miskhak/windows/ascend.iss")"
OUT_W="$(cygpath -w "$RELEASE_DIR")"
ICO_W="$(cygpath -w "$ASCEND_ROOT/build/work/ascend.ico")"

# MSYS2_ARG_CONV_EXCL：Git Bash 会把 /Dxxx 参数误转为路径（MSYS 路径
# 转换），禁用后 /D 定义原样传给 ISCC
MSYS2_ARG_CONV_EXCL="*" ISCC.exe "$ISS_W" \
  /DStage="$STAGE_W" /DVersion="$VERSION" /DVerNum="$VER_NUM" \
  /DOutDir="$OUT_W" /DIcon="$ICO_W" >/dev/null

echo "已生成: $RELEASE_DIR/ascend-game-windows-setup.exe"
