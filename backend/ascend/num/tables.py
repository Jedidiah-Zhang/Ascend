"""冻表超越函数 — cos 四分之一周期表 + 纯整数查询（issue #53 P1 第二粒）。

- 表数据在 ``frozen_tables.py``（生成物，摘要入库）；查询路径**无浮点、
  无 libm**，跨平台逐位一致；
- 角度为 Q(TABLE_BITS) 弧度，``cos_q`` 返回同精度 Q 值；
- 查询流水线：模 2π 归约 → 象限映射 → 表内线性插值（半偶舍入）；
- **声明误差上界**：插值 ≤ (Δθ)²/8·max|f''| = ((π/2)/1024)²/8 ≈ 1.18e-6；
  归约 ≤ 2π·2^-30 ≈ 5.9e-9 → 总界取 ``DECLARED_EPSILON``（含裕度，测试
  以密集采样验证）。

多精度/其他函数（sin/tanh/acos）按 #53 顺序后续加入；本模块只支持
表精度（``bits != TABLE_BITS`` 即拒绝，不静默换精度）。
"""

from __future__ import annotations

from .fixed import round_half_even_div
from .frozen_tables import (
    COS_QUARTER_Q,
    HALF_PI_Q,
    SEGMENTS,
    TABLE_BITS,
    TANH_MAX_Q,
    TANH_MIN_Q,
    TANH_SEGMENTS,
    TANH_TABLE_Q,
    TWO_PI_Q,
)

__all__ = ["DECLARED_EPSILON", "TANH_MAX_ERROR", "cos_q", "sin_q", "tanh_q"]

# 声明误差上界（相对 1 的绝对误差；见模块 docstring 的推导）
DECLARED_EPSILON: float = 1e-5


def cos_q(theta_q: int, bits: int = TABLE_BITS) -> int:
    """定点余弦：``theta_q`` 为 Q(bits) 弧度，返回 Q(bits) 的 cos 值。

    Raises:
        ValueError: ``bits`` 与冻表精度不符（不静默换精度）。
    """
    if bits != TABLE_BITS:
        raise ValueError(
            f"冻表 cos 只支持 Q({TABLE_BITS})，实际 Q({bits})；"
            f"多精度表按 #53 后续粒度加入"
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
    """定点正弦：sin(θ) = cos(θ − π/2)（复用同一冻表，无新数据）。"""
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
            f"冻表 tanh 只支持 Q({TABLE_BITS})，实际 Q({bits})；"
            f"多精度表按 #53 后续粒度加入"
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
