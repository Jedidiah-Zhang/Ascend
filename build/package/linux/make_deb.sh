#!/usr/bin/env bash
# DEB 包（Debian/Ubuntu）：舞台目录 → 安装包
#
# 用法: bash build/package/linux/make_deb.sh
# 前置: 舞台目录已组装；dpkg-deb（ubuntu runner 预装，本地缺省时跳过）
# 产物: build/dist/release/ascend-linux.deb
# 安装到 /opt/ascend + /usr/bin/ascend 软链 + 桌面菜单/图标注册
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
VERSION="$(cat "$ROOT/build/nuitka/version.txt")"
DEB_VERSION="${VERSION//-/\~}"   # 0.0.1-alpha → 0.0.1~alpha（预发布排序语义）
STAGE="$ROOT/build/work/staging/Ascend-linux"
RELEASE_DIR="$ROOT/build/dist/release"
PKG="$ROOT/build/work/deb"

command -v dpkg-deb >/dev/null 2>&1 || { echo "跳过 deb：缺少 dpkg-deb"; exit 0; }
[ -d "$STAGE" ] || { echo "缺少舞台目录: $STAGE" >&2; exit 1; }

mkdir -p "$RELEASE_DIR"
rm -rf "$PKG"
mkdir -p "$PKG/DEBIAN" "$PKG/opt/ascend" "$PKG/usr/bin" \
  "$PKG/usr/share/applications" "$PKG/usr/share/icons/hicolor/256x256/apps"

cp -r "$STAGE/ascend.x86_64" "$STAGE/ascend.pck" "$STAGE/server" "$PKG/opt/ascend/"
ln -s /opt/ascend/ascend.x86_64 "$PKG/usr/bin/ascend"
cp "$ROOT/build/package/linux/ascend.desktop" "$PKG/usr/share/applications/"
cp "$ROOT/build/assets/ascend.svg" "$PKG/usr/share/icons/hicolor/256x256/apps/"

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

dpkg-deb --build "$PKG" "$RELEASE_DIR/ascend-linux.deb" >/dev/null
echo "已生成: $RELEASE_DIR/ascend-linux.deb"
