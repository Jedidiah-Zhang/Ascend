"""冻表测试（issue #53 P1）：端点精确、声明误差、对称与确定性。"""

from __future__ import annotations

import hashlib
import json
import math
import random

import pytest

from ascend.num import tables
from ascend.num.fixed import quantize, to_float
from ascend.num.frozen_tables import (
    COS_QUARTER_Q,
    HALF_PI_Q,
    PI_Q,
    SEGMENTS,
    TABLE_BITS,
    TABLE_DIGEST,
    TANH_MAX_Q,
    TANH_MIN_Q,
    TANH_SEGMENTS,
    TANH_TABLE_Q,
    TWO_PI_Q,
)

SCALE = 1 << TABLE_BITS


class TestTableData:
    def test_shape_and_digest(self):
        assert len(COS_QUARTER_Q) == SEGMENTS + 1
        assert COS_QUARTER_Q[0] == SCALE
        assert COS_QUARTER_Q[-1] == 0
        assert all(
            COS_QUARTER_Q[i] >= COS_QUARTER_Q[i + 1]
            for i in range(SEGMENTS)
        ), "四分之一周期内应单调不增"
        payload = json.dumps(
            {"bits": TABLE_BITS,
             "cos": {"segments": SEGMENTS, "pi_q": PI_Q,
                     "table": list(COS_QUARTER_Q)},
             "tanh": {"segments": TANH_SEGMENTS, "min_q": TANH_MIN_Q,
                      "max_q": TANH_MAX_Q, "table": list(TANH_TABLE_Q)}},
            sort_keys=True, separators=(",", ":"),
        ).encode()
        assert TABLE_DIGEST == (
            "sha256:" + hashlib.sha256(payload).hexdigest()
        ), "表内容与入库摘要不符（生成物被手改）"


class TestCosQuery:
    def test_endpoint_values(self):
        assert tables.cos_q(0) == SCALE
        assert abs(tables.cos_q(HALF_PI_Q)) <= 2
        assert tables.cos_q(PI_Q) == -SCALE
        assert abs(tables.cos_q(3 * HALF_PI_Q)) <= 2
        assert tables.cos_q(TWO_PI_Q - 1) == SCALE

    def test_declared_error_on_dense_samples(self):
        """密集采样：|cos_q − cos| ≤ 声明上界（含归约与插值）。"""
        rng = random.Random(20260917)
        worst = 0.0
        for _ in range(3000):
            theta = rng.uniform(-4 * math.pi, 4 * math.pi)
            raw = quantize(theta, TABLE_BITS)
            error = abs(
                to_float(tables.cos_q(raw), TABLE_BITS) - math.cos(theta)
            )
            worst = max(worst, error)
        assert worst <= tables.DECLARED_EPSILON, (
            f"实测最大误差 {worst:.3g} 超出声明 {tables.DECLARED_EPSILON}"
        )

    def test_negative_symmetry_and_determinism(self):
        rng = random.Random(53)
        for _ in range(200):
            raw = rng.randint(-TWO_PI_Q, TWO_PI_Q)
            assert tables.cos_q(-raw) == tables.cos_q(raw)
            assert tables.cos_q(raw) == tables.cos_q(raw)

    def test_periodicity(self):
        rng = random.Random(7)
        for _ in range(100):
            raw = rng.randint(-TWO_PI_Q, TWO_PI_Q)
            assert tables.cos_q(raw) == tables.cos_q(raw + TWO_PI_Q)

    def test_other_precision_rejected(self):
        with pytest.raises(ValueError, match="只支持"):
            tables.cos_q(1, bits=TABLE_BITS - 1)


class TestSinQuery:
    def test_endpoint_values(self):
        assert abs(tables.sin_q(0)) <= 2
        assert tables.sin_q(HALF_PI_Q) == SCALE
        assert abs(tables.sin_q(PI_Q)) <= 2
        assert tables.sin_q(3 * HALF_PI_Q) == -SCALE

    def test_odd_symmetry(self):
        rng = random.Random(11)
        for _ in range(200):
            raw = rng.randint(-TWO_PI_Q, TWO_PI_Q)
            assert tables.sin_q(-raw) == -tables.sin_q(raw)

    def test_declared_error_and_pythagorean(self):
        """采样：sin 误差 ≤ 声明界；sin²+cos² 在 2×声明界内为 1。"""
        rng = random.Random(29)
        worst = 0.0
        for _ in range(2000):
            theta = rng.uniform(-4 * math.pi, 4 * math.pi)
            raw = quantize(theta, TABLE_BITS)
            worst = max(worst, abs(
                to_float(tables.sin_q(raw), TABLE_BITS) - math.sin(theta)
            ))
            s = to_float(tables.sin_q(raw), TABLE_BITS)
            c = to_float(tables.cos_q(raw), TABLE_BITS)
            assert abs(s * s + c * c - 1.0) <= 4 * tables.DECLARED_EPSILON
        assert worst <= tables.DECLARED_EPSILON, worst


class TestTanhQuery:
    def test_endpoints_and_saturation(self):
        assert tables.tanh_q(0) == 0
        near_one = to_float(tables.tanh_q(TANH_MAX_Q), TABLE_BITS)
        assert abs(near_one - 1.0) <= 3e-7
        assert tables.tanh_q(TANH_MAX_Q) == tables.tanh_q(10 * SCALE)
        assert tables.tanh_q(TANH_MIN_Q) == tables.tanh_q(-10 * SCALE)

    def test_odd_symmetry(self):
        rng = random.Random(41)
        for _ in range(200):
            raw = rng.randint(TANH_MIN_Q, TANH_MAX_Q)
            assert tables.tanh_q(-raw) == -tables.tanh_q(raw)

    def test_declared_error_on_dense_samples(self):
        rng = random.Random(43)
        worst = 0.0
        for _ in range(3000):
            x = rng.uniform(-12.0, 12.0)
            raw = quantize(x, TABLE_BITS)
            error = abs(to_float(tables.tanh_q(raw), TABLE_BITS) - math.tanh(x))
            worst = max(worst, error)
        assert worst <= tables.TANH_MAX_ERROR, worst

    def test_other_precision_rejected(self):
        with pytest.raises(ValueError, match="只支持"):
            tables.tanh_q(1, bits=TABLE_BITS - 1)
