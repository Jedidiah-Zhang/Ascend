"""C 扩展按需编译加载器 — _perlin / _hydrology / _streamlines 共用。

三处 C 加速模块共用同一加载模式：扩展缺失或比 .c 旧时用 gcc 编译
（Windows 下为 .dll，由 mingw gcc 产出；其余平台为 .so）。
`.so`/`.dll` 已 gitignore（跨机/跨 Python 版本不提交二进制），首次导入自动重建。

编译失败不静默降级：这些模块的 Python 层是 C 的薄包装（无纯 Python
回退），缺失时启动即失败并给出明确错误，避免静默用错误数据运行。
"""

import ctypes
import os
import subprocess
from pathlib import Path

_GCC = "gcc"
# 无 -ffast-math（破坏 IEEE-754 语义，令同 seed 世界跨机器不确定）；
# 无 -march=native（产物绑定本机指令集，跨机器不可移植）——gcc 默认
# 基线即可：x86 链 → x86-64，aarch64 链 → armv8-a，跨架构均可编译。
_CFLAGS = ["-O3", "-funroll-loops", "-shared", "-fPIC"]

_EXT_SUFFIX = ".dll" if os.name == "nt" else ".so"


def c_ext_path(c_source: str) -> str:
    """C 源码对应的动态库路径（Windows .dll，其余平台 .so）。"""
    return str(Path(c_source).with_suffix(_EXT_SUFFIX))


def load_c_extension(c_source: str, so_path: str, link_flags: list[str] | None = None) -> ctypes.CDLL:
    """加载（必要时编译）C 扩展。

    Args:
        c_source: .c 源文件路径。
        so_path: 动态库输出路径（.so/.dll；后缀不符时按平台纠正）。
        link_flags: 额外链接参数（如 ["-lm"]）。

    Returns:
        加载后的 CDLL 实例。

    Raises:
        RuntimeError: gcc 缺失或编译失败（无纯 Python 回退，直接失败）。
    """
    c_path = Path(c_source)
    so = Path(so_path)
    if so.suffix != _EXT_SUFFIX:
        so = so.with_suffix(_EXT_SUFFIX)
    so.parent.mkdir(parents=True, exist_ok=True)
    # 源码缺失（打包环境不随包分发 .c）且动态库已存在：直接加载。
    # 源码缺失且动态库也缺失：无法编译也无库可载，明确报错。
    if not c_path.exists() and not so.exists():
        raise RuntimeError(
            f"C 扩展源码与动态库均缺失: {c_path.name}（打包环境需携带 {so.name}）"
        )
    if not so.exists() or (
        c_path.exists() and c_path.stat().st_mtime > so.stat().st_mtime
    ):
        cmd = [_GCC, *_CFLAGS, "-o", str(so), str(c_path), *(link_flags or [])]
        try:
            subprocess.run(cmd, check=True, cwd=str(c_path.parent))
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            raise RuntimeError(
                f"C 扩展编译失败（需要 gcc）: {c_path.name} — {exc}"
            ) from exc
    return ctypes.CDLL(str(so))
