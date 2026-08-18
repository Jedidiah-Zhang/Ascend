"""种子派生确定性随机工具 — 生成层随机源单一入口。

契约：任何"位置相关 + seed 相关"的确定性随机（tile 级 / cell 级）
都必须经本模块函数，掺入世界 seed。禁止裸坐标哈希或裸 random——
否则同一模式在所有世界重复（如旧版湿地斑块：所有世界湿地位置相同）。

本模块只依赖整数运算，跨平台/跨架构位级一致（IEEE-754 无关）。
"""

import math

_KNUTH_P1 = 2654435761
_KNUTH_P2 = 1597334677
_KNUTH_P3 = 2246822519
_SPLITMIX_MUL = 3266489917


def seed_angle(seed: int) -> float:
    """seed → 确定性角度 [0, 2π)。

    Knuth 乘法哈希将任意整数种子均匀映射到角度；温度梯度方向
    与盛行风向均由此派生（各自独立调用点，同一 seed 结果相同）。
    """
    return ((seed * _KNUTH_P1) & 0xFFFFFFFF) / 0xFFFFFFFF * 2.0 * math.pi


def cell_hash(wx: int, wy: int, seed: int = 0) -> float:
    """(wx, wy, seed) → 确定性哈希值 [0, 1)。

    tile/cell 级伪随机用：相同坐标 + 相同 seed 恒等（确定性），
    不同 seed 产生完全不同的模式（世界间差异），不同坐标均匀分布。

    实现：坐标与 seed 线性混合（各用不同 Knuth 素数）后经
    splitmix64 风格 xor-shift + 乘法混淆（xorshift 自逆 + 乘奇数，
    为 Z/2³² 上双射，仅保证混淆均匀；线性混合是 64→32 位压缩，
    非单射），避免 seed 仅造成坐标平移。
    """
    h = (wx * _KNUTH_P1 + wy * _KNUTH_P2 + (seed & 0xFFFFFFFF) * _KNUTH_P3) & 0xFFFFFFFF
    h ^= h >> 13
    h = (h * _SPLITMIX_MUL) & 0xFFFFFFFF
    h ^= h >> 16
    return h / 0xFFFFFFFF


__all__ = ["seed_angle", "cell_hash"]