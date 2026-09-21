"""预计算表超越函数 — cos 四分之一周期表 + 纯整数查询。

- 表数据在 ``frozen_tables.py``（生成物，摘要入库）；查询路径**无浮点、
  无 libm**，跨平台逐位一致；
- 角度为 Q(TABLE_BITS) 弧度，``cos_q`` 返回同精度 Q 值；
- 查询流水线：模 2π 归约 → 象限映射 → 表内线性插值（半偶舍入）；
- **声明误差上界**：插值 ≤ (Δθ)²/8·max|f''| = ((π/2)/1024)²/8 ≈ 1.18e-6；
  归约 ≤ 2π·2^-30 ≈ 5.9e-9 → 总界取 ``DECLARED_EPSILON``（含裕度，测试
  以密集采样验证）。

本模块只提供单一表精度：``bits != TABLE_BITS`` 即拒绝（不静默换精度）。
"""

from __future__ import annotations

from .fixed import div, mul, round_half_even_div
from .frozen_tables import (
    ACOS_SEGMENTS,
    ACOS_TABLE_Q,
    COS_QUARTER_Q,
    HALF_PI_Q,
    PI_Q,
    SEGMENTS,
    TABLE_BITS,
    TANH_MAX_Q,
    TANH_MIN_Q,
    TANH_SEGMENTS,
    TANH_TABLE_Q,
    TWO_PI_Q,
)

__all__ = [
    "ACOS_MAX_ERROR", "DECLARED_EPSILON", "DEG_FACTOR_Q",
    "TANH_MAX_ERROR", "TAN_MAX_ERROR", "acos_q", "cos_q", "degrees_q",
    "sin_q", "tan_q", "tanh_q",
]

# 声明误差上界（相对 1 的绝对误差；见模块 docstring 的推导）
DECLARED_EPSILON: float = 1e-5


def cos_q(theta_q: int, bits: int = TABLE_BITS) -> int:
    """定点余弦：``theta_q`` 为 Q(bits) 弧度，返回 Q(bits) 的 cos 值。

    Raises:
        ValueError: ``bits`` 与表精度不符（不静默换精度）。
    """
    if bits != TABLE_BITS:
        raise ValueError(
            f"预计算表 cos 只支持 Q({TABLE_BITS})，实际 Q({bits})"
        )
    t = theta_q % TWO_PI_Q
    quarter = t // HALF_PI_Q
    r = t % HALF_PI_Q
    if quarter == 0:
        return _interp(r)
    if quarter == 2:
        return -_interp(r)
    # 象限 1：cos(π/2 + r) = -cos(π/2 − r)；象限 3：cos(3π/2 + r) = +cos(π/2 − r)
    value = _interp(HALF_PI_Q - r)
    return -value if quarter == 1 else value


def _interp(r: int) -> int:
    """表内线性插值：r ∈ [0, π/2) 的 Q 值 → cos(r) 的 Q 值（半偶舍入）。"""
    position = r * SEGMENTS
    index = position // HALF_PI_Q
    remainder = position - index * HALF_PI_Q
    if index >= SEGMENTS:  # 防御：r 不应达到端点
        return COS_QUARTER_Q[SEGMENTS]
    left = COS_QUARTER_Q[index] * (HALF_PI_Q - remainder)
    right = COS_QUARTER_Q[index + 1] * remainder
    return round_half_even_div(left + right, HALF_PI_Q)


def sin_q(theta_q: int, bits: int = TABLE_BITS) -> int:
    """定点正弦：sin(θ) = cos(θ − π/2)（复用同一张 cos 表，无新数据）。"""
    return cos_q(theta_q - HALF_PI_Q, bits)

# tanh 声明误差：插值 ≤ (16/2048)²/8·0.77 ≈ 5.9e-6 + 域外饱和 2.3e-7 + 舍入
TANH_MAX_ERROR: float = 1e-5


def tanh_q(x_q: int, bits: int = TABLE_BITS) -> int:
    """定点双曲正切：``x_q`` 为 Q(bits) 无量纲输入，返回 Q(bits)。

    区间 [-8, 8] 内查表线性插值（半偶舍入）；域外**饱和**到端点值
    （|tanh(±8) − ±1| ≤ 2.3e-7，含在声明界内）。
    """
    if bits != TABLE_BITS:
        raise ValueError(
            f"预计算表 tanh 只支持 Q({TABLE_BITS})，实际 Q({bits})"
        )
    span = TANH_MAX_Q - TANH_MIN_Q
    x = max(TANH_MIN_Q, min(TANH_MAX_Q, x_q))
    position = (x - TANH_MIN_Q) * TANH_SEGMENTS
    index = position // span
    if index >= TANH_SEGMENTS:
        return TANH_TABLE_Q[TANH_SEGMENTS]
    remainder = position - index * span
    left = TANH_TABLE_Q[index] * (span - remainder)
    right = TANH_TABLE_Q[index + 1] * remainder
    return round_half_even_div(left + right, span)

# acos 声明误差：端点导数无界，均匀采样 16384 段在 |x|→1 处插值误差
# 实测最坏 ≈ 1.28e-3 rad（密集采样）；取 2e-3 含裕度
ACOS_MAX_ERROR: float = 2e-3
# tan 由 sin/cos 表相除；|x| ≤ 1.4 rad（纬度 ≤ 80°）内误差 ≤ ~7e-4
TAN_MAX_ERROR: float = 1e-3

# degrees(x) = x·180/π；180/π 的 Q 值由 PI_Q 整数半偶除得
DEG_FACTOR_Q: int = round_half_even_div(180 * (1 << TABLE_BITS) * (1 << TABLE_BITS), PI_Q)


def acos_q(x_q: int, bits: int = TABLE_BITS) -> int:
    """定点反余弦：``x_q`` ∈ [−1, 1] 的 Q(bits)，返回 [0, π] 的 Q(bits)。

    域外输入**钳制**到端点（对应声明中的 clamp 语义）。
    """
    if bits != TABLE_BITS:
        raise ValueError(
            f"预计算表 acos 只支持 Q({TABLE_BITS})，实际 Q({bits})"
        )
    scale = 1 << TABLE_BITS
    span = 2 * scale
    x = max(-scale, min(scale, x_q))
    position = (x + scale) * ACOS_SEGMENTS
    index = position // span
    if index >= ACOS_SEGMENTS:
        return ACOS_TABLE_Q[ACOS_SEGMENTS]
    remainder = position - index * span
    left = ACOS_TABLE_Q[index] * (span - remainder)
    right = ACOS_TABLE_Q[index + 1] * remainder
    return round_half_even_div(left + right, span)


def tan_q(x_q: int, bits: int = TABLE_BITS) -> int:
    """定点正切：tan = sin/cos（两张既有表相除，半偶舍入）。"""
    if bits != TABLE_BITS:
        raise ValueError(
            f"预计算表 tan 只支持 Q({TABLE_BITS})，实际 Q({bits})"
        )
    return div(sin_q(x_q, bits), cos_q(x_q, bits), bits)


def degrees_q(x_q: int, bits: int = TABLE_BITS) -> int:
    """定点弧度 → 角度：x·180/π（定点乘，半偶舍入）。"""
    if bits != TABLE_BITS:
        raise ValueError(
            f"预计算表 degrees 只支持 Q({TABLE_BITS})，实际 Q({bits})"
        )
    return mul(x_q, DEG_FACTOR_Q, bits)
