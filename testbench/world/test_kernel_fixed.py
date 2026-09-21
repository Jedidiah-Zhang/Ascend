"""定点原语测试：精确性用 Fraction 对照，不依赖浮点结论。"""

from __future__ import annotations

import random
from fractions import Fraction

import pytest

from olam.kernel import fixed


def _half_even(value: Fraction) -> int:
    """独立参照：精确分数的半偶舍入。"""
    floor = value.numerator // value.denominator
    remainder = value - floor
    if remainder > Fraction(1, 2):
        return floor + 1
    if remainder < Fraction(1, 2):
        return floor
    return floor if floor % 2 == 0 else floor + 1


class TestRounding:
    @pytest.mark.parametrize("num, den, expected", [
        (1, 2, 0), (3, 2, 2), (5, 2, 2), (7, 2, 4),   # 平局取偶
        (-1, 2, 0), (-3, 2, -2), (-5, 2, -2),         # 负数同样半偶
        (10, 3, 3), (-10, 3, -3),
    ])
    def test_round_half_even_div(self, num, den, expected):
        assert fixed.round_half_even_div(num, den) == expected

    def test_round_half_even_div_rejects_bad_denominator(self):
        with pytest.raises(ValueError, match="分母"):
            fixed.round_half_even_div(1, 0)

    def test_rounding_matches_fraction_reference(self):
        rng = random.Random(20260917)
        for _ in range(2000):
            num = rng.randint(-10**6, 10**6)
            den = rng.randint(1, 1024)
            assert fixed.round_half_even_div(num, den) == _half_even(
                Fraction(num, den))


class TestOperations:
    @pytest.mark.parametrize("bits", [0, 4, 16, 32])
    def test_mul_matches_exact_fraction(self, bits):
        rng = random.Random(bits + 7)
        for _ in range(500):
            a = rng.randint(-10**5, 10**5)
            b = rng.randint(-10**5, 10**5)
            assert fixed.mul(a, b, bits) == _half_even(
                Fraction(a * b, 1 << bits))

    @pytest.mark.parametrize("bits", [0, 4, 16, 32])
    def test_div_matches_exact_fraction(self, bits):
        rng = random.Random(bits + 11)
        for _ in range(500):
            a = rng.randint(-10**5, 10**5)
            b = rng.randint(1, 10**5) * rng.choice((-1, 1))
            assert fixed.div(a, b, bits) == _half_even(
                Fraction(a << bits, b))

    def test_div_by_zero_rejected(self):
        with pytest.raises(ZeroDivisionError):
            fixed.div(1, 0, 8)

    @pytest.mark.parametrize("bits", [0, 8, 16])
    def test_sqrt_is_nearest_integer(self, bits):
        import math

        rng = random.Random(bits + 13)
        for _ in range(500):
            a = rng.randint(0, 10**6)
            scaled = a << bits
            floor = math.isqrt(scaled)
            # 独立重推最邻近判据：sqrt(v) ≥ k+1/2 ⟺ 4v ≥ 4k²+4k+1
            nearest = floor + (
                1 if 4 * scaled >= 4 * floor * floor + 4 * floor + 1 else 0
            )
            assert fixed.sqrt(a, bits) == nearest

    def test_sqrt_negative_rejected(self):
        with pytest.raises(ValueError, match="非负"):
            fixed.sqrt(-1, 8)


class TestBounds:
    def test_clamp_and_fit(self):
        assert fixed.clamp(5, 0, 3) == 3
        assert fixed.clamp(-1, 0, 3) == 0
        assert fixed.fit(127, 8) == 127
        assert fixed.fit(-128, 8) == -128
        assert fixed.fit(255, 8, signed=False) == 255

    def test_overflow_fail_closed(self):
        with pytest.raises(OverflowError, match="定点溢出"):
            fixed.fit(128, 8)
        with pytest.raises(OverflowError, match="定点溢出"):
            fixed.fit(-129, 8)
        with pytest.raises(OverflowError, match="定点溢出"):
            fixed.fit(256, 8, signed=False)
        with pytest.raises(ValueError, match="限幅区间倒置"):
            fixed.clamp(0, 5, 1)

    def test_quantize_boundary_roundtrip(self):
        raw = fixed.quantize(0.25, 8)
        assert raw == 64
        assert fixed.to_float(raw, 8) == 0.25
        # 0.1 的二进制表示不精确：量化值必须等于精确分数半偶舍入
        exact = Fraction(0.1) * 256
        assert fixed.quantize(0.1, 8) == _half_even(exact)

    def test_from_ratio(self):
        assert fixed.from_ratio(1, 3, 8) == _half_even(Fraction(256, 3))
        with pytest.raises(ValueError):
            fixed.from_ratio(1, 0, 8)

    def test_invalid_bits_rejected(self):
        for bad in (-1, True, 1.5):
            with pytest.raises(ValueError, match="精度"):
                fixed.mul(1, 1, bad)
