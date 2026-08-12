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
# 目标目录必须存在（Godot 导出不自动建目录；CI 全新检出无 build/work）
# 语言文件（与后端共用 repo/lang/）先拷入前端项目使其进入导出资源（PCK）
echo "==> [0/3] 同步语言文件到前端项目 ..."
rm -rf "$ROOT/frontend/lang"
mkdir -p "$ROOT/frontend/lang"
cp "$ROOT/lang/"*.json "$ROOT/frontend/lang/"
if $NEED_LINUX; then
  echo "==> [1/3] 导出前端 Linux ..."
  mkdir -p "$ROOT/build/work/exports/linux"
  godot --headless --path "$ROOT/frontend" --export-release "Linux X11"
fi
if $NEED_WIN; then
  echo "==> [1/3] 导出前端 Windows ..."
  mkdir -p "$ROOT/build/work/exports/windows"
  godot --headless --path "$ROOT/frontend" --export-release "Windows Desktop"
fi

# ── 后端编译 ──────────────────────────────────────────────
# Windows 后端脚本可覆盖：本地默认 wine 交叉编译；
# CI（windows runner）设置 BACKEND_WIN_SCRIPT 指向原生编译脚本
BACKEND_WIN_SCRIPT="${BACKEND_WIN_SCRIPT:-$ROOT/build/nuitka/build_backend_windows.sh}"

if $NEED_LINUX; then
  echo "==> [2/3] 编译后端 Linux（Nuitka）..."
  bash "$ROOT/build/nuitka/build_backend.sh"
fi
if $NEED_WIN; then
  echo "==> [2/3] 编译后端 Windows（$BACKEND_WIN_SCRIPT）..."
  bash "$BACKEND_WIN_SCRIPT"
fi

# ── 组装 → 冒烟 → 归档 ────────────────────────────────────
_smoke() {
  local stage="$1" platform="$2"
  local port=19081
  local tmp; tmp="$(mktemp -d)"
  local logf="$tmp/server.log"
  local pid ok=""
  echo "    [冒烟] 启动打包后端，等待端口 $port ..."
  case "$(uname -s)" in
    MINGW*|MSYS*|CYGWIN*)
      # 原生 Windows（CI runner 的 Git Bash）：路径须转 Windows 风格
      #（msys 的 /c/... 会被 Windows Python 解析为 C:\c\... 而崩溃）
      local stage_win tmp_win
      stage_win="$(cygpath -w "$stage")"
      tmp_win="$(cygpath -w "$tmp")"
      ASCEND_SERVER_PORT=$port ASCEND_SAVE_ROOT="$tmp_win" \
        "$stage/server/server.exe" --project-root "$stage_win" >"$logf" 2>&1 &
      ;;
    *)
      if [ "$platform" = "windows" ]; then
        # Linux 本机构建 Windows 包：wine 冒烟
        ASCEND_SERVER_PORT=$port ASCEND_SAVE_ROOT="$tmp" \
          wine "$stage/server/server.exe" --project-root "Z:$(echo "$stage" | sed 's|/|\\|g')" >"$logf" 2>&1 &
      else
        ASCEND_SERVER_PORT=$port ASCEND_SAVE_ROOT="$tmp" \
          "$stage/server/server" --project-root "$stage" >"$logf" 2>&1 &
      fi
      ;;
  esac
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
  if [ -z "$ok" ]; then
    echo "    [冒烟] 失败：后端未在端口 $port 就绪，日志:" >&2
    cat "$logf" 2>/dev/null | tail -20 >&2 || true
    rm -rf "$tmp"
    exit 1
  fi
  rm -rf "$tmp"
  echo "    [冒烟] 通过"
}

if $NEED_LINUX; then
  echo "==> [3/3] 组装 + 冒烟 + 归档 Linux ..."
  bash "$ROOT/build/package/assemble_release.sh" linux
  _smoke "$ROOT/build/work/staging/Ascend-linux" linux
  bash "$ROOT/build/package/linux/make_tar_gz.sh"
  bash "$ROOT/build/package/linux/make_deb.sh"
  bash "$ROOT/build/package/linux/make_rpm.sh"
  bash "$ROOT/build/package/linux/make_appimage.sh"
  rm -rf "$ROOT/build/work/staging/Ascend-linux"
fi
if $NEED_WIN; then
  echo "==> [3/3] 组装 + 冒烟 + 归档 Windows ..."
  bash "$ROOT/build/package/assemble_release.sh" windows
  _smoke "$ROOT/build/work/staging/Ascend-windows" windows
  bash "$ROOT/build/package/windows/make_zip.sh"
  bash "$ROOT/build/package/windows/make_installer.sh"
  rm -rf "$ROOT/build/work/staging/Ascend-windows"
fi

echo "全部完成。产物:"
ls -la "$ROOT/build/dist/release/"
echo "发布: git tag v$(cat "$ROOT/build/nuitka/version.txt") && bash build/ci/publish_release.sh"
