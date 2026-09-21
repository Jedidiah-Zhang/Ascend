#!/usr/bin/env bash
# 游戏包 Windows 压缩包（zip）→ build/dist/miskhak/ascend-game-windows.zip
#
# 用法: bash build/package/miskhak/windows/make_zip.sh
# 前置: bash build/package/miskhak/assemble.sh windows
# 归档内顶层目录为对外产品名 Ascend-Game（舞台目录名是内部通道名）。
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)/lib/common.sh"

CHANNEL=miskhak
PLATFORM=windows
STAGE="$(ascend_stage_dir "$CHANNEL" "$PLATFORM")"
TOP="$(ascend_stage_top "$CHANNEL")"
ARCHIVE="$(ascend_dist_dir "$CHANNEL")/$(ascend_artifact_base "$CHANNEL" "$PLATFORM").zip"

[ -d "$STAGE" ] || ascend_die "舞台目录不存在: $STAGE（先运行 build/package/miskhak/assemble.sh windows）"

python3 "$ASCEND_ROOT/build/lib/make_zip.py" --stage "$STAGE" --top "$TOP" --output "$ARCHIVE"
du -sh "$ARCHIVE"
