#!/usr/bin/env bash
# RPM 包（Fedora/OpenSUSE 等 rpm 系发行版）：舞台目录 → 安装包
#
# 用法: bash build/package/linux/make_rpm.sh
# 前置: 舞台目录已组装；rpmbuild（CI: apt install rpm；本地缺省时跳过）
# 产物: build/dist/release/ascend-linux.rpm
# 结构与 deb 一致：/opt/ascend + /usr/bin/ascend 软链 + 桌面注册
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
VERSION="$(cat "$ROOT/build/nuitka/version.txt")"
VER="${VERSION%%-*}"
if [ "$VER" = "$VERSION" ]; then
  RELEASE="1"
else
  RELEASE="0.${VERSION#*-}"   # 0.0.1-alpha → Version 0.0.1, Release 0.alpha
fi
STAGE="$ROOT/build/work/staging/Ascend-linux"
RELEASE_DIR="$ROOT/build/dist/release"
RPM_ROOT="$ROOT/build/work/rpm"

command -v rpmbuild >/dev/null 2>&1 || { echo "跳过 rpm：缺少 rpmbuild"; exit 0; }
[ -d "$STAGE" ] || { echo "缺少舞台目录: $STAGE" >&2; exit 1; }

mkdir -p "$RELEASE_DIR" "$RPM_ROOT"/{BUILD,RPMS,SPECS,SOURCES,SRPMS}

cat > "$RPM_ROOT/SPECS/ascend.spec" <<EOF
Name:           ascend
Version:        $VER
Release:        $RELEASE
Summary:        AI 原生 2D 俯视生存经营模拟游戏
License:        TBD
BuildArch:      x86_64

%description
AI 原生 2D 俯视生存经营模拟游戏 — 基因改造驱动群体演化。
（占位描述，正式发布前完善。）

%install
rm -rf %{buildroot}
mkdir -p %{buildroot}/opt/ascend %{buildroot}/usr/bin
cp -r "$STAGE"/ascend.x86_64 "$STAGE"/ascend.pck "$STAGE"/server %{buildroot}/opt/ascend/
ln -s /opt/ascend/ascend.x86_64 %{buildroot}/usr/bin/ascend
mkdir -p %{buildroot}/usr/share/applications %{buildroot}/usr/share/icons/hicolor/256x256/apps
cp "$ROOT"/build/package/linux/ascend.desktop %{buildroot}/usr/share/applications/
cp "$ROOT"/build/assets/ascend.svg %{buildroot}/usr/share/icons/hicolor/256x256/apps/

%files
/opt/ascend/
/usr/bin/ascend
/usr/share/applications/ascend.desktop
/usr/share/icons/hicolor/256x256/apps/ascend.svg
EOF

rpmbuild --define "_topdir $RPM_ROOT" -bb "$RPM_ROOT/SPECS/ascend.spec" >/dev/null
cp "$RPM_ROOT/RPMS/x86_64/ascend-$VER-$RELEASE.x86_64.rpm" "$RELEASE_DIR/ascend-linux.rpm"
echo "已生成: $RELEASE_DIR/ascend-linux.rpm"
