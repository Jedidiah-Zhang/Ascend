#!/usr/bin/env bash
# CI 工具：安装 Godot <版本>（含导出模板）
#
# 用法: bash build/ci/setup_godot.sh [linux|windows]
#   - 二进制 → ~/godot/godot[.exe]（路径写入 GITHUB_PATH）
#   - 模板   → 平台标准导出模板目录（幂等：已存在则跳过）
#
# 注意: 依赖 curl + python3（解压 zip），CI runner 均满足。
set -euo pipefail

VER="4.7.1"
TARGET="${1:-linux}"
BASE="https://github.com/godotengine/godot/releases/download/$VER-stable"

case "$TARGET" in
  linux)
    BIN_URL="$BASE/Godot_v${VER}-stable_linux.x86_64.zip"
    BIN_NAME="godot"
    BIN_DIR="$HOME/godot"
    TPL_DIR="$HOME/.local/share/godot/export_templates"
    ;;
  windows)
    BIN_URL="$BASE/Godot_v${VER}-stable_win64.exe.zip"
    BIN_NAME="godot.exe"
    BIN_DIR="$HOME/godot"
    TPL_DIR="$HOME/AppData/Roaming/Godot/export_templates"
    ;;
  *)
    echo "未知目标: $TARGET（仅支持 linux|windows）" >&2
    exit 1
    ;;
esac

mkdir -p "$BIN_DIR" "$TPL_DIR"

# ── Godot 二进制 ─────────────────────────────────────────
if [ ! -f "$BIN_DIR/$BIN_NAME" ]; then
  echo "下载 Godot $VER ..."
  curl -sL -o /tmp/godot.zip "$BIN_URL"
  python3 -m zipfile -e /tmp/godot.zip "$BIN_DIR/"
  # 官方 zip 内二进制名：linux = Godot_v<版本>-stable_linux.x86_64，
  # windows = Godot_v<版本>-stable_win64.exe（另有 _console 变体，勿误取）
  case "$TARGET" in
    linux)   mv "$BIN_DIR/Godot_v${VER}-stable_linux.x86_64" "$BIN_DIR/$BIN_NAME" ;;
    windows) mv "$BIN_DIR/Godot_v${VER}-stable_win64.exe" "$BIN_DIR/$BIN_NAME" ;;
  esac
  chmod +x "$BIN_DIR/$BIN_NAME"
fi

# ── 导出模板 ─────────────────────────────────────────────
if [ ! -d "$TPL_DIR/$VER.stable" ]; then
  echo "下载导出模板（约 1GB）..."
  curl -sL -o /tmp/templates.tpz "$BASE/Godot_v${VER}-stable_export_templates.tpz"
  rm -rf /tmp/tpl_extract
  python3 -m zipfile -e /tmp/templates.tpz /tmp/tpl_extract/
  mv /tmp/tpl_extract/templates "$TPL_DIR/$VER.stable"
fi

echo "$BIN_DIR" >> "$GITHUB_PATH"
echo "Godot 就绪: $BIN_DIR/$BIN_NAME"
