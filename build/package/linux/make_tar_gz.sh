#!/usr/bin/env bash
# Linux 发布压缩包（tar.gz）：舞台目录 → 最终交付物，打包后删除舞台目录
#
# 用法: bash build/package/linux/make_tar_gz.sh
# 前置: bash build/package/assemble_release.sh linux
# 产物: build/dist/release/ascend-linux.tar.gz（固定名，每次覆盖）
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
STAGE="$ROOT/build/work/staging/Ascend-linux"
RELEASE_DIR="$ROOT/build/dist/release"
ARCHIVE="$RELEASE_DIR/ascend-linux.tar.gz"

if [ ! -d "$STAGE" ]; then
  echo "舞台目录不存在: $STAGE（先运行 assemble_release.sh linux）" >&2
  exit 1
fi

mkdir -p "$RELEASE_DIR"
rm -f "$ARCHIVE"

cd "$ROOT/build/work/staging"
tar -czf "$ARCHIVE" "Ascend-linux"

echo "已生成: $ARCHIVE"
du -sh "$ARCHIVE"
