#!/bin/bash
set -e
cd "$(dirname "$0")"

# ── GUT headless 测试运行脚本 ──
# 用法: ./run_tests.sh [unit|integration|all]
# AI 判定: 看最后一行 "GUT_EXIT=0" 即为通过

MODE="${1:-all}"

case "$MODE" in
  unit)
    DIRS="res://tests/unit"
    echo "[GUT] running unit tests..."
    ;;
  integration)
    DIRS="res://tests/integration"
    echo "[GUT] running integration tests..."
    ;;
  all|*)
    DIRS="res://tests/unit,res://tests/integration"
    echo "[GUT] running all tests..."
    ;;
esac

EXIT_CODE=0
godot --headless --path . \
  -s addons/gut/gut_cmdln.gd \
  --verbose \
  -gdir="$DIRS" \
  -ginclude_subdirs \
  -glog=2 \
  -gjunit_xml_file=res://test_report.xml \
  -gexit \
  -gexit_on_success \
  || EXIT_CODE=$?

echo ""
echo "GUT_EXIT=$EXIT_CODE"

if [ "$EXIT_CODE" -eq 0 ]; then
  echo "[GUT] ALL TESTS PASSED"
else
  echo "[GUT] TESTS FAILED (exit=$EXIT_CODE)"
fi

exit $EXIT_CODE
