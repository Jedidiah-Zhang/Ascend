#!/usr/bin/env bash
# Ascend 版本对账 — 单一源 build/nuitka/version.txt
#
# 用法: bash build/ci/check_version.sh [--tag <ref>]
#   --tag <ref>：tag 触发发布时校验触发 tag 与版本文件一致（v<version>），
#   不一致退出 1（阻断 CI release job），防止误打 tag 发布错误版本名。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VERSION="$(cat "$ROOT/build/nuitka/version.txt")"

if [ "${1:-}" = "--tag" ]; then
  REF="${2:?--tag 需要参数（如 github.ref_name）}"
  if [ "$REF" != "v$VERSION" ]; then
    echo "版本对账失败: 触发 tag '$REF' ≠ v$VERSION（build/nuitka/version.txt）" >&2
    echo "请先更新 build/nuitka/version.txt 并重新打 tag" >&2
    exit 1
  fi
fi

echo "版本对账通过: v$VERSION"