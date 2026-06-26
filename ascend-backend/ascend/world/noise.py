"""Perlin 噪声 — 种子化、八度叠加、线程安全的二维噪声生成器。

每个 PerlinNoise 实例从 world_seed 派生独立的排列表，
不同线程持有各自实例时完全无共享状态。
"""

import random


class PerlinNoise:
    """种子化二维 Perlin 噪声生成器。

    线程安全：每个实例拥有独立的排列表和梯度表，无外部共享状态。
    相同种子产生相同噪声值，保证确定性。

    用法:
        noise = PerlinNoise(seed=42)
        value = noise.sample(1.5, 3.2)         # 单八度，范围 [-1, 1]
        value = noise.octave(1.5, 3.2, 4)      # 4 八度叠加
    """

    # Ken Perlin 的改进缓动曲线: 6t^5 - 15t^4 + 10t^3
    # 保证边界处一阶和二阶导数为零，消除网格伪影
    _GRADIENTS: list[tuple[float, float]] = [
        (1.0, 1.0), (-1.0, 1.0), (1.0, -1.0), (-1.0, -1.0),
        (1.0, 0.0), (-1.0, 0.0), (0.0, 1.0), (0.0, -1.0),
    ]

    _TABLE_SIZE: int = 256

    def __init__(self, seed: int = 0) -> None:
        """从种子初始化排列表。

        对 world_seed 做哈希派生，确保不同 seed 产生完全不相关的噪声。

        Args:
            seed: 噪声种子。相同种子产生相同的噪声序列。
        """
        rng = random.Random(seed)
        self._perm: list[int] = list(range(self._TABLE_SIZE))
        rng.shuffle(self._perm)
        # 双倍长度避免索引越界的取模操作
        self._perm2: list[int] = self._perm + self._perm

    def __repr__(self) -> str:
        """返回噪声实例标识，不暴露内部排列表。

        Returns:
            含排列表摘要的 repr 字符串。
        """
        return f"PerlinNoise(hash={hash(tuple(self._perm[:4])) & 0xFFFF:04x})"

    # ── 公开 API ──────────────────────────────────────────

    def sample(self, x: float, y: float) -> float:
        """在 (x, y) 处采样单八度 Perlin 噪声。

        Args:
            x: X 坐标（浮点）。
            y: Y 坐标（浮点）。

        Returns:
            噪声值，范围约 [-1, 1]。
        """
        return self._noise2d(x, y)

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

        每个八度的频率翻倍（× lacunarity），振幅减半（× persistence）。
        叠加结果归一化到 [-1, 1]。

        Args:
            x: X 坐标。
            y: Y 坐标。
            octaves: 八度数量，默认 4。
            persistence: 振幅衰减率，默认 0.5。
            lacunarity: 频率倍增率，默认 2.0。
            frequency: 基础频率，默认 1.0。

        Returns:
            叠加后的噪声值，范围 [-1, 1]（近似）。
        """
        total: float = 0.0
        amplitude: float = 1.0
        max_value: float = 0.0
        freq = frequency

        for _ in range(octaves):
            total += self._noise2d(x * freq, y * freq) * amplitude
            max_value += amplitude
            amplitude *= persistence
            freq *= lacunarity

        return total / max_value

    # ── 内部 ──────────────────────────────────────────────

    @staticmethod
    def _fade(t: float) -> float:
        """Ken Perlin 改进缓动曲线。

        Args:
            t: 输入值 [0, 1]。

        Returns:
            缓动后的值。
        """
        return t * t * t * (t * (t * 6.0 - 15.0) + 10.0)

    @staticmethod
    def _lerp(a: float, b: float, t: float) -> float:
        """线性插值。

        Args:
            a: 起始值。
            b: 终止值。
            t: 插值因子 [0, 1]。

        Returns:
            插值结果。
        """
        return a + t * (b - a)

    def _grad(self, hash_val: int, x: float, y: float) -> float:
        """计算哈希值对应的梯度向量与 (x, y) 的点积。

        Args:
            hash_val: 排列表中的哈希值。
            x: X 偏移。
            y: Y 偏移。

        Returns:
            点积值。
        """
        gx, gy = self._GRADIENTS[hash_val & 7]
        return gx * x + gy * y

    def _noise2d(self, x: float, y: float) -> float:
        """二维 Perlin 噪声核心。

        Args:
            x: X 坐标。
            y: Y 坐标。

        Returns:
            噪声值 [-1, 1]。
        """
        # 网格单元
        xi = int(x) & (self._TABLE_SIZE - 1)
        yi = int(y) & (self._TABLE_SIZE - 1)

        # 网格内偏移
        xf = x - int(x)
        yf = y - int(y)

        # 缓动权重
        u = self._fade(xf)
        v = self._fade(yf)

        # 四个角的哈希值
        p = self._perm2
        aa = p[p[xi] + yi]
        ab = p[p[xi] + yi + 1]
        ba = p[p[xi + 1] + yi]
        bb = p[p[xi + 1] + yi + 1]

        # 四个角的梯度点积 → 双线性插值
        x1 = self._lerp(
            self._grad(aa, xf, yf),
            self._grad(ba, xf - 1.0, yf),
            u,
        )
        x2 = self._lerp(
            self._grad(ab, xf, yf - 1.0),
            self._grad(bb, xf - 1.0, yf - 1.0),
            u,
        )

        return self._lerp(x1, x2, v)
