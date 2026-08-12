#!/usr/bin/env bash
# Ascend 后端单独打包（CI 研究平台发行）
#
# 用法: bash build/build_backend_release.sh [linux|windows]
#   默认取当前平台。编译后端 → 组装 server-only 舞台目录（含 lang/，
#   后端 i18n 按模块相对路径解析到 server/lang/）→ 冒烟 → 归档。
#
# 产物: build/dist/release/ascend-server-linux.tar.gz（Linux）
#       build/dist/release/ascend-server-windows.zip（Windows）
# 发布: git tag v<版本> && bash build/ci/publish_release.sh
#
# 说明：CI 仅打包后端（前端为闭源商业资产，不走本仓库 CI）；
# 本地全量打包（前端+后端）仍用 build/build_release.sh。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="$(cat "$ROOT/build/nuitka/version.txt")"
# Windows 后端脚本可覆盖：本地默认 wine 交叉编译；
# CI（windows runner）设置 BACKEND_WIN_SCRIPT 指向原生编译脚本
BACKEND_WIN_SCRIPT="${BACKEND_WIN_SCRIPT:-$ROOT/build/nuitka/build_backend_windows.sh}"

PLATFORM="${1:-}"
if [ -z "$PLATFORM" ]; then
  case "$(uname -s)" in
    Linux*) PLATFORM="linux" ;;
    MINGW*|MSYS*|CYGWIN*) PLATFORM="windows" ;;
    *) echo "无法识别平台，请显式指定: $0 [linux|windows]" >&2; exit 1 ;;
  esac
fi

case "$PLATFORM" in
  linux)
    BACKEND_SCRIPT="$ROOT/build/nuitka/build_backend.sh"
    SERVER_SRC="$ROOT/build/work/nuitka/server"
    ARCHIVE="$ROOT/build/dist/release/ascend-server-linux.tar.gz"
    ;;
  windows)
    BACKEND_SCRIPT="$BACKEND_WIN_SCRIPT"
    SERVER_SRC="$ROOT/build/work/nuitka-win/server"
    ARCHIVE="$ROOT/build/dist/release/ascend-server-windows.zip"
    ;;
  *)
    echo "未知平台: $PLATFORM（仅支持 linux|windows）" >&2
    exit 1
    ;;
esac

# ── 编译后端 ──────────────────────────────────────────────
echo "==> [1/3] 编译后端 $PLATFORM ..."
bash "$BACKEND_SCRIPT"

# ── 组装 → 冒烟 → 归档 ────────────────────────────────────
STAGE="$ROOT/build/work/staging/Server-$PLATFORM"

_smoke() {
  local port=19081
  local tmp; tmp="$(mktemp -d)"
  local logf="$tmp/server.log"
  local pid ok=""
  echo "    [冒烟] 启动打包后端，等待端口 $port ..."
  case "$(uname -s)" in
    MINGW*|MSYS*|CYGWIN*)
      # 原生 Windows（CI runner 的 Git Bash）：路径须转 Windows 风格
      local stage_win tmp_win
      stage_win="$(cygpath -w "$STAGE")"
      tmp_win="$(cygpath -w "$tmp")"
      ASCEND_SERVER_PORT=$port ASCEND_SAVE_ROOT="$tmp_win" \
        "$STAGE/server/server.exe" --project-root "$stage_win" >"$logf" 2>&1 &
      ;;
    *)
      if [ "$PLATFORM" = "windows" ]; then
        # Linux 本机构建 Windows 包：wine 冒烟
        ASCEND_SERVER_PORT=$port ASCEND_SAVE_ROOT="$tmp" \
          wine "$STAGE/server/server.exe" --project-root "Z:$(echo "$STAGE" | sed 's|/|\\|g')" >"$logf" 2>&1 &
      else
        ASCEND_SERVER_PORT=$port ASCEND_SAVE_ROOT="$tmp" \
          "$STAGE/server/server" --project-root "$STAGE" >"$logf" 2>&1 &
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
  pkill -f "$STAGE/server/server" 2>/dev/null || true
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

echo "==> [2/3] 组装 + 冒烟 $PLATFORM ..."
rm -rf "$STAGE"
mkdir -p "$STAGE"
cp -r "$SERVER_SRC" "$STAGE/server"
# 后端 i18n 按模块相对路径解析（ascend/i18n.py 上三级 → server/lang）
cp -r "$ROOT/lang" "$STAGE/server/lang"

cat > "$STAGE/README.txt" <<EOF
Ascend 后端服务器 $VERSION ($PLATFORM) — 研究平台

运行: ./server/server[.exe] --project-root <本目录>
（服务模式监听 127.0.0.1:9081；ASCEND_SAVE_ROOT 可重定向存档目录）
EOF

_smoke

echo "==> [3/3] 归档 $PLATFORM ..."
mkdir -p "$(dirname "$ARCHIVE")"
rm -f "$ARCHIVE"
cd "$ROOT/build/work/staging"
if [ "$PLATFORM" = "linux" ]; then
  tar -czf "$ARCHIVE" "Server-linux"
elif command -v zip >/dev/null 2>&1; then
  zip -qr "$ARCHIVE" "Server-windows"
else
  # Windows runner（Git Bash）无 zip 时的兜底（Python zipfile，DEFLATE）
  python3 - "$ARCHIVE" "Server-windows" <<'EOF'
import sys, zipfile
from pathlib import Path
archive, stage_name = Path(sys.argv[1]), Path(sys.argv[2])
base = archive.parent.parent.parent / "work" / "staging"
with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
    for f in sorted((base / stage_name).rglob("*")):
        if f.is_file():
            zf.write(f, f.relative_to(base))
EOF
fi
rm -rf "$STAGE"
echo "已生成: $ARCHIVE"
echo "发布: git tag v$VERSION && bash build/ci/publish_release.sh"
