#!/usr/bin/env bash
# Ascend 发布脚本 — 上传到 GitHub Releases（版本化命名，本地不留历史产物）
#
# 用法: bash build/ci/publish_release.sh
# 前置:
#   - gh CLI 已安装并登录（gh auth login）
#   - git tag v<版本> 已创建并推送（版本见 build/nuitka/version.txt）
#   - build/dist/release/ 下已有产物（ascend-linux.tar.gz / ascend-windows.zip）
#
# 流程: 将固定名产物复制为版本化名（ascend-linux-0.0.1-alpha.tar.gz），
# 上传到 GitHub Release 后即删（本地永远只留最新固定名产物）。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VERSION="$(cat "$ROOT/build/nuitka/version.txt")"
TAG="v$VERSION"
RELEASE_DIR="$ROOT/build/dist/release"

if ! command -v gh >/dev/null 2>&1; then
  echo "需要 gh CLI（https://cli.github.com），并先 gh auth login" >&2
  exit 1
fi

if ! git -C "$ROOT" rev-parse "$TAG" >/dev/null 2>&1; then
  echo "git tag $TAG 不存在，先创建并推送:" >&2
  echo "  git tag $TAG && git push origin $TAG" >&2
  exit 1
fi

ASSETS=()
for f in "$RELEASE_DIR"/*; do
  [ -f "$f" ] && ASSETS+=("$f")
done
if [ "${#ASSETS[@]}" -eq 0 ]; then
  echo "build/dist/release/ 下没有产物，先运行各格式 make_* 脚本" >&2
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
gh release create "$TAG" "${VERSIONED[@]}" --title "Ascend $VERSION" --generate-notes
echo "已发布: https://github.com/$(git -C "$ROOT" remote get-url origin | sed -E 's|.*github\.com[:/]([^/]+/[^/]+)(\.git)?$|\1|')/releases/tag/$TAG"
