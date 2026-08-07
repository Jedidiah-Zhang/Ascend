#!/usr/bin/env bash
# Ascend 一键打包：导出前端 → 编译后端 → 组装舞台目录 → 冒烟测试 → 打归档
#
# 用法: bash build/build_release.sh [linux|windows|all]
#   默认 all（Linux 后端本机编译；Windows 后端经 wine 交叉编译）
#
# 产物: build/dist/release/ascend-linux.tar.gz、ascend-windows.zip
# 发布: git tag v<版本> && bash build/ci/publish_release.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${1:-all}"

case "$TARGET" in
  all|linux|windows) ;;
  *) echo "用法: $0 [linux|windows|all]" >&2; exit 1 ;;
esac

NEED_LINUX=false; NEED_WIN=false
if [ "$TARGET" = "all" ] || [ "$TARGET" = "linux" ]; then NEED_LINUX=true; fi
if [ "$TARGET" = "all" ] || [ "$TARGET" = "windows" ]; then NEED_WIN=true; fi

# ── 前端导出 ──────────────────────────────────────────────
if $NEED_LINUX; then
  echo "==> [1/3] 导出前端 Linux ..."
  godot --headless --path "$ROOT/frontend" --export-release "Linux X11"
fi
if $NEED_WIN; then
  echo "==> [1/3] 导出前端 Windows ..."
  godot --headless --path "$ROOT/frontend" --export-release "Windows Desktop"
fi

# ── 后端编译 ──────────────────────────────────────────────
if $NEED_LINUX; then
  echo "==> [2/3] 编译后端 Linux（Nuitka）..."
  bash "$ROOT/build/nuitka/build_backend.sh" onefile
fi
if $NEED_WIN; then
  echo "==> [2/3] 编译后端 Windows（wine + Nuitka，较慢）..."
  bash "$ROOT/build/nuitka/build_backend_windows.sh"
fi

# ── 组装 → 冒烟 → 归档 ────────────────────────────────────
_smoke() {
  local stage="$1" platform="$2"
  local port=19081
  local tmp; tmp="$(mktemp -d)"
  local pid ok=""
  echo "    [冒烟] 启动打包后端，等待端口 $port ..."
  if [ "$platform" = "windows" ]; then
    ASCEND_SERVER_PORT=$port ASCEND_SAVE_ROOT="$tmp" \
      wine "$stage/server/server.exe" --project-root "Z:$(echo "$stage" | sed 's|/|\\|g')" >/dev/null 2>&1 &
  else
    ASCEND_SERVER_PORT=$port ASCEND_SAVE_ROOT="$tmp" \
      "$stage/server/server" --project-root "$stage" >/dev/null 2>&1 &
  fi
  pid=$!
  for _ in $(seq 1 45); do
    if (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null; then
      exec 3>&- 3<&-; ok=1; break
    fi
    sleep 1
  done
  kill "$pid" 2>/dev/null || true
  pkill -f "$stage/server/server" 2>/dev/null || true
  wait "$pid" 2>/dev/null || true
  rm -rf "$tmp"
  [ -n "$ok" ] || { echo "    [冒烟] 失败：后端未在端口 $port 就绪" >&2; exit 1; }
  echo "    [冒烟] 通过"
}

if $NEED_LINUX; then
  echo "==> [3/3] 组装 + 冒烟 + 归档 Linux ..."
  bash "$ROOT/build/package/assemble_release.sh" linux
  _smoke "$ROOT/build/work/staging/Ascend-linux" linux
  bash "$ROOT/build/package/linux/make_tar_gz.sh"
fi
if $NEED_WIN; then
  echo "==> [3/3] 组装 + 冒烟 + 归档 Windows ..."
  bash "$ROOT/build/package/assemble_release.sh" windows
  _smoke "$ROOT/build/work/staging/Ascend-windows" windows
  bash "$ROOT/build/package/windows/make_zip.sh"
fi

echo "全部完成。产物:"
ls -la "$ROOT/build/dist/release/"
echo "发布: git tag v$(cat "$ROOT/build/nuitka/version.txt") && bash build/ci/publish_release.sh"
