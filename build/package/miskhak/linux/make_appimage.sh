#!/usr/bin/env bash
# 游戏包 AppImage（单文件免安装）：舞台目录 → build/dist/miskhak/ascend-game-linux.AppImage
#
# 用法: bash build/package/miskhak/linux/make_appimage.sh
# 前置: 舞台目录已组装；curl + ImageMagick(magick) + FUSE
#       （appimagetool 自动下载；条件不满足时跳过）
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)/lib/common.sh"

CHANNEL=miskhak
PLATFORM=linux
STAGE="$(ascend_stage_dir "$CHANNEL" "$PLATFORM")"
RELEASE_DIR="$(ascend_dist_dir "$CHANNEL")"
ARCHIVE="$RELEASE_DIR/$(ascend_artifact_base "$CHANNEL" "$PLATFORM").AppImage"
TOOL="$ASCEND_ROOT/build/work/appimagetool"
APPDIR="$ASCEND_ROOT/build/work/AppDir"

MAGICK="$(command -v magick || command -v convert || true)"
if [ -z "$MAGICK" ]; then
  echo "跳过 AppImage：缺少 ImageMagick"
  exit 0
fi
[ -d "$STAGE" ] || ascend_die "舞台目录不存在: $STAGE（先运行 build/package/miskhak/assemble.sh linux）"

mkdir -p "$RELEASE_DIR"
if [ ! -x "$TOOL" ]; then
  echo "下载 appimagetool ..."
  curl -sL -o "$TOOL" \
    "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"
  chmod +x "$TOOL"
fi

# ── 组装 AppDir ──────────────────────────────────────────
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/share/applications" "$APPDIR/usr/share/icons/hicolor/256x256/apps"
cp -r "$STAGE/ascend.x86_64" "$STAGE/ascend.pck" "$STAGE/server" \
  "$STAGE/data" "$STAGE/lang" "$APPDIR/"
cp "$ASCEND_ROOT/build/package/miskhak/linux/ascend.desktop" "$APPDIR/ascend.desktop"
cp "$ASCEND_ROOT/build/package/miskhak/linux/ascend.desktop" "$APPDIR/usr/share/applications/"
cp "$ASCEND_ROOT/build/assets/ascend.svg" "$APPDIR/usr/share/icons/hicolor/256x256/apps/"
# AppImage 需要 PNG 图标（与 desktop 文件 Icon= 同名）
"$MAGICK" "$ASCEND_ROOT/build/assets/ascend.svg" -resize 256x256 "$APPDIR/ascend.png"

cat > "$APPDIR/AppRun" <<'EOF'
#!/usr/bin/env bash
HERE="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
exec "$HERE/ascend.x86_64" "$@"
EOF
chmod +x "$APPDIR/AppRun"

# ── 打包 ─────────────────────────────────────────────────
# appimagetool 默认输出到当前目录 → 在 build/work/ 内运行后移动到目标路径
rm -f "$ASCEND_ROOT"/build/work/*.AppImage
(
  cd "$ASCEND_ROOT/build/work"
  "$TOOL" --appimage-extract-and-run "$APPDIR" >/dev/null 2>&1 || "$TOOL" "$APPDIR" >/dev/null
)
mv "$ASCEND_ROOT"/build/work/*.AppImage "$ARCHIVE"
echo "已生成: $ARCHIVE"
