#!/usr/bin/env bash
# 研究包归档：舞台目录 → build/dist/kheker/ascend-research-<平台>.{tar.gz|zip}
#
# 用法: bash build/package/kheker/archive.sh <linux|windows>
# 归档内顶层目录为对外产品名 Ascend-Research（舞台目录名是内部通道名）。
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/lib/common.sh"

PLATFORM="${1:?用法: archive.sh <linux|windows>}"
ascend_require_one_platform "$PLATFORM"
CHANNEL=kheker
STAGE="$(ascend_stage_dir "$CHANNEL" "$PLATFORM")"
TOP="$(ascend_stage_top "$CHANNEL")"
RELEASE_DIR="$(ascend_dist_dir "$CHANNEL")"
BASE="$(ascend_artifact_base "$CHANNEL" "$PLATFORM")"

[ -d "$STAGE" ] || ascend_die "舞台目录不存在: $STAGE（先运行 build/package/kheker/assemble.sh $PLATFORM）"

mkdir -p "$RELEASE_DIR"

if [ "$PLATFORM" = "linux" ]; then
  ARCHIVE="$RELEASE_DIR/$BASE.tar.gz"
  rm -f "$ARCHIVE"
  cd "$(dirname "$STAGE")"
  tar -czf "$ARCHIVE" --transform "s|^$(basename "$STAGE")|$TOP|" "$(basename "$STAGE")"
else
  ARCHIVE="$RELEASE_DIR/$BASE.zip"
  python3 "$ASCEND_ROOT/build/lib/make_zip.py" --stage "$STAGE" --top "$TOP" --output "$ARCHIVE"
fi

echo "已生成: $ARCHIVE"
du -sh "$ARCHIVE"
