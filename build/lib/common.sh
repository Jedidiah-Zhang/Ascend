#!/usr/bin/env bash
# 打包共享底座：仓库根、版本、通道↔产品映射、平台解析、后端编译。
#
# 命名约定（两类包）：
#   通道（内部，与仓库分区同名）：miskhak（游戏包）、kheker（研究包）
#   产品（对外，出现在产物名/解压顶层/tag/Release 标题）：game / research
#
# 版本号（见 build/version/）：
#   core.txt      共享核心版本（olam + miskhak 后端 + data/lang + 协议）
#   miskhak.txt   游戏包版本 = 核心版本 + 游戏序号（0.0.3-alpha.1）
#   kheker.txt    研究包版本 = 核心版本 + 研究序号
[[ -n "${ASCEND_COMMON_LOADED:-}" ]] && return 0
ASCEND_COMMON_LOADED=1

ASCEND_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ASCEND_VERSION_DIR="$ASCEND_ROOT/build/version"

ascend_die() { echo "错误: $*" >&2; exit 1; }
ascend_info() { echo "==> $*"; }

# 校验通道参数（在主 shell 中调用，保证失败即退出）
ascend_require_channel() {
  case "${1:-}" in
    miskhak|kheker) ;;
    *) ascend_die "未知打包通道: ${1:-}（可选 miskhak|kheker）" ;;
  esac
}

# 校验平台参数（all = 两类平台；在主 shell 中调用——映射函数常在命令替换里用，
# 其中的 ascend_die 只退出子 shell，必须在入口先校验）
ascend_require_platform() {
  case "${1:-}" in
    all|linux|windows) ;;
    *) ascend_die "未知平台: ${1:-}（可选 linux|windows|all）" ;;
  esac
}

# 校验单平台参数（组装/归档脚本用）
ascend_require_one_platform() {
  case "${1:-}" in
    linux|windows) ;;
    *) ascend_die "未知平台: ${1:-}（可选 linux|windows）" ;;
  esac
}

# 通道 → 对外产品名（game / research）
ascend_product_of() {
  case "${1:-}" in
    miskhak) echo game ;;
    kheker) echo research ;;
    *) echo "${1:-}" ;;
  esac
}

# 发布 tag 前缀 → 通道（game-v* / research-v*）
ascend_channel_of_tag() {
  case "${1:-}" in
    game-*) echo miskhak ;;
    research-*) echo kheker ;;
    *) return 1 ;;
  esac
}

# 通道版本号（core|miskhak|kheker）
ascend_version() {
  local file="$ASCEND_VERSION_DIR/${1:?需要通道名}.txt"
  [[ -f "$file" ]] || ascend_die "版本文件缺失: $file"
  tr -d '[:space:]' < "$file"
}

# 版本号 → 数字段（Windows 版本资源 / deb-rpm 版本：0.0.3-alpha.1 → 0.0.3）
ascend_numeric_version() { echo "${1%%-*}"; }

# 通道 + 平台 → 舞台目录名 / 路径（build/work/staging/<通道>-<平台>）
ascend_stage_name() { echo "${1:?通道}-${2:?平台}"; }
ascend_stage_dir() { echo "$ASCEND_ROOT/build/work/staging/$(ascend_stage_name "$1" "$2")"; }

# 通道 → 产物目录（build/dist/<通道>）
ascend_dist_dir() { echo "$ASCEND_ROOT/build/dist/${1:?通道}"; }

# 通道 → 归档内顶层目录（对外产品名）
ascend_stage_top() {
  case "$(ascend_product_of "${1:-}")" in
    game) echo "Ascend-Game" ;;
    research) echo "Ascend-Research" ;;
    *) ascend_die "未知产品: $1" ;;
  esac
}

# 通道 + 平台 → 归档文件基名（ascend-game-linux / ascend-research-windows）
ascend_artifact_base() {
  echo "ascend-$(ascend_product_of "${1:?通道}")-${2:?平台}"
}

# 本机平台
ascend_host_platform() {
  case "$(uname -s)" in
    Linux*) echo linux ;;
    MINGW*|MSYS*|CYGWIN*) echo windows ;;
    *) ascend_die "无法识别平台: $(uname -s)（请显式指定 linux|windows）" ;;
  esac
}

# 平台参数 → 平台列表（all = linux windows）
ascend_platforms() {
  case "${1:-all}" in
    all) echo "linux windows" ;;
    linux|windows) echo "$1" ;;
    *) ascend_die "未知平台: $1（可选 linux|windows|all）" ;;
  esac
}

# 平台 → Godot 导出预设名
ascend_godot_preset() {
  case "${1:?平台}" in
    linux) echo "Linux X11" ;;
    windows) echo "Windows Desktop" ;;
    *) ascend_die "未知平台: $1" ;;
  esac
}

# 平台 → 游戏可执行文件名
ascend_game_exe() {
  case "${1:?平台}" in
    linux) echo "ascend.x86_64" ;;
    windows) echo "ascend.exe" ;;
    *) ascend_die "未知平台: $1" ;;
  esac
}

# 平台 → 后端编译产物目录
ascend_backend_output() {
  case "${1:?平台}" in
    linux) echo "$ASCEND_ROOT/build/work/nuitka/server" ;;
    windows) echo "$ASCEND_ROOT/build/work/nuitka-win/server" ;;
    *) ascend_die "未知平台: $1" ;;
  esac
}

# 编译后端（Windows 可用 BACKEND_WIN_SCRIPT 指向原生脚本；默认 wine 交叉编译）
ascend_build_backend() {
  local platform="${1:?平台}"
  case "$platform" in
    linux)
      bash "$ASCEND_ROOT/build/nuitka/build_backend.sh"
      ;;
    windows)
      local script="${BACKEND_WIN_SCRIPT:-$ASCEND_ROOT/build/nuitka/build_backend_windows.sh}"
      bash "$script"
      ;;
  esac
}
