#!/usr/bin/env bash
# 游戏包（miskhak → game）舞台组装：共享后端舞台 + 前端叠加 + 说明/许可证。
#
# 用法: bash build/package/miskhak/assemble.sh <linux|windows>
# 前置: 后端已编译（build/nuitka/）、前端已导出（build/work/exports/<平台>/）
# 产物: build/work/staging/miskhak-<平台>/（游戏可执行 + ascend.pck + server/ + data/ + lang/）
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/lib/common.sh"
source "$ASCEND_ROOT/build/lib/stage.sh"

PLATFORM="${1:?用法: assemble.sh <linux|windows>}"
ascend_require_one_platform "$PLATFORM"
CHANNEL=miskhak
VERSION="$(ascend_version "$CHANNEL")"
STAGE="$(ascend_stage_dir "$CHANNEL" "$PLATFORM")"
EXE="$(ascend_game_exe "$PLATFORM")"

ascend_stage_backend "$CHANNEL" "$PLATFORM"
ascend_stage_frontend "$CHANNEL" "$PLATFORM" "$VERSION"

cat > "$STAGE/README.txt" <<EOF
Ascend 游戏包 $VERSION ($PLATFORM)

运行: 执行 ./$EXE（Linux 需 chmod +x）。
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
