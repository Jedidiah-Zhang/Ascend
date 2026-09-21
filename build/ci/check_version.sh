#!/usr/bin/env bash
# Ascend 版本对账 — 单一源 build/version/{core,miskhak,kheker}.txt
#
# 用法:
#   bash build/ci/check_version.sh [--tag <ref>]
#
#   无 --tag：打印两通道版本，并校验 core 是两个产品版本的前缀。
#   --tag  ：tag 触发发布时校验触发 tag 与对应通道版本一致
#            （game-v<版本> → miskhak，research-v<版本> → kheker），
#            不一致退出 1（阻断 CI release job），防止误打 tag 发布错误版本名。
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/lib/common.sh"

CORE="$(ascend_version core)"
MISKHAK="$(ascend_version miskhak)"
KHEKER="$(ascend_version kheker)"

# core 必须是产品版本的前缀（产品版本 = 核心版本 + .序号）
for channel in miskhak kheker; do
  version="$(ascend_version "$channel")"
  case "$version" in
    "$CORE".*) ;;
    *)
      echo "版本对账失败: $channel 版本 '$version' 未以核心版本 '$CORE' 为前缀" >&2
      echo "产品版本应为 <core>.<序号>（如 $CORE.1）" >&2
      exit 1
      ;;
  esac
done

if [ "${1:-}" = "--tag" ]; then
  REF="${2:?--tag 需要参数（如 github.ref_name）}"
  CHANNEL="$(ascend_channel_of_tag "$REF")" || {
    echo "版本对账失败: 无法识别的 tag '$REF'（应为 game-v* 或 research-v*）" >&2
    exit 1
  }
  EXPECTED="$(ascend_product_of "$CHANNEL")-v$(ascend_version "$CHANNEL")"
  if [ "$REF" != "$EXPECTED" ]; then
    echo "版本对账失败: 触发 tag '$REF' ≠ $EXPECTED（build/version/$CHANNEL.txt）" >&2
    echo "请先更新 build/version/$CHANNEL.txt 并重新打 tag" >&2
    exit 1
  fi
  echo "版本对账通过: $REF（核心 $CORE）"
else
  echo "版本对账通过: core $CORE | game(miskhak) $MISKHAK | research(kheker) $KHEKER"
fi
