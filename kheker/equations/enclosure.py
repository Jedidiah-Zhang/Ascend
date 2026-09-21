"""包络表示与代数（研究侧验证库）。

包络 = 一个量的可能取值集合。本模块提供两种表示：

- :class:`Interval`：定点整数域上的闭区间 ``[lo, hi]``（Q(bits)）；
- :class:`DiscreteSet`：类别量的有限取值集合（int/str/bool，可空）。

共同协议：``contains`` / ``union`` / ``intersect`` / ``is_empty`` /
``is_singleton`` / ``width``（宽度 = "不确定度"，离散集合取元素数-1 或
无穷大语义由调用方决定，见方法文档）。

运算（仅 :class:`Interval`）：加减乘除、取负、clamp、min/max 的**区间
扩展**，全部由整数运算实现（无浮点），并满足**包含保持**：

    对任意 a ∈ A、b ∈ B，point_op(a, b) ∈ op(A, B)

其中 point 运算由 :mod:`olam.kernel.fixed` 提供（半偶舍入），
包含关系由 ``testbench/world/test_kernel_enclosure.py`` 采样验证。定义域外/除零等未定义
情形一律**抛出**（fail-closed），由上层决定是否降级为全集合。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from olam.kernel.fixed import round_half_even_div

__all__ = [
    "DiscreteSet", "Interval",
    "acos_enclosure", "cos_enclosure", "degrees_enclosure",
    "sin_enclosure", "tan_enclosure", "tanh_enclosure",
]


@dataclass(frozen=True, slots=True)
class Interval:
    """定点闭区间 ``[lo, hi]``，单位 = Q(bits)。"""

    lo: int
    hi: int
    bits: int

    def __post_init__(self) -> None:
        if type(self.bits) is not int or self.bits < 0:
            raise ValueError(f"bits 必须为非负整数: {self.bits!r}")
        if self.lo > self.hi:
            raise ValueError(f"区间下界大于上界: [{self.lo}, {self.hi}]")

    # ── 基本性质 ─────────────────────────────────────────

    @classmethod
    def point(cls, value: int, bits: int) -> "Interval":
        """单点包络（退化区间）。"""
        return cls(value, value, bits)

    def contains(self, value: int) -> bool:
        return self.lo <= value <= self.hi

    def is_empty(self) -> bool:
        return False  # 闭区间表示下构造即非空

    def is_singleton(self) -> bool:
        return self.lo == self.hi

    def width(self) -> int:
        """宽度（上界 − 下界，Q 单位）：点包络宽度为 0。"""
        return self.hi - self.lo

    @staticmethod
    def hull(intervals: Iterable["Interval"]) -> "Interval":
        """凸包（并的最小覆盖区间）；空序列即拒绝。"""
        items = tuple(intervals)
        if not items:
            raise ValueError("凸包需要至少一个区间")
        bits = items[0].bits
        if any(item.bits != bits for item in items):
            raise ValueError("凸包要求同一 Q 精度")
        return Interval(
            min(item.lo for item in items),
            max(item.hi for item in items),
            bits,
        )

    def to_floats(self) -> tuple[float, float]:
        """调试/报告用（边界；语义核不使用）。"""
        scale = 1 << self.bits
        return self.lo / scale, self.hi / scale

    # ── 集合代数 ─────────────────────────────────────────

    def union(self, other: "Interval") -> "Interval":
        """并的凸包（保持包含性；不精确表示不连通并集）。"""
        self._same_bits(other)
        return Interval(min(self.lo, other.lo), max(self.hi, other.hi), self.bits)

    def intersect(self, other: "Interval") -> "Interval | None":
        """交集；不连通即返回 None（fail-closed，不臆造空区间）。"""
        self._same_bits(other)
        lo, hi = max(self.lo, other.lo), min(self.hi, other.hi)
        return None if lo > hi else Interval(lo, hi, self.bits)

    # ── 区间扩展（包含保持）──────────────────────────────

    def add(self, other: "Interval") -> "Interval":
        self._same_bits(other)
        return Interval(self.lo + other.lo, self.hi + other.hi, self.bits)

    def sub(self, other: "Interval") -> "Interval":
        self._same_bits(other)
        return Interval(self.lo - other.hi, self.hi - other.lo, self.bits)

    def neg(self) -> "Interval":
        return Interval(-self.hi, -self.lo, self.bits)

    def mul(self, other: "Interval") -> "Interval":
        """乘法区间扩展：对四个端点积取下确界/上确界。

        注意：点乘由 ``fixed.mul`` 定义（含半偶舍入），端点积是精确整数
        积的包络——包含保持成立（舍入偏差 ≤ 0.5 ulp 含在端点选择中，
        见测试）。
        """
        self._same_bits(other)
        candidates = (
            self.lo * other.lo, self.lo * other.hi,
            self.hi * other.lo, self.hi * other.hi,
        )
        scale = 1 << self.bits
        # 精确积的包络 → 除以 scale 时向外取整
        lo = _floor_div(min(candidates), scale)
        hi = _ceil_div(max(candidates), scale)
        return Interval(lo, hi, self.bits)

    def div(self, other: "Interval") -> "Interval":
        """除法区间扩展：分母含 0 即拒绝（fail-closed）。

        真商的极值在矩形四角取得；每角取 floor/ceil 向外取整再取凸包，
        保证包含所有点商（含半偶舍入结果）。
        """
        self._same_bits(other)
        if other.contains(0):
            raise ZeroDivisionError("区间除法分母含 0（未定义）")
        scale = 1 << self.bits
        floors = (
            _floor_div(self.lo * scale, other.lo),
            _floor_div(self.lo * scale, other.hi),
            _floor_div(self.hi * scale, other.lo),
            _floor_div(self.hi * scale, other.hi),
        )
        ceils = (
            _ceil_div(self.lo * scale, other.lo),
            _ceil_div(self.lo * scale, other.hi),
            _ceil_div(self.hi * scale, other.lo),
            _ceil_div(self.hi * scale, other.hi),
        )
        return Interval(min(floors), max(ceils), self.bits)

    def clamp(self, low: int, high: int) -> "Interval":
        """定点 clamp（含端点）；区间与界求交后限幅。"""
        lo = max(self.lo, low)
        hi = min(self.hi, high)
        if lo > hi:  # 区间完全在界外 → 退化为最近的界
            value = low if self.hi < low else high
            return Interval.point(value, self.bits)
        return Interval(lo, hi, self.bits)

    def scale_by_ratio(self, numerator: int, denominator: int) -> "Interval":
        """按精确比例 ``numerator/denominator`` 缩放（常量乘除，半偶舍入）。"""
        if denominator == 0:
            raise ZeroDivisionError("比例分母为零")
        lo = round_half_even_div(self.lo * numerator, denominator)
        hi = round_half_even_div(self.hi * numerator, denominator)
        if numerator < 0:
            lo, hi = hi, lo
        return Interval(min(lo, hi), max(lo, hi), self.bits)

    # ── 内部 ─────────────────────────────────────────────

    def _same_bits(self, other: "Interval") -> None:
        if other.bits != self.bits:
            raise ValueError(
                f"Q 精度不一致: {self.bits} vs {other.bits}"
            )


@dataclass(frozen=True, slots=True)
class DiscreteSet:
    """类别量的有限取值集合（空集 = 无可能取值，构造即允许）。"""

    values: frozenset

    def contains(self, value: object) -> bool:
        return value in self.values

    def is_empty(self) -> bool:
        return not self.values

    def is_singleton(self) -> bool:
        return len(self.values) == 1

    def width(self) -> float:
        """宽度语义：单点 0；否则 1.0（离散度量：任两值距离为 1）。"""
        return 0.0 if self.is_singleton() else 1.0

    def union(self, other: "DiscreteSet") -> "DiscreteSet":
        return DiscreteSet(self.values | other.values)

    def intersect(self, other: "DiscreteSet") -> "DiscreteSet":
        return DiscreteSet(self.values & other.values)

    @classmethod
    def of(cls, values: Iterable) -> "DiscreteSet":
        return cls(frozenset(values))


def _floor_div(num: int, den: int) -> int:
    return num // den


def _ceil_div(num: int, den: int) -> int:
    return -(-num // den)


# ── 预计算表函数的区间扩展（结果含表声明误差膨胀）───────────


def _ceil_q(value: float, bits: int) -> int:
    """浮点常量向上取整为 Q（仅用于误差膨胀边界）。"""
    scaled = value * (1 << bits)
    return int(scaled) if scaled == int(scaled) else int(scaled) + 1


def _contains_multiple(a: int, b: int, modulus: int) -> bool:
    """区间 [a,b] 是否包含 modulus 的整数倍（modulus > 0）。"""
    first = -((-a) // modulus)   # ceil(a / modulus)
    return first * modulus <= b


def _contains_offset_multiple(
    a: int, b: int, offset: int, modulus: int,
) -> bool:
    """区间 [a,b] 是否包含 offset + k·modulus。"""
    return _contains_multiple(a - offset, b - offset, modulus)


def cos_enclosure(box: Interval) -> Interval:
    """cos 的区间扩展：端点 + 极值点判定，膨胀 tables.DECLARED_EPSILON。"""
    from olam.kernel.frozen_tables import PI_Q, TABLE_BITS, TWO_PI_Q
    from olam.kernel.tables import DECLARED_EPSILON, cos_q

    if box.bits != TABLE_BITS:
        raise ValueError(f"预计算表包络只支持 Q({TABLE_BITS})")
    scale = 1 << TABLE_BITS
    lo = min(cos_q(box.lo), cos_q(box.hi))
    hi = max(cos_q(box.lo), cos_q(box.hi))
    if _contains_multiple(box.lo, box.hi, TWO_PI_Q):
        hi = max(hi, scale)                      # cos = +1
    if _contains_offset_multiple(box.lo, box.hi, PI_Q, TWO_PI_Q):
        lo = min(lo, -scale)                     # cos = −1
    eps = _ceil_q(DECLARED_EPSILON, TABLE_BITS)
    return Interval(lo - eps, hi + eps, TABLE_BITS)


def sin_enclosure(box: Interval) -> Interval:
    """sin 的区间扩展：极值在 π/2 + 2kπ（+1）与 −π/2 + 2kπ（−1）。"""
    from olam.kernel.frozen_tables import HALF_PI_Q, TABLE_BITS, TWO_PI_Q
    from olam.kernel.tables import DECLARED_EPSILON, sin_q

    if box.bits != TABLE_BITS:
        raise ValueError(f"预计算表包络只支持 Q({TABLE_BITS})")
    scale = 1 << TABLE_BITS
    lo = min(sin_q(box.lo), sin_q(box.hi))
    hi = max(sin_q(box.lo), sin_q(box.hi))
    if _contains_offset_multiple(box.lo, box.hi, HALF_PI_Q, TWO_PI_Q):
        hi = max(hi, scale)                      # sin = +1
    if _contains_offset_multiple(box.lo, box.hi, -HALF_PI_Q, TWO_PI_Q):
        lo = min(lo, -scale)                     # sin = −1
    eps = _ceil_q(DECLARED_EPSILON, TABLE_BITS)
    return Interval(lo - eps, hi + eps, TABLE_BITS)


def tanh_enclosure(box: Interval) -> Interval:
    """tanh 单调不减（含域外饱和）：端点求值 + 声明误差膨胀。"""
    from olam.kernel.frozen_tables import TABLE_BITS
    from olam.kernel.tables import TANH_MAX_ERROR, tanh_q

    if box.bits != TABLE_BITS:
        raise ValueError(f"预计算表包络只支持 Q({TABLE_BITS})")
    lo = tanh_q(box.lo)
    hi = tanh_q(box.hi)
    eps = _ceil_q(TANH_MAX_ERROR, TABLE_BITS)
    return Interval(min(lo, hi) - eps, max(lo, hi) + eps, TABLE_BITS)


def acos_enclosure(box: Interval) -> Interval:
    """acos 单调递减，定义域端点钳制（与生产语义一致）。"""
    from olam.kernel.frozen_tables import TABLE_BITS
    from olam.kernel.tables import ACOS_MAX_ERROR, acos_q

    if box.bits != TABLE_BITS:
        raise ValueError(f"预计算表包络只支持 Q({TABLE_BITS})")
    scale = 1 << TABLE_BITS
    low = acos_q(box.hi)
    high = acos_q(box.lo)
    if box.hi < -scale:
        low = high = acos_q(-scale)              # 全域低于 −1 → π
    if box.lo > scale:
        low = high = 0                           # 全域高于 1 → 0
    eps = _ceil_q(ACOS_MAX_ERROR, TABLE_BITS)
    return Interval(low - eps, high + eps, TABLE_BITS)


def tan_enclosure(box: Interval) -> Interval:
    """tan = sin/cos：区间相除；分母含 0（跨越 π/2 + kπ）即拒绝。"""
    from olam.kernel.frozen_tables import TABLE_BITS
    from olam.kernel.tables import TAN_MAX_ERROR

    if box.bits != TABLE_BITS:
        raise ValueError(f"预计算表包络只支持 Q({TABLE_BITS})")
    result = sin_enclosure(box).div(cos_enclosure(box))
    eps = _ceil_q(TAN_MAX_ERROR, TABLE_BITS)
    return Interval(result.lo - eps, result.hi + eps, TABLE_BITS)


def degrees_enclosure(box: Interval) -> Interval:
    """degrees：乘点包络 180/π（定点乘的区间扩展，向外取整）。"""
    from olam.kernel.frozen_tables import TABLE_BITS
    from olam.kernel.tables import DEG_FACTOR_Q

    if box.bits != TABLE_BITS:
        raise ValueError(f"预计算表包络只支持 Q({TABLE_BITS})")
    return box.mul(Interval.point(DEG_FACTOR_Q, TABLE_BITS))
