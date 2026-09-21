#!/usr/bin/env bash
# 游戏包 DEB（Debian/Ubuntu）：舞台目录 → build/dist/miskhak/ascend-game-linux.deb
#
# 用法: bash build/package/miskhak/linux/make_deb.sh
# 前置: 舞台目录已组装；dpkg-deb（本地缺省时跳过）
# 安装到 /opt/ascend + /usr/bin/ascend 软链 + 桌面菜单/图标注册
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)/lib/common.sh"

CHANNEL=miskhak
PLATFORM=linux
VERSION="$(ascend_version "$CHANNEL")"
DEB_VERSION="${VERSION//-/\~}"   # 0.0.3-alpha.1 → 0.0.3~alpha.1（预发布排序语义）
STAGE="$(ascend_stage_dir "$CHANNEL" "$PLATFORM")"
RELEASE_DIR="$(ascend_dist_dir "$CHANNEL")"
ARCHIVE="$RELEASE_DIR/$(ascend_artifact_base "$CHANNEL" "$PLATFORM").deb"
PKG="$ASCEND_ROOT/build/work/deb"

command -v dpkg-deb >/dev/null 2>&1 || { echo "跳过 deb：缺少 dpkg-deb"; exit 0; }
[ -d "$STAGE" ] || ascend_die "舞台目录不存在: $STAGE（先运行 build/package/miskhak/assemble.sh linux）"

mkdir -p "$RELEASE_DIR"
rm -rf "$PKG"
mkdir -p "$PKG/DEBIAN" "$PKG/opt/ascend" "$PKG/usr/bin" \
  "$PKG/usr/share/applications" "$PKG/usr/share/icons/hicolor/256x256/apps"

cp -r "$STAGE/ascend.x86_64" "$STAGE/ascend.pck" "$STAGE/server" \
  "$STAGE/data" "$STAGE/lang" "$PKG/opt/ascend/"
ln -s /opt/ascend/ascend.x86_64 "$PKG/usr/bin/ascend"
cp "$ASCEND_ROOT/build/package/miskhak/linux/ascend.desktop" "$PKG/usr/share/applications/"
cp "$ASCEND_ROOT/build/assets/ascend.svg" "$PKG/usr/share/icons/hicolor/256x256/apps/"

cat > "$PKG/DEBIAN/control" <<EOF
Package: ascend
Version: $DEB_VERSION
Section: games
Priority: optional
Architecture: amd64
Maintainer: Ascend Developers <noreply@example.com>
Description: AI 原生 2D 俯视生存经营模拟游戏
 基因改造驱动群体演化。世界先于智能体，AI 为一等公民。
 （占位描述，正式发布前完善。）
EOF

dpkg-deb --build "$PKG" "$ARCHIVE" >/dev/null
echo "已生成: $ARCHIVE"
