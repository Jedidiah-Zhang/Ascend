#!/usr/bin/env bash
# Windows 发布压缩包（zip）：舞台目录 → 最终交付物，打包后删除舞台目录
#
# 用法: bash build/package/windows/make_zip.sh
# 前置: bash build/package/assemble_release.sh windows
# 产物: build/dist/release/ascend-windows.zip（固定名，每次覆盖）
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
STAGE_NAME="Ascend-windows"
STAGE="$ROOT/build/work/staging/$STAGE_NAME"
RELEASE_DIR="$ROOT/build/dist/release"
ARCHIVE="$RELEASE_DIR/ascend-windows.zip"

if [ ! -d "$STAGE" ]; then
  echo "舞台目录不存在: $STAGE（先运行 assemble_release.sh windows）" >&2
  exit 1
fi

mkdir -p "$RELEASE_DIR"
rm -f "$ARCHIVE"

if command -v zip >/dev/null 2>&1; then
  (cd "$ROOT/build/work/staging" && zip -qr "$ARCHIVE" "$STAGE_NAME")
else
  # 无 zip 命令时的兜底（Python zipfile，DEFLATE）
  python3 - "$ARCHIVE" "$STAGE_NAME" <<'EOF'
import sys, zipfile
from pathlib import Path
archive, stage_name = Path(sys.argv[1]), sys.argv[2]
base = archive.parent.parent.parent / "work" / "staging"
with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
    for f in sorted((base / stage_name).rglob("*")):
        if f.is_file():
            zf.write(f, f.relative_to(base))
EOF
fi


echo "已生成: $ARCHIVE"
du -sh "$ARCHIVE"
