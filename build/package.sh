#!/usr/bin/env bash
# Ascend 打包入口 — 两条通道共享同一套流程底座（build/lib/）。
#
# 用法: bash build/package.sh <miskhak|kheker> [linux|windows|all]
#
#   通道        对外产品    内容                        产物目录
#   miskhak     game        前端 + 后端                  build/dist/miskhak/
#   kheker      research    仅后端（server/data/lang）   build/dist/kheker/
#
# 平台缺省 = 本机；all = linux + windows（Windows 在本机走 wine 交叉编译）。
#
# 版本号见 build/version/（core 为共享核心版本；两产品版本以 core 为前缀）。
# 发布: research 走 GitHub Releases（build/ci/publish_release.sh kheker）；
#       game 含闭源前端资产，走私有流程。
set -euo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

CHANNEL="${1:-}"
# 平台缺省 = 本机（要双平台显式传 all）
PLATFORM="${2:-$(ascend_host_platform)}"

ascend_require_channel "$CHANNEL"
ascend_require_platform "$PLATFORM"

for platform in $(ascend_platforms "$PLATFORM"); do
  ascend_info "[$(ascend_product_of "$CHANNEL")] 打包 $platform（版本 $(ascend_version "$CHANNEL")）"
  bash "$ASCEND_ROOT/build/package/$CHANNEL/build.sh" "$platform"
done

ascend_info "全部完成。产物:"
ls -la "$(ascend_dist_dir "$CHANNEL")"
