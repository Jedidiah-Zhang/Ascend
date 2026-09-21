#!/usr/bin/env bash
# 研究包（kheker → research）完整流程：编译后端 → 组装舞台 → 冒烟 → 归档。
#
# 用法: bash build/package/kheker/build.sh <linux|windows>
# 产物: build/dist/kheker/（固定名，每次覆盖；版本化命名发生在发布时刻）
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/lib/common.sh"
source "$ASCEND_ROOT/build/lib/stage.sh"

PLATFORM="${1:?用法: build.sh <linux|windows>}"
ascend_require_one_platform "$PLATFORM"
CHANNEL=kheker
STAGE="$(ascend_stage_dir "$CHANNEL" "$PLATFORM")"

# ── 1. 编译后端 ──────────────────────────────────────────────
ascend_info "[research] 编译后端 $PLATFORM ..."
ascend_build_backend "$PLATFORM"

# ── 2. 组装 + 冒烟 ───────────────────────────────────────────
ascend_info "[research] 组装 + 冒烟 $PLATFORM ..."
bash "$ASCEND_ROOT/build/package/$CHANNEL/assemble.sh" "$PLATFORM"
ascend_smoke "$CHANNEL" "$PLATFORM"

# ── 3. 归档 + 清理 ───────────────────────────────────────────
ascend_info "[research] 归档 $PLATFORM ..."
bash "$ASCEND_ROOT/build/package/$CHANNEL/archive.sh" "$PLATFORM"
rm -rf "$STAGE"

echo "已生成（build/dist/$CHANNEL/）:"
ls -la "$(ascend_dist_dir "$CHANNEL")"
