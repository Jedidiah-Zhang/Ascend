"""包络代数测试（issue #53 P4 地基）：包含保持 + 集合律 + fail-closed。"""

from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT / "research" / "equations"))

import enclosure  # noqa: E402

from ascend.world.kernel.fixed import div as point_div  # noqa: E402
from ascend.world.kernel.fixed import mul as point_mul  # noqa: E402

BITS = 20


def _rand_interval(rng: random.Random, span: int = 2000) -> enclosure.Interval:
    lo = rng.randint(-span, span)
    hi = lo + rng.randint(0, span)
    return enclosure.Interval(lo, hi, BITS)


class TestIntervalBasics:
    def test_point_and_properties(self):
        point = enclosure.Interval.point(7, BITS)
        assert point.is_singleton() and point.width() == 0
        assert point.contains(7) and not point.contains(8)

    def test_union_hull_and_intersect(self):
        a = enclosure.Interval(0, 5, BITS)
        b = enclosure.Interval(3, 9, BITS)
        assert a.union(b) == enclosure.Interval(0, 9, BITS)
        assert a.intersect(b) == enclosure.Interval(3, 5, BITS)
        assert a.intersect(enclosure.Interval(6, 8, BITS)) is None

    def test_hull_and_validation(self):
        assert enclosure.Interval.hull(
            [enclosure.Interval(1, 2, BITS), enclosure.Interval(0, 4, BITS)],
        ) == enclosure.Interval(0, 4, BITS)
        with pytest.raises(ValueError, match="至少一个"):
            enclosure.Interval.hull([])
        with pytest.raises(ValueError, match="同一 Q 精度"):
            enclosure.Interval.hull(
                [enclosure.Interval(1, 2, BITS), enclosure.Interval(1, 2, 8)],
            )
        with pytest.raises(ValueError, match="下界大于上界"):
            enclosure.Interval(2, 1, BITS)
        with pytest.raises(ValueError, match="bits"):
            enclosure.Interval(0, 1, -1)


class TestContainment:
    """包含保持：点运算结果必须落在区间扩展内（D2 核心义务）。"""

    def test_mul_contains_all_point_results(self):
        rng = random.Random(20260917)
        for _ in range(3000):
            a, b = _rand_interval(rng), _rand_interval(rng)
            product = a.mul(b)
            for _ in range(4):
                x = rng.randint(a.lo, a.hi)
                y = rng.randint(b.lo, b.hi)
                assert product.contains(point_mul(x, y, BITS)), (
                    a, b, x, y, point_mul(x, y, BITS), product,
                )

    def test_div_contains_all_point_results(self):
        rng = random.Random(7)
        checked = 0
        while checked < 2000:
            a = _rand_interval(rng)
            b = _rand_interval(rng)
            if b.contains(0):
                continue
            quotient = a.div(b)
            for _ in range(4):
                x = rng.randint(a.lo, a.hi)
                y = rng.randint(b.lo, b.hi)
                if y == 0:
                    continue
                value = point_div(x, y, BITS)
                assert quotient.contains(value), (
                    a, b, x, y, value, quotient,
                )
            checked += 1

    def test_add_sub_neg_clamp(self):
        a = enclosure.Interval(-5, 4, BITS)
        b = enclosure.Interval(1, 3, BITS)
        assert a.add(b) == enclosure.Interval(-4, 7, BITS)
        assert a.sub(b) == enclosure.Interval(-8, 3, BITS)
        assert a.neg() == enclosure.Interval(-4, 5, BITS)
        assert a.clamp(0, 2) == enclosure.Interval(0, 2, BITS)
        assert enclosure.Interval(-5, -1, BITS).clamp(0, 2) == (
            enclosure.Interval.point(0, BITS)
        )
        assert enclosure.Interval(3, 9, BITS).clamp(0, 2) == (
            enclosure.Interval.point(2, BITS)
        )

    def test_scale_by_ratio(self):
        interval = enclosure.Interval(-6, 3, BITS)
        result = interval.scale_by_ratio(1, 3)
        assert result == enclosure.Interval(-2, 1, BITS)


class TestFailClosed:
    def test_div_by_zero_containing_interval(self):
        with pytest.raises(ZeroDivisionError, match="含 0"):
            enclosure.Interval(1, 2, BITS).div(
                enclosure.Interval(-1, 1, BITS),
            )

    def test_bits_mismatch(self):
        with pytest.raises(ValueError, match="Q 精度"):
            enclosure.Interval(1, 2, BITS).add(
                enclosure.Interval(1, 2, BITS - 1),
            )


class TestDiscreteSet:
    def test_laws(self):
        a = enclosure.DiscreteSet.of([1, 2])
        b = enclosure.DiscreteSet.of([2, 3])
        assert a.union(b).values == frozenset({1, 2, 3})
        assert a.intersect(b).values == frozenset({2})
        assert a.contains(1) and not a.contains(3)
        assert a.width() == 1.0
        assert enclosure.DiscreteSet.of(["rain"]).width() == 0.0
        assert enclosure.DiscreteSet.of([]).is_empty()


class TestTableEnclosures:
    """冻表函数区间扩展：采样包含保持 + 极值/定义域语义。"""

    def test_cos_and_sin_inclusion(self):
        rng = random.Random(53)
        for _ in range(1500):
            a = rng.randint(-3 * enclosure.Interval.point(0, BITS).bits ** 0 * 10, 0)
            del a
            lo = rng.randint(-12 * (1 << 20), 12 * (1 << 20))
            hi = lo + rng.randint(0, 6 * (1 << 20))
            box = enclosure.Interval(lo, hi, 30)
            cbox = enclosure.cos_enclosure(box)
            sbox = enclosure.sin_enclosure(box)
            for _ in range(4):
                x = rng.randint(lo, hi)
                from ascend.world.kernel.tables import cos_q as point_cos
                from ascend.world.kernel.tables import sin_q as point_sin
                assert cbox.contains(point_cos(x)), (box, x, cbox)
                assert sbox.contains(point_sin(x)), (box, x, sbox)

    def test_cos_extrema_detected(self):
        scale = 1 << 30
        from ascend.world.kernel.frozen_tables import PI_Q
        box = enclosure.Interval(-PI_Q // 2, PI_Q // 2, 30)
        cbox = enclosure.cos_enclosure(box)
        assert cbox.hi >= scale, "区间跨 0 应包含 cos=+1"
        assert cbox.lo <= 0

    def test_tanh_and_acos_and_degrees_inclusion(self):
        rng = random.Random(61)
        scale = 1 << 30
        for _ in range(800):
            lo = rng.randint(-3 * scale, 3 * scale)
            hi = min(lo + rng.randint(0, 2 * scale), 3 * scale)
            box = enclosure.Interval(lo, hi, 30)
            tbox = enclosure.tanh_enclosure(box)
            dbox = enclosure.degrees_enclosure(box)
            for _ in range(4):
                x = rng.randint(lo, hi)
                from ascend.world.kernel.tables import degrees_q as point_deg
                from ascend.world.kernel.tables import tanh_q as point_tanh
                assert tbox.contains(point_tanh(x))
                assert dbox.contains(point_deg(x))
        # acos 定义域钳制：域外区域像为点 0 / π，再膨胀声明误差
        abox = enclosure.acos_enclosure(enclosure.Interval(2 * scale, 3 * scale, 30))
        assert abox.contains(0) and abox.width() <= 2 * (1 << 30) * 2e-3 + 2
        abox2 = enclosure.acos_enclosure(
            enclosure.Interval(-3 * scale, -2 * scale, 30))
        from ascend.world.kernel.frozen_tables import PI_Q
        assert abox2.contains(PI_Q)

    def test_tan_pole_rejected(self):
        scale = 1 << 30
        from ascend.world.kernel.frozen_tables import HALF_PI_Q
        with pytest.raises(ZeroDivisionError):
            enclosure.tan_enclosure(
                enclosure.Interval(HALF_PI_Q - scale // 10,
                                   HALF_PI_Q + scale // 10, 30),
            )
