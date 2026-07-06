"""Perlin 噪声生成模块。

用法:
    noise = PerlinNoise(seed=42)
    value = noise.sample(1.5, 3.2)         # 单八度，范围 [-1, 1]
    value = noise.octave(1.5, 3.2, 4)      # 4 八度叠加
"""

import ctypes
import random
import subprocess
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SO = _HERE / "_perlin.so"
_C = _HERE / "_perlin.c"

# 按需编译（.so 不存在或比 .c 旧）
if not _SO.exists() or _C.stat().st_mtime > _SO.stat().st_mtime:
    subprocess.run(
        ["gcc", "-O3", "-march=native", "-ffast-math", "-funroll-loops",
         "-shared", "-fPIC", "-o", str(_SO), str(_C), "-lm"],
        check=True, cwd=str(_HERE),
    )

_LIB = ctypes.CDLL(str(_SO))
_LIB.perlin_sample.argtypes = [
    ctypes.POINTER(ctypes.c_int), ctypes.c_double, ctypes.c_double,
]
_LIB.perlin_sample.restype = ctypes.c_double
_LIB.perlin_octave.argtypes = [
    ctypes.POINTER(ctypes.c_int),
    ctypes.c_double, ctypes.c_double,
    ctypes.c_int,
    ctypes.c_double, ctypes.c_double, ctypes.c_double,
]
_LIB.perlin_octave.restype = ctypes.c_double
_LIB.perlin_octave_grid.argtypes = [
    ctypes.POINTER(ctypes.c_int),
    ctypes.c_double, ctypes.c_double,  # cx, cy (float — 支持半像素偏移)
    ctypes.c_int, ctypes.c_int,  # w, h
    ctypes.c_double,             # frequency
    ctypes.POINTER(ctypes.c_double),  # output array
    ctypes.c_int,                # octaves
    ctypes.c_double, ctypes.c_double,  # persistence, lacunarity
]
_LIB.perlin_octave_grid.restype = None


class PerlinNoise:
    """种子化二维 Perlin 噪声生成器。

    线程安全：每个实例的排列表为独立数组，无共享状态。
    """

    def __init__(self, seed: int = 0) -> None:
        """从种子初始化排列表。

        Args:
            seed: 噪声种子。相同种子产生相同的噪声序列。
        """
        rng = random.Random(seed)
        p = list(range(256))
        for i in range(255, 0, -1):
            j = rng.randint(0, i)
            p[i], p[j] = p[j], p[i]
        # 双倍长度数组（C 端使用）
        perm = (ctypes.c_int * 512)()
        for i in range(256):
            perm[i] = p[i]
            perm[i + 256] = p[i]
        self._perm = perm

    def __repr__(self) -> str:
        """返回噪声实例标识。"""
        h = hash(tuple(self._perm[:4])) & 0xFFFF
        return f"PerlinNoise(hash={h:04x})"

    def sample(self, x: float, y: float) -> float:
        """在 (x, y) 处采样单八度 Perlin 噪声。

        Args:
            x: X 坐标。
            y: Y 坐标。

        Returns:
            噪声值，范围约 [-1, 1]。
        """
        return _LIB.perlin_sample(self._perm, x, y)

    def octave(
        self,
        x: float,
        y: float,
        octaves: int = 4,
        *,
        persistence: float = 0.5,
        lacunarity: float = 2.0,
        frequency: float = 1.0,
    ) -> float:
        """多八度叠加（分形噪声）。

        Args:
            x: X 坐标。
            y: Y 坐标。
            octaves: 八度数量。
            persistence: 振幅衰减率。
            lacunarity: 频率倍增率。
            frequency: 基础频率。

        Returns:
            叠加后的噪声值，范围 [-1, 1]。
        """
        return _LIB.perlin_octave(
            self._perm, x, y, octaves, persistence, lacunarity, frequency,
        )

    def octave_grid(
        self,
        cx: float, cy: float, w: int, h: int,
        *,
        frequency: float = 1.0,
        octaves: int = 4,
        persistence: float = 0.5,
        lacunarity: float = 2.0,
    ) -> list[float]:
        """在网格上批量采样多八度噪声。

        一次性计算 w×h 个坐标，仅一次 ctypes 跨语言调用。
        cx, cy 支持浮点偏移（如 +0.5），避免采样在整数网格点（噪声零点）。

        Args:
            cx, cy: 起始坐标（支持浮点偏移）。
            w, h: 网格宽高。
            frequency: 噪声频率。
            octaves: 八度数量。
            persistence: 振幅衰减率。
            lacunarity: 频率倍增率。

        Returns:
            长度为 w*h 的浮点列表，按行排列。
        """
        size = w * h
        output = (ctypes.c_double * size)()
        _LIB.perlin_octave_grid(
            self._perm, cx, cy, w, h,
            frequency, output,
            octaves, persistence, lacunarity,
        )
        return list(output)
