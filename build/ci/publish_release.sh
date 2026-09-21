#!/usr/bin/env bash
# Ascend 发布脚本 — 上传研究包到 GitHub Releases（版本化命名，临时副本用后即删）。
#
# 用法: bash build/ci/publish_release.sh [kheker|miskhak]
#   默认 kheker（研究包，对外 research）。miskhak（游戏包）含闭源前端资产，
#   不公开上传——走私有流程，本脚本拒绝。
#
# 前置:
#   - gh CLI 已安装并登录（gh auth login）
#   - git tag research-v<版本> 已创建并推送（版本见 build/version/kheker.txt）
#   - build/dist/kheker/ 下已有产物（build/package.sh kheker 生成）
#
# 流程: 将固定名产物复制为版本化名（ascend-research-linux-0.0.3-alpha.1.tar.gz），
# 上传到 GitHub Release 后即删（本地永远只留最新固定名产物）。
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/lib/common.sh"

CHANNEL="${1:-kheker}"
ascend_require_channel "$CHANNEL"

if [ "$CHANNEL" = "miskhak" ]; then
  echo "游戏包（miskhak）含闭源前端资产，不走公开 Release——请使用私有发布流程。" >&2
  exit 1
fi

VERSION="$(ascend_version "$CHANNEL")"
TAG="$(ascend_product_of "$CHANNEL")-v$VERSION"
RELEASE_DIR="$(ascend_dist_dir "$CHANNEL")"

if ! command -v gh >/dev/null 2>&1; then
  echo "需要 gh CLI（https://cli.github.com），并先 gh auth login" >&2
  exit 1
fi

if ! git -C "$ASCEND_ROOT" rev-parse "$TAG" >/dev/null 2>&1; then
  echo "git tag $TAG 不存在，先创建并推送:" >&2
  echo "  git tag $TAG && git push origin $TAG" >&2
  exit 1
fi

ASSETS=()
for f in "$RELEASE_DIR"/*; do
  [ -f "$f" ] && ASSETS+=("$f")
done
if [ "${#ASSETS[@]}" -eq 0 ]; then
  echo "$RELEASE_DIR/ 下没有产物，先运行: bash build/package.sh kheker" >&2
  exit 1
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

VERSIONED=()
for f in "${ASSETS[@]}"; do
  name="$(basename "$f")"
  if [[ "$name" == *.tar.gz ]]; then
    base="${name%.tar.gz}"; ext=".tar.gz"
  else
    base="${name%.*}"; ext=".${name##*.}"
  fi
  versioned="$base-$VERSION$ext"
  cp "$f" "$TMP/$versioned"
  VERSIONED+=("$TMP/$versioned")
done

echo "发布 $TAG:"
printf '  %s\n' "${VERSIONED[@]}"
gh release create "$TAG" "${VERSIONED[@]}" \
  --title "Ascend Research $VERSION" --generate-notes
echo "已发布: https://github.com/$(git -C "$ASCEND_ROOT" remote get-url origin | sed -E 's|.*github\.com[:/]([^/]+/[^/]+)(\.git)?$|\1|')/releases/tag/$TAG"
