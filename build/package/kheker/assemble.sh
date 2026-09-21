#!/usr/bin/env bash
# 研究包（kheker → research）舞台组装：共享后端舞台 + 运行说明。
#
# 用法: bash build/package/kheker/assemble.sh <linux|windows>
# 前置: 后端已编译（build/nuitka/）
# 产物: build/work/staging/kheker-<平台>/（server/ + data/ + lang/）
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/lib/common.sh"
source "$ASCEND_ROOT/build/lib/stage.sh"

PLATFORM="${1:?用法: assemble.sh <linux|windows>}"
ascend_require_one_platform "$PLATFORM"
CHANNEL=kheker
VERSION="$(ascend_version "$CHANNEL")"
STAGE="$(ascend_stage_dir "$CHANNEL" "$PLATFORM")"

ascend_stage_backend "$CHANNEL" "$PLATFORM"

cat > "$STAGE/README.txt" <<EOF
Ascend 研究包 $VERSION ($PLATFORM)

运行: ./server/server[.exe] --project-root <本目录>
（服务模式监听 127.0.0.1:9081；ASCEND_SAVE_ROOT 可重定向存档目录）
EOF

echo "舞台目录已组装: $STAGE"
du -sh "$STAGE"
