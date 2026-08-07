#!/usr/bin/env bash
# AppImage（单文件免安装，多发行版通用）：舞台目录 → AppImage
#
# 用法: bash build/package/linux/make_appimage.sh
# 前置: 舞台目录已组装；curl + ImageMagick(magick) + FUSE
#       （appimagetool 自动下载；条件不满足时跳过）
# 产物: build/dist/release/ascend-linux.AppImage
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
STAGE="$ROOT/build/work/staging/Ascend-linux"
RELEASE_DIR="$ROOT/build/dist/release"
TOOL="$ROOT/build/work/appimagetool"
APPDIR="$ROOT/build/work/AppDir"

MAGICK="$(command -v magick || command -v convert || true)"
if [ -z "$MAGICK" ]; then
  echo "跳过 AppImage：缺少 ImageMagick"
  exit 0
fi
[ -d "$STAGE" ] || { echo "缺少舞台目录: $STAGE" >&2; exit 1; }

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
cp -r "$STAGE/ascend.x86_64" "$STAGE/ascend.pck" "$STAGE/server" "$APPDIR/"
cp "$ROOT/build/package/linux/ascend.desktop" "$APPDIR/ascend.desktop"
cp "$ROOT/build/package/linux/ascend.desktop" "$APPDIR/usr/share/applications/"
cp "$ROOT/build/assets/ascend.svg" "$APPDIR/usr/share/icons/hicolor/256x256/apps/"
# AppImage 需要 PNG 图标（与 desktop 文件 Icon= 同名）
"$MAGICK" "$ROOT/build/assets/ascend.svg" -resize 256x256 "$APPDIR/ascend.png"

cat > "$APPDIR/AppRun" <<'EOF'
#!/usr/bin/env bash
HERE="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
exec "$HERE/ascend.x86_64" "$@"
EOF
chmod +x "$APPDIR/AppRun"

# ── 打包 ─────────────────────────────────────────────────
# appimagetool 默认输出到当前目录 → 在 work/ 内运行，产物落位可预期
rm -f "$ROOT"/build/work/*.AppImage
(
  cd "$ROOT/build/work"
  "$TOOL" --appimage-extract-and-run "$APPDIR" >/dev/null 2>&1 || "$TOOL" "$APPDIR" >/dev/null
)
mv "$ROOT"/build/work/*.AppImage "$RELEASE_DIR/ascend-linux.AppImage"
echo "已生成: $RELEASE_DIR/ascend-linux.AppImage"
