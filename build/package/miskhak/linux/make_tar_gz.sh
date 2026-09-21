#!/usr/bin/env bash
# 游戏包 Linux 压缩包（tar.gz）→ build/dist/miskhak/ascend-game-linux.tar.gz
#
# 用法: bash build/package/miskhak/linux/make_tar_gz.sh
# 前置: bash build/package/miskhak/assemble.sh linux
# 归档内顶层目录为对外产品名 Ascend-Game（舞台目录名是内部通道名）。
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)/lib/common.sh"

CHANNEL=miskhak
PLATFORM=linux
STAGE="$(ascend_stage_dir "$CHANNEL" "$PLATFORM")"
TOP="$(ascend_stage_top "$CHANNEL")"
ARCHIVE="$(ascend_dist_dir "$CHANNEL")/$(ascend_artifact_base "$CHANNEL" "$PLATFORM").tar.gz"

[ -d "$STAGE" ] || ascend_die "舞台目录不存在: $STAGE（先运行 build/package/miskhak/assemble.sh linux）"

mkdir -p "$(dirname "$ARCHIVE")"
rm -f "$ARCHIVE"

cd "$(dirname "$STAGE")"
tar -czf "$ARCHIVE" --transform "s|^$(basename "$STAGE")|$TOP|" "$(basename "$STAGE")"

echo "已生成: $ARCHIVE"
du -sh "$ARCHIVE"
