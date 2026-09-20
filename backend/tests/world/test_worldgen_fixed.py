"""世界生成定点实现测试（P2-3c-3）— 与 float 参考并行对拍（#52 方法论）。

覆盖 5 个机制：#53 P3 剩余的 3 个 C 单源标量 + climate_zone / biome 决策树。
判据：定点实现与 float 参考在采样域内落在**声明的内核误差**内
（标量）或逐点相等（离散决策树，采样点避开阈值 2⁻³¹ 邻域）。
"""

from __future__ import annotations

import random

import pytest

from ascend.world.modules.worldgen import gen_fixed
from ascend.space.biome import BiomeType, biome_from_attrs
from ascend.space.hydrology import (
    apply_lapse_rate_c,
    classify_climate_c,
    rainfall_from_noise_c,
    sea_level_temperature_c,
)

_RNG = random.Random(20260918)


def _samples(n: int = 400) -> list[float]:
    """[-1, 1] 均匀采样（噪声域）。"""
    return [_RNG.uniform(-1.0, 1.0) for _ in range(n)]


class TestScalarFixedPoint:
    """3 个 C 单源标量：定点 vs float 参考在声明界内。"""

    def test_sea_level_temperature(self):
        worst = 0.0
        for noise in _samples() + [-1.0, 0.0, 1.0]:
            delta = abs(
                gen_fixed.sea_level_temperature(noise)
                - sea_level_temperature_c(noise)
            )
            worst = max(worst, delta)
        assert worst <= 1e-7, f"最坏差 {worst}"
        # 端点 clamp 与参考一致
        assert gen_fixed.sea_level_temperature(-1.0) == pytest.approx(-15.0)
        assert gen_fixed.sea_level_temperature(1.0) == pytest.approx(35.0)

    def test_rainfall(self):
        worst = 0.0
        for noise in _samples() + [-1.0, 0.0, 1.0]:
            delta = abs(
                gen_fixed.rainfall_from_noise(noise)
                - rainfall_from_noise_c(noise)
            )
            worst = max(worst, delta)
        assert worst <= 5e-6, f"最坏差 {worst}"
        assert gen_fixed.rainfall_from_noise(-1.0) == pytest.approx(50.0)
        assert gen_fixed.rainfall_from_noise(1.0) == pytest.approx(3500.0)

    def test_lapse_rate(self):
        worst = 0.0
        for _ in range(400):
            sea = _RNG.uniform(-20.0, 38.0)
            altitude = _RNG.uniform(-500.0, 5000.0)
            delta = abs(
                gen_fixed.apply_lapse_rate(sea, altitude)
                - apply_lapse_rate_c(sea, altitude)
            )
            worst = max(worst, delta)
        assert worst <= 1e-6, f"最坏差 {worst}"
        # 海域恒等；陆地直减
        assert gen_fixed.apply_lapse_rate(20.0, -100.0) == \
            pytest.approx(20.0)
        assert gen_fixed.apply_lapse_rate(20.0, 1000.0) == \
            pytest.approx(11.0)
        # 高温/高海拔 clamp
        assert gen_fixed.apply_lapse_rate(20.0, 5000.0) == \
            pytest.approx(-20.0)


class TestDecisionTrees:
    """climate_zone / biome 决策树：离散输出逐点相等（采样避开阈值邻域）。"""

    def test_classify_climate_matches_reference(self):
        mismatches = 0
        total = 0
        for _ in range(600):
            temp = _RNG.uniform(-30.0, 40.0)
            rain = _RNG.uniform(0.0, 4000.0)
            altitude = _RNG.uniform(-500.0, 5000.0)
            total += 1
            if gen_fixed.classify_climate(temp, rain, altitude) != \
                    int(classify_climate_c(temp, rain, altitude)):
                mismatches += 1
        assert mismatches == 0, f"{mismatches}/{total} 输出不一致"

    def test_biome_matches_reference(self):
        """海洋三档 + 陆地档内主隶属：静态值域路径逐点一致。"""
        mismatches = 0
        for _ in range(400):
            temp = _RNG.uniform(-30.0, 40.0)
            rain = _RNG.uniform(0.0, 4000.0)
            altitude = _RNG.uniform(-500.0, 5000.0)
            sea = _RNG.uniform(-20.0, 38.0)
            moisture = _RNG.uniform(-1.0, 1.0)
            fixed = gen_fixed.biome_from_attrs(
                temp, rain, altitude, sea, moisture,
            )
            legacy = int(biome_from_attrs(
                temp, rain, altitude, sea, moisture,
            ))
            if fixed != legacy:
                mismatches += 1
        assert mismatches == 0, f"{mismatches}/400 输出不一致"

    def test_ocean_tiers(self):
        """海洋按海面温度三档（altitude < 0）。"""
        assert gen_fixed.biome_from_attrs(10.0, 800.0, -100.0, 25.0) == \
            int(BiomeType.WARM_OCEAN)
        assert gen_fixed.biome_from_attrs(10.0, 800.0, -100.0, 10.0) == \
            int(BiomeType.TEMPERATE_OCEAN)
        assert gen_fixed.biome_from_attrs(10.0, 800.0, -100.0, -5.0) == \
            int(BiomeType.COLD_OCEAN)

    def test_boundary_clamps_discrete(self):
        """极端输入不越出既有分类（alpine/polar/desert 优先序保持）。"""
        assert gen_fixed.classify_climate(25.0, 1000.0, 2500.0) == 7
        assert gen_fixed.classify_climate(-10.0, 50.0, 0.0) == 6
        assert gen_fixed.classify_climate(25.0, 100.0, 0.0) == 2
