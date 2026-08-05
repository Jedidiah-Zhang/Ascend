"""C 扩展按需编译加载器 — _perlin / _hydrology / _streamlines 共用。

三处 C 加速模块共用同一加载模式：.so 缺失或比 .c 旧时用 gcc 编译。
`.so` 已 gitignore（跨机/跨 Python 版本不提交二进制），首次导入自动重建。

编译失败不静默降级：这些模块的 Python 层是 C 的薄包装（无纯 Python
回退），缺失时启动即失败并给出明确错误，避免静默用错误数据运行。
"""

import ctypes
import subprocess
from pathlib import Path

_GCC = "gcc"
_CFLAGS = ["-O3", "-march=native", "-ffast-math", "-funroll-loops",
           "-shared", "-fPIC"]


def load_c_extension(c_source: str, so_path: str, link_flags: list[str] | None = None) -> ctypes.CDLL:
    """加载（必要时编译）C 扩展。

    Args:
        c_source: .c 源文件路径。
        so_path: .so 输出路径。
        link_flags: 额外链接参数（如 ["-lm"]）。

    Returns:
        加载后的 CDLL 实例。

    Raises:
        RuntimeError: gcc 缺失或编译失败（无纯 Python 回退，直接失败）。
    """
    c_path = Path(c_source)
    so = Path(so_path)
    so.parent.mkdir(parents=True, exist_ok=True)
    if not so.exists() or c_path.stat().st_mtime > so.stat().st_mtime:
        cmd = [_GCC, *_CFLAGS, "-o", str(so), str(c_path), *(link_flags or [])]
        try:
            subprocess.run(cmd, check=True, cwd=str(c_path.parent))
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            raise RuntimeError(
                f"C 扩展编译失败（需要 gcc）: {c_path.name} — {exc}"
            ) from exc
    return ctypes.CDLL(str(so))
