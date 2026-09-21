#!/usr/bin/env bash
# 舞台组装与冒烟 — 两类包共用同一份后端舞台，游戏包额外叠加前端。
#
# 依赖 common.sh（先 source 本文件前先 source 它）。
[[ -n "${ASCEND_STAGE_LOADED:-}" ]] && return 0
ASCEND_STAGE_LOADED=1

# 组装后端舞台：server/ + data/ + lang/（三类内容对两类包完全相同）。
#
# 用法: ascend_stage_backend <通道> <平台>
ascend_stage_backend() {
  local channel="${1:?通道}" platform="${2:?平台}"
  local stage; stage="$(ascend_stage_dir "$channel" "$platform")"
  local server_src; server_src="$(ascend_backend_output "$platform")"

  [[ -d "$server_src" ]] || ascend_die "后端编译产物缺失: $server_src（先编译后端）"
  rm -rf "$stage"
  mkdir -p "$stage"
  cp -r "$server_src" "$stage/server"
  # 后端 i18n 以 miskhak 包目录为锚向外查找 lang → 配送到舞台根
  cp -r "$ASCEND_ROOT/miskhak/lang" "$stage/lang"
  # 世界内容数据（import 期强依赖；olam 包目录向外锚定 → 舞台根）
  cp -r "$ASCEND_ROOT/data" "$stage/data"
}

# 叠加前端产物：ascend 可执行 + ascend.pck（仅游戏包）。
#
# 用法: ascend_stage_frontend <通道> <平台> <版本>
ascend_stage_frontend() {
  local channel="${1:?通道}" platform="${2:?平台}" version="${3:?版本}"
  local stage; stage="$(ascend_stage_dir "$channel" "$platform")"
  local exports="$ASCEND_ROOT/build/work/exports/$platform"
  local exe; exe="$(ascend_game_exe "$platform")"

  [[ -f "$exports/$exe" ]] || ascend_die "前端导出缺失: $exports/$exe"
  [[ -f "$exports/ascend.pck" ]] || ascend_die "前端导出缺失: $exports/ascend.pck"
  cp "$exports/$exe" "$stage/"
  cp "$exports/ascend.pck" "$stage/"
}

# 协议级冒烟（后端起得来 + 握手 + 存档层就绪；游戏包另验前端文件齐备）。
#
# 用法: ascend_smoke <通道> <平台>
ascend_smoke() {
  local channel="${1:?通道}" platform="${2:?平台}"
  bash "$ASCEND_ROOT/build/ci/smoke.sh" \
    "$(ascend_stage_dir "$channel" "$platform")" "$platform" "$(ascend_product_of "$channel")"
}
