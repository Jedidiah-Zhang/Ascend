"""定点数值原语 — 精确语义内核的地基（WC-4.4）。

目标（WC-4.4 完全体）：语义核中的一切运算都由**整数**构成，跨平台逐位
一致；浮点只作为显式孤岛。本模块提供二进制定点 Q(bits) 的基础运算：

- 表示：raw 整数，真值 = raw / 2**bits（精确可表示）；
- 舍入：**半偶**（与项目既有 ``round_half_even`` 语义一致），
  由纯整数比较实现，不经过浮点；
- 溢出：**显式失败**（``OverflowError``），不静默回绕；调用方可显式
  ``clamp``；``fit`` 为预置的边界写入检查原语（当前暂无生产调用方，
  接线列入 #53 后续）；
- 除法/开方：先按声明精度放大再做整数运算并半偶舍入。

保守默认（待 #53 未决项裁决后固化）：Q 格式的 bits 由调用方逐处声明；
有符号字宽检查用 ``fit``；本模块不做任何浮点转换的隐式回退。

``quantize``/``to_float`` 只允许出现在**边界**（声明数据装载、报告与
测试），语义核路径不得调用——由后续门禁（P4）巡检。
"""

from __future__ import annotations

from fractions import Fraction

__all__ = [
    "add",
    "clamp",
    "div",
    "fit",
    "from_ratio",
    "mul",
    "round_half_even_div",
    "sqrt",
    "to_float",
    "quantize",
]


def round_half_even_div(num: int, den: int) -> int:
    """整数除法的半偶舍入（den ≠ 0；纯整数实现，无浮点）。

    分母为负时按 ``num/den`` 原值计算（内部统一为正分母）。
    """
    if den == 0:
        raise ValueError("分母不能为零")
    if den < 0:
        num, den = -num, -den
    quotient, remainder = divmod(num, den)
    twice = 2 * remainder
    if twice > den or (twice == den and quotient % 2 != 0):
        quotient += 1
    return quotient


def add(a: int, b: int) -> int:
    """定点加法（同 bits）：直接相加，无精度损失。"""
    return a + b


def mul(a: int, b: int, bits: int) -> int:
    """定点乘法：``a*b / 2**bits``，半偶舍入。"""
    _check_bits(bits)
    return round_half_even_div(a * b, 1 << bits)


def div(a: int, b: int, bits: int) -> int:
    """定点除法：``(a << bits) / b``，半偶舍入（除零即 ``ZeroDivisionError``）。"""
    _check_bits(bits)
    if b == 0:
        raise ZeroDivisionError("定点除法除数为零")
    return round_half_even_div(a << bits, b)


def sqrt(a: int, bits: int) -> int:
    """定点开方：``sqrt(a / 2**bits)`` 的 Q(bits) 表示，半偶舍入。

    ``a < 0`` 即 ``ValueError``（WC-9.1：定义域外 fail-closed）。
    """
    _check_bits(bits)
    if a < 0:
        raise ValueError(f"定点开方定义域非负: {a!r}")
    scaled = _isqrt_floor(a << bits)
    remainder = (a << bits) - scaled * scaled
    # 最邻近舍入：比较 remainder 与 floor + 1/4（(k+0.5)² = k²+k+0.25；
    # 平方根不会恰好落在半整数上，无平局分支）
    if 4 * remainder > 4 * scaled + 1:
        scaled += 1
    return scaled


def clamp(raw: int, low: int, high: int) -> int:
    """限幅（含端点）；上下界倒置即拒绝。"""
    if low > high:
        raise ValueError(f"限幅区间倒置: [{low}, {high}]")
    return max(low, min(high, raw))


def fit(raw: int, width: int, *, signed: bool = True) -> int:
    """把 raw 装入声明字宽；超界即 ``OverflowError``（不静默回绕）。

    Args:
        raw: 待装入的整数。
        width: 字宽（bit 数，正整数）。
        signed: 是否有符号（二补码语义）。

    Returns:
        原值（通过检查时）。
    """
    if type(width) is not int or width <= 0:
        raise ValueError(f"字宽必须为正整数: {width!r}")
    if signed:
        low, high = -(1 << (width - 1)), (1 << (width - 1)) - 1
    else:
        low, high = 0, (1 << width) - 1
    if not (low <= raw <= high):
        raise OverflowError(
            f"定点溢出（WC-9.4）：{raw} 超出 {'signed' if signed else 'unsigned'} "
            f"{width} bit 范围 [{low}, {high}]"
        )
    return raw


def from_ratio(num: int, den: int, bits: int) -> int:
    """按半偶舍入把精确分数 ``num/den`` 表示为 Q(bits)（常量装载用）。"""
    _check_bits(bits)
    if den <= 0:
        raise ValueError(f"分母必须为正整数: {den!r}")
    return round_half_even_div(num * (1 << bits), den)


def quantize(value: float, bits: int) -> int:
    """浮点 → 定点（**仅供边界**：声明装载/报告/测试）。

    使用 ``Fraction`` 精确转换浮点值，再半偶舍入；语义核不得调用。
    """
    _check_bits(bits)
    exact = Fraction(value) * (1 << bits)
    return round_half_even_div(exact.numerator, exact.denominator)


def to_float(raw: int, bits: int) -> float:
    """定点 → 浮点（**仅供边界**：报告/测试）。"""
    _check_bits(bits)
    return raw / (1 << bits)


def _check_bits(bits: int) -> None:
    if type(bits) is not int or bits < 0:
        raise ValueError(f"定点精度必须为非负整数: {bits!r}")


def _isqrt_floor(value: int) -> int:
    """整数平方根下取整（Python 3.8+ 标准库 ``math.isqrt`` 的本地包装）。"""
    import math

    return math.isqrt(value)
