#!/usr/bin/env bash
# Ascend 打包产物冒烟 — 启动产物 → 协议级握手 → 清理
#
# 用法: bash build/ci/smoke.sh <stage目录> <platform: linux|windows>
#
# 协议级验证（build/ci/smoke_server.py）：TCP 就绪 + hello 握手 +
# save_list 响应，替代旧的"端口有人听"检查（残留进程占用端口会假阳性）。
# 端口随机（OS 分配空闲端口），避免与开发实例/残留进程冲突。
#
# 平台分派:
#   linux 产物         → 直接执行 server/server
#   windows 产物+Linux → wine 执行（路径转 Z:\ 风格）
#   windows 产物+MSYS  → 原生执行（路径 cygpath 转 Windows 风格）
set -euo pipefail

STAGE="${1:?用法: bash build/ci/smoke.sh <stage目录> <platform>}"
PLATFORM="${2:?用法: bash build/ci/smoke.sh <stage目录> <platform>}"

case "$PLATFORM" in
  linux|windows) ;;
  *) echo "未知平台: $PLATFORM（仅支持 linux|windows）" >&2; exit 1 ;;
esac

# ── 随机端口（OS 分配空闲端口）────────────────────────────
PORT="$(python3 - <<'EOF'
import socket
s = socket.socket()
s.bind(("127.0.0.1", 0))
print(s.getsockname()[1])
s.close()
EOF
)"

TMP="$(mktemp -d)"
LOGF="$TMP/server.log"
PID=""
ok=""

# 内容数据配送检查：后端 import 期强依赖 data/（缺则整个服务起不来）。
# 主位置 STAGE/data（舞台根，见 data.py 双布局回退），回退 server/data。
if [ ! -d "$STAGE/data" ] && [ ! -d "$STAGE/server/data" ]; then
  echo "    [冒烟] 失败：缺少内容数据目录（STAGE/data 或 STAGE/server/data）" >&2
  exit 1
fi
# 语言文件配送检查（i18n 解析目标为舞台根 lang，回退 server/lang）
if [ ! -d "$STAGE/lang" ] && [ ! -d "$STAGE/server/lang" ]; then
  echo "    [冒烟] 失败：缺少语言目录（STAGE/lang 或 STAGE/server/lang）" >&2
  exit 1
fi

cleanup() {
  [ -n "$PID" ] && kill "$PID" 2>/dev/null || true
  # 兜底：精确匹配本舞台目录的产物进程（路径含 stage 目录，不误伤其它实例）
  pkill -f "$STAGE/server/server" 2>/dev/null || true
  [ -n "$PID" ] && wait "$PID" 2>/dev/null || true
}
trap cleanup EXIT

echo "    [冒烟] 启动打包后端（端口 $PORT）..."
case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*)
    STAGE_WIN="$(cygpath -w "$STAGE")"
    TMP_WIN="$(cygpath -w "$TMP")"
    ASCEND_SERVER_PORT="$PORT" ASCEND_SAVE_ROOT="$TMP_WIN" \
      "$STAGE/server/server.exe" --project-root "$STAGE_WIN" >"$LOGF" 2>&1 &
    ;;
  *)
    if [ "$PLATFORM" = "windows" ]; then
      ASCEND_SERVER_PORT="$PORT" ASCEND_SAVE_ROOT="$TMP" \
        wine "$STAGE/server/server.exe" --project-root "Z:$(echo "$STAGE" | sed 's|/|\\|g')" >"$LOGF" 2>&1 &
    else
      ASCEND_SERVER_PORT="$PORT" ASCEND_SAVE_ROOT="$TMP" \
        "$STAGE/server/server" --project-root "$STAGE" >"$LOGF" 2>&1 &
    fi
    ;;
esac
PID=$!

if python3 "$(dirname "${BASH_SOURCE[0]}")/smoke_server.py" \
    --port "$PORT" --token-file "$STAGE/.ascend_token"; then
  ok=1
fi

if [ -z "$ok" ]; then
  echo "    [冒烟] 失败：协议级握手未通过，产物日志（末尾）:" >&2
  tail -20 "$LOGF" >&2 || true
  exit 1
fi

cleanup
rm -rf "$TMP"
echo "    [冒烟] 通过"