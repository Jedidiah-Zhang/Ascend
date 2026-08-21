#!/usr/bin/env bash
# Ascend 舞台目录组装脚本
#
# 用法: bash build/package/assemble_release.sh [linux|windows]
#   默认取当前平台。消费 build/work/ 下的导出产物与后端编译产物，
#   组装为固定名舞台目录（无版本号，用完即删）：
#
#   build/work/staging/Ascend-<平台>/
#   ├── ascend[.exe|x86_64]        # 游戏可执行（根目录）
#   ├── ascend.pck                 # 游戏资源包
#   ├── server/                    # 后端（standalone 目录，含依赖库）
#   │   └── server[.exe]
#   ├── .ascend_token              # （运行时由后端生成）
#   ├── README.txt                 # 运行说明
#   └── LICENSE.txt                # 发行许可证（代码 CC BY-NC-SA / 资产专有）
#
# 前置: 前端已导出（build/work/exports/<平台>/）、后端已编译
#   （build/work/nuitka/ 或 nuitka-win/）。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VERSION="$(cat "$ROOT/build/nuitka/version.txt")"

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
    GAME_EXE="ascend.x86_64"
    SUBDIR="linux"
    BACKEND_DIR="$ROOT/build/work/nuitka"
    ;;
  windows)
    GAME_EXE="ascend.exe"
    SUBDIR="windows"
    BACKEND_DIR="$ROOT/build/work/nuitka-win"
    ;;
  *)
    echo "未知平台: $PLATFORM（仅支持 linux|windows）" >&2
    exit 1
    ;;
esac

EXPORTS_DIR="$ROOT/build/work/exports/$SUBDIR"
STAGE="$ROOT/build/work/staging/Ascend-$PLATFORM"
SERVER_SRC="$BACKEND_DIR/server"

# 前置检查
MISSING=""
for f in "$EXPORTS_DIR/$GAME_EXE" "$EXPORTS_DIR/ascend.pck"; do
  [ -f "$f" ] || MISSING="$MISSING $f"
done
[ -d "$SERVER_SRC" ] || MISSING="$MISSING $SERVER_SRC/"
if [ -n "$MISSING" ]; then
  echo "缺少构建产物，请先导出前端并编译后端:"
  echo "$MISSING" | sed 's/^/  - /'
  echo "  前端: godot --headless --path frontend --export-release \"$PLATFORM\""
  echo "  后端: bash build/nuitka/build_backend.sh（Linux）或 build_backend_windows.sh（Windows）"
  [ "$PLATFORM" = "windows" ] && \
    echo "  （Windows 版后端须执行 build/nuitka/build_backend_windows.sh，在 Linux 上用 wine 交叉编译）"
  exit 1
fi

rm -rf "$STAGE"
mkdir -p "$STAGE"

cp "$EXPORTS_DIR/$GAME_EXE" "$STAGE/"
cp "$EXPORTS_DIR/ascend.pck" "$STAGE/"
cp -r "$SERVER_SRC" "$STAGE/"
# 后端 i18n 按模块相对路径解析：Nuitka standalone 下 __file__ 含包前缀，
# ascend/i18n.py 上三级 = 舞台根 → lang 配送到 STAGE/lang
cp -r "$ROOT/lang" "$STAGE/lang"
# 后端内容数据（第 1 层数据驱动，import 期强依赖；ascend/data.py 上三级
# = 舞台根 → data 配送到 STAGE/data；data.py 内置 server/data 回退）
cp -r "$ROOT/data" "$STAGE/data"

cat > "$STAGE/README.txt" <<EOF
Ascend $VERSION ($PLATFORM)

运行: 执行 ./$GAME_EXE（Linux 需 chmod +x）。
游戏会自动拉起同目录 server/ 中的后端进程。
首次运行会生成 .ascend_token 与存档目录（默认 ~/.ascend/saves）。
EOF

cat > "$STAGE/LICENSE.txt" <<EOF
Ascend $VERSION — 许可证 / License

Copyright (c) 2026 Jedidiah-Zhang

代码（server/ 与游戏逻辑）依据 CC BY-NC-SA 4.0（署名-非商业性使用-相同方式共享）许可：
https://creativecommons.org/licenses/by-nc-sa/4.0/

游戏资源（美术、音频等）保留所有权利。
本游戏仅授予运行许可；禁止解包、提取、复制、修改或再分发游戏资源。
商业使用本游戏或其中任何部分，请联系作者获取授权。

Code (server/ and game logic) is licensed under CC BY-NC-SA 4.0:
https://creativecommons.org/licenses/by-nc-sa/4.0/

Game assets (art, audio, etc.) all rights reserved. This game grants a license to
run it only; unpacking, extracting, copying, modifying, or redistributing the
assets is prohibited. Commercial use of the game or any part of it requires
separate authorization from the author.
EOF

echo "舞台目录已组装: $STAGE"
du -sh "$STAGE"
