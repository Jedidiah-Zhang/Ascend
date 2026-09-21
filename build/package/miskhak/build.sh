#!/usr/bin/env bash
# 游戏包（miskhak → game）完整流程：同步客户端资源 → 导出前端 → 编译后端 →
# 组装舞台 → 冒烟 → 归档（各格式）。
#
# 用法: bash build/package/miskhak/build.sh <linux|windows>
# 产物: build/dist/miskhak/（固定名，每次覆盖）
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/lib/common.sh"
source "$ASCEND_ROOT/build/lib/stage.sh"

PLATFORM="${1:?用法: build.sh <linux|windows>}"
ascend_require_one_platform "$PLATFORM"
CHANNEL=miskhak
VERSION="$(ascend_version "$CHANNEL")"
STAGE="$(ascend_stage_dir "$CHANNEL" "$PLATFORM")"

# ── 0. 同步语言文件与版本号到客户端项目（进 PCK）────────────────
# 语言文件（游戏区 miskhak/lang/）先拷入客户端项目使其进入导出资源；
# 版本号（build/version/miskhak.txt）同样入 PCK，主菜单据此显示。
ascend_info "[game] 同步语言与版本文档到客户端 ..."
rm -rf "$ASCEND_ROOT/miskhak/client/lang"
mkdir -p "$ASCEND_ROOT/miskhak/client/lang"
cp "$ASCEND_ROOT/miskhak/lang/"*.json "$ASCEND_ROOT/miskhak/client/lang/"
cp "$ASCEND_ROOT/build/version/$CHANNEL.txt" "$ASCEND_ROOT/miskhak/client/version.txt"

# ── 1. 导出前端（目标目录须先存在：Godot 导出不自动建目录）──────
ascend_info "[game] 导出前端 $PLATFORM ..."
mkdir -p "$ASCEND_ROOT/build/work/exports/$PLATFORM"
godot --headless --path "$ASCEND_ROOT/miskhak/client" \
  --export-release "$(ascend_godot_preset "$PLATFORM")"

# ── 2. 编译后端 ──────────────────────────────────────────────
ascend_info "[game] 编译后端 $PLATFORM ..."
ascend_build_backend "$PLATFORM"

# ── 3. 组装 + 冒烟 ───────────────────────────────────────────
ascend_info "[game] 组装 + 冒烟 $PLATFORM ..."
bash "$ASCEND_ROOT/build/package/$CHANNEL/assemble.sh" "$PLATFORM"
ascend_smoke "$CHANNEL" "$PLATFORM"

# ── 4. 归档（舞台目录用完即删）───────────────────────────────
ascend_info "[game] 归档 $PLATFORM ..."
case "$PLATFORM" in
  linux)
    for script in make_tar_gz make_deb make_rpm make_appimage; do
      bash "$ASCEND_ROOT/build/package/$CHANNEL/linux/$script.sh"
    done
    ;;
  windows)
    for script in make_zip make_installer; do
      bash "$ASCEND_ROOT/build/package/$CHANNEL/windows/$script.sh"
    done
    ;;
  *)
    ascend_die "未知平台: $PLATFORM"
    ;;
esac
rm -rf "$STAGE"

echo "已生成（build/dist/$CHANNEL/）:"
ls -la "$(ascend_dist_dir "$CHANNEL")"
