#!/usr/bin/env bash
# 游戏包 RPM（Fedora/OpenSUSE 等 rpm 系）：舞台目录 → build/dist/miskhak/ascend-game-linux.rpm
#
# 用法: bash build/package/miskhak/linux/make_rpm.sh
# 前置: 舞台目录已组装；rpmbuild（本地缺省时跳过）
# 结构与 deb 一致：/opt/ascend + /usr/bin/ascend 软链 + 桌面注册
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)/lib/common.sh"

CHANNEL=miskhak
PLATFORM=linux
VERSION="$(ascend_version "$CHANNEL")"
VER="$(ascend_numeric_version "$VERSION")"
if [ "$VER" = "$VERSION" ]; then
  RELEASE="1"
else
  RELEASE="0.${VERSION#*-}"   # 0.0.3-alpha.1 → Version 0.0.3, Release 0.alpha.1
fi
STAGE="$(ascend_stage_dir "$CHANNEL" "$PLATFORM")"
RELEASE_DIR="$(ascend_dist_dir "$CHANNEL")"
ARCHIVE="$RELEASE_DIR/$(ascend_artifact_base "$CHANNEL" "$PLATFORM").rpm"
RPM_ROOT="$ASCEND_ROOT/build/work/rpm"

command -v rpmbuild >/dev/null 2>&1 || { echo "跳过 rpm：缺少 rpmbuild"; exit 0; }
[ -d "$STAGE" ] || ascend_die "舞台目录不存在: $STAGE（先运行 build/package/miskhak/assemble.sh linux）"

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
cp -r "$STAGE"/ascend.x86_64 "$STAGE"/ascend.pck "$STAGE"/server "$STAGE"/data "$STAGE"/lang %{buildroot}/opt/ascend/
ln -s /opt/ascend/ascend.x86_64 %{buildroot}/usr/bin/ascend
mkdir -p %{buildroot}/usr/share/applications %{buildroot}/usr/share/icons/hicolor/256x256/apps
cp "$ASCEND_ROOT"/build/package/miskhak/linux/ascend.desktop %{buildroot}/usr/share/applications/
cp "$ASCEND_ROOT"/build/assets/ascend.svg %{buildroot}/usr/share/icons/hicolor/256x256/apps/

%files
/opt/ascend/
/usr/bin/ascend
/usr/share/applications/ascend.desktop
/usr/share/icons/hicolor/256x256/apps/ascend.svg
EOF

rpmbuild --define "_topdir $RPM_ROOT" -bb "$RPM_ROOT/SPECS/ascend.spec" >/dev/null
cp "$RPM_ROOT/RPMS/x86_64/ascend-$VER-$RELEASE.x86_64.rpm" "$ARCHIVE"
echo "已生成: $ARCHIVE"
