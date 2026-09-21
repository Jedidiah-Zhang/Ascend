#!/usr/bin/env bash
# 编译子进程环境净化 — 只放行构建必需变量，凭据一律不进编译进程。
#
# 用法：PYTHONPATH=... ascend_build_env python -m nuitka ...
#      （PYTHONPATH 已在白名单内；wine/Windows 变量按平台带上）

# 白名单：构建必需 + 平台运行需要；新增项须确认非凭据类。
ASCEND_BUILD_ENV_KEEP=(
  # 基础（shell/Python/临时目录/语言）
  PATH HOME TMPDIR TMP TEMP LANG LANGUAGE LC_ALL LC_CTYPE LC_NUMERIC
  PYTHONPATH PYTHONHASHSEED VIRTUAL_ENV
  # 编译器与可复现构建
  CC CXX CFLAGS CXXFLAGS LDFLAGS SOURCE_DATE_EPOCH
  # wine（Windows 交叉编译）
  WINEPREFIX WINEARCH WINEDEBUG DISPLAY WAYLAND_DISPLAY XDG_RUNTIME_DIR
  # 原生 Windows（GitHub Actions / msys）
  SYSTEMROOT SystemRoot WINDIR COMSPEC PATHEXT
  USERPROFILE APPDATA LOCALAPPDATA PROGRAMFILES PROGRAMFILES_X86
)

ascend_build_env() {
  local out=() name
  for name in "${ASCEND_BUILD_ENV_KEEP[@]}"; do
    [[ -n "${!name+x}" ]] && out+=("$name=${!name}")
  done
  env -i "${out[@]}" "$@"
}
