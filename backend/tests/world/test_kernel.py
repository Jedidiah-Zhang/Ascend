"""内核测试 — 定点原语、地址随机与内容摘要。"""

from __future__ import annotations

import pytest

from ascend.world.kernel import (
    Address,
    address_seed,
    address_value,
    canonical_bytes,
    clamp,
    digest_object,
    div,
    draw_fixed,
    fit,
    mul,
    quantize,
    round_half_even_div,
    to_float,
)


class TestFixedPrimitives:
    def test_round_half_even(self):
        assert round_half_even_div(1, 2) == 0
        assert round_half_even_div(3, 2) == 2
        assert round_half_even_div(-1, 2) == 0
        assert round_half_even_div(-3, 2) == -2
        with pytest.raises(ValueError):
            round_half_even_div(1, 0)

    def test_mul_div_round_trip(self):
        q = lambda value: quantize(value, 10)  # noqa: E731
        product = mul(q(1.5), q(2.0), 10)
        assert to_float(product, 10) == pytest.approx(3.0)
        quotient = div(q(3.0), q(2.0), 10)
        assert to_float(quotient, 10) == pytest.approx(1.5)

    def test_clamp_and_fit(self):
        assert clamp(5, 0, 3) == 3
        assert clamp(-5, 0, 3) == 0
        with pytest.raises(ValueError):
            clamp(5, 3, 0)
        assert fit(127, 8) == 127
        with pytest.raises(OverflowError):
            fit(128, 8)


class TestAddressRandom:
    def test_pure_function(self):
        address = Address("toy", "rt", instance=(1, 2), time=3)
        assert address_seed(0, address) == address_seed(0, address)
        assert address_seed(0, address) != address_seed(1, address)

    def test_order_independent(self):
        first = Address("toy", "rt", time=3)
        second = Address("toy", "rt", time=4)
        before = address_seed(0, second)
        address_seed(0, first)  # 抽取其他地址不影响
        assert address_seed(0, second) == before

    def test_draw_index_changes_value(self):
        first = Address("toy", "rt", draw_index=0)
        second = Address("toy", "rt", draw_index=1)
        assert address_seed(0, first) != address_seed(0, second)

    def test_range_mapping(self):
        address = Address("toy", "rt")
        assert address_value(0, address, minimum=5, maximum=5) == 5
        for _ in range(1):
            assert 0 <= address_value(0, address, minimum=0, maximum=9) <= 9
        with pytest.raises(ValueError):
            address_value(0, address, minimum=5, maximum=4)

    def test_draw_fixed_range(self):
        address = Address("toy", "rt")
        for bits in (1, 8, 30):
            assert 0 <= draw_fixed(0, address, bits) < (1 << bits)

    def test_identifier_validation(self):
        with pytest.raises(ValueError):
            Address("Toy", "rt")
        with pytest.raises(ValueError):
            Address("toy", "rt", time=-1)
        with pytest.raises(ValueError):
            Address("toy", "rt", instance=(1.5,))  # type: ignore[arg-type]


class TestDigest:
    def test_canonical_key_order(self):
        assert canonical_bytes({"b": 1, "a": 2}) == canonical_bytes(
            {"a": 2, "b": 1}
        )

    def test_digest_changes_with_content(self):
        assert digest_object({"a": 1}) != digest_object({"a": 2})
