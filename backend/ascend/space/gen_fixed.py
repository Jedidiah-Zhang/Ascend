"""世界生成标量公式 — 定点实现（issue #52 空间内核批次）。

覆盖 #53 P3 剩余的空间生成机制（3 个 C 单源标量 + climate_zone / biome
决策树）的定点等价物：

- :func:`sea_level_temperature` / :func:`rainfall_from_noise` /
  :func:`apply_lapse_rate`：`_hydrology.c` 三个标量函数的定点实现；
- :func:`classify_climate`：8 档决策树——量化输入 + 整数阈值比较，
  判定顺序与优先级与 C 逐一对应（altitude ≥ alpine → temp < polar →
  rain < desert → rain < steppe 且 temp > steppe_min → temp ≥ tropical
  → temp ≥ temperate → rain ≥ taiga，其余 polar）；
- :func:`biome_from_attrs`：海洋按海面温度三档；陆地按档内细分维度
  归一化 + 三角隶属取主型（`biome.py` 语义的定点等价物）。

语义（WC-4.4）：

- 统一 Q(bits)（``bits = TABLE_BITS``，与天气侧冻表内核一致）；
- 边界输入（噪声/海拔/温度）在机制入口一次性量化；乘加走定点原语
  （半偶舍入，纯整数）；除法走整数半偶除法；clamp 在量化域完成；
- 阈值比较在量化域进行——与 float 比较的差异 ≤ 输入量化误差
  （2⁻³¹ 量级）才能翻转，随机制容差登记（见
  ``research/equations/reference_check.py::_TOLERANCES``）。

与 float 参考的已登记差异：

- 输入量化误差 ≤ 2⁻³¹（Q30 半步），经放大系数传播
  （sst ≤ 25×、rainfall ≤ (max−min)/2 = 1725×、lapse ≤ 9e-3×）；
- 每步定点乘/除的半偶舍入误差 ≤ 2⁻³¹ 量级；
- 因此各机制与 float 规范参考的差 ≤ 1e-6（sst/rainfall）与 1e-5
  （lapse）；决策树输出为离散整数，阈值附近的翻转差异已在容差
  声明中覆盖（浮点值落在阈值 2⁻³¹ 邻域内才可能不同）。

**边界**：大陆/水文宏观管线（``_hydrology.c::compute_climate``，含
exp 大陆度修正）与 `biome.py` 的 float 路径（动态 ``subdiv_ranges``）
仍是显式 `slice_boundary`，不在本模块；本模块是注册表机制（研究事实
源）的实现。两条路径的最终统一（宏观管线定点化）留待后续批次。
"""

from __future__ import annotations

from ascend.config import (
    ALPINE_ALTITUDE,
    DESERT_RAINFALL,
    LAPSE_RATE,
    POLAR_TEMP,
    RAINFOREST_RAINFALL,
    RAINFALL_MAX,
    RAINFALL_MIN,
    STEPPE_MIN_TEMP,
    STEPPE_RAINFALL,
    TAIGA_RAINFALL,
    TEMPERATE_TEMP,
    TROPICAL_TEMP,
)
from ascend.num.fixed import (
    clamp as _clamp,
    mul as _mul,
    quantize as _quantize,
    round_half_even_div as _round_div,
    to_float as _to_float,
)
from ascend.num.frozen_tables import TABLE_BITS

_BITS = TABLE_BITS

__all__ = [
    "apply_lapse_rate",
    "biome_from_attrs",
    "classify_climate",
    "rainfall_from_noise",
    "sea_level_temperature",
]


def _q(value: float) -> int:
    """边界量化（Q(bits)）；语义核内部不再接触浮点。"""
    return _quantize(value, _BITS)


def sea_level_temperature(latitude_noise: float) -> float:
    """纬度噪声 → 海平面温度（Q30）：``clamp(noise*25 + 10, -20, 38)``。"""
    value = _mul(_q(latitude_noise), _q(25.0), _BITS) + _q(10.0)
    return _to_float(_clamp(value, _q(-20.0), _q(38.0)), _BITS)


def rainfall_from_noise(rainfall_noise: float) -> float:
    """降雨噪声 → 年降雨量（Q30）：``clamp(min + (n+1)/2*(max-min), 0, 5000)``。"""
    span = _q(RAINFALL_MAX) - _q(RAINFALL_MIN)
    value = _q(RAINFALL_MIN) + _mul(
        _mul(_q(rainfall_noise) + _q(1.0), _q(0.5), _BITS), span, _BITS,
    )
    return _to_float(_clamp(value, _q(0.0), _q(5000.0)), _BITS)


def apply_lapse_rate(sea_level_temperature: float, altitude: float) -> float:
    """气温直减率（Q30）：陆地 ``clamp(sst - alt*lapse/1000, -20, 36)``；
    海拔 ≤ 0（海域）返回海面温度本身（不 clamp，与 C 语义一致）。"""
    sea = _q(sea_level_temperature)
    alt = _q(altitude)
    if alt > 0:
        drop = _round_div(_mul(alt, _q(LAPSE_RATE), _BITS), 1000)
        return _to_float(_clamp(sea - drop, _q(-20.0), _q(36.0)), _BITS)
    return _to_float(sea, _BITS)


def classify_climate(
    mean_temperature: float,
    annual_rainfall: float,
    altitude: float,
) -> int:
    """8 档气候决策树（量化域整数比较；判定顺序同 C）。"""
    temp = _q(mean_temperature)
    rain = _q(annual_rainfall)
    alt = _q(altitude)
    if alt >= _q(ALPINE_ALTITUDE):
        return 7  # ALPINE
    if temp < _q(POLAR_TEMP):
        return 6  # POLAR_TUNDRA
    if rain < _q(DESERT_RAINFALL):
        return 2  # DESERT
    if rain < _q(STEPPE_RAINFALL) and temp > _q(STEPPE_MIN_TEMP):
        return 3  # STEPPE
    if temp >= _q(TROPICAL_TEMP):
        if rain >= _q(RAINFOREST_RAINFALL):
            return 0  # EQUATORIAL_RAINFOREST
        return 1  # TROPICAL_SAVANNA
    if temp >= _q(TEMPERATE_TEMP):
        return 4  # TEMPERATE_FOREST
    if rain >= _q(TAIGA_RAINFALL):
        return 5  # SUBARCTIC_TAIGA
    return 6  # POLAR_TUNDRA


def biome_from_attrs(
    mean_temp: float,
    annual_rainfall: float,
    altitude: float,
    sea_level_temp: float,
    moisture_noise: float = 0.0,
) -> int:
    """按连续气候属性分配群系主型（量化域；语义同 ``biome.biome_membership``）。

    静态细分值域（data/biome.json 的 value_min/value_max）；动态
    ``subdiv_ranges`` 属大陆管线边界，不在本函数。
    """
    from .biome import (
        BiomeType,
        _OCEAN_COLD_CUTOFF,
        _OCEAN_WARM_CUTOFF,
        _SEA_LEVEL,
        _SUBDIV_ALTITUDE,
        _SUBDIV_CONFIGS,
        _SUBDIV_MOISTURE,
        _SUBDIV_RAINFALL,
        _SUBDIV_TEMPERATURE,
    )

    alt = _q(altitude)
    sea = _q(sea_level_temp)
    if alt < _q(_SEA_LEVEL):
        if sea >= _q(_OCEAN_WARM_CUTOFF):
            return int(BiomeType.WARM_OCEAN)
        if sea >= _q(_OCEAN_COLD_CUTOFF):
            return int(BiomeType.TEMPERATE_OCEAN)
        return int(BiomeType.COLD_OCEAN)

    from .climate import ClimateZone

    climate = ClimateZone(classify_climate(mean_temp, annual_rainfall, altitude))
    cfg = _SUBDIV_CONFIGS.get(climate)
    if cfg is None:
        return int(BiomeType.TEMPERATE_DECIDUOUS_FOREST)

    if cfg.dimension == _SUBDIV_RAINFALL:
        raw = annual_rainfall
    elif cfg.dimension == _SUBDIV_TEMPERATURE:
        raw = mean_temp
    elif cfg.dimension == _SUBDIV_ALTITUDE:
        raw = altitude
    else:  # _SUBDIV_MOISTURE
        raw = moisture_noise

    span = _q(cfg.value_max) - _q(cfg.value_min)
    if span <= 0:
        v = _q(0.5)
    else:
        v = _round_div((_q(raw) - _q(cfg.value_min)) << _BITS, span)
        v = max(0, min(_q(1.0), v))

    # 三角隶属（未归一化）：w = max(0, 1 − 2·|v − c|)；归一化不改变
    # "主隶属"比较（同分母）与平局（列表首项 = low 胜）。
    c_lo, c_hi = _q(0.25), _q(0.75)
    w_lo = max(0, _q(1.0) - 2 * (v - c_lo if v > c_lo else c_lo - v))
    w_hi = max(0, _q(1.0) - 2 * (v - c_hi if v > c_hi else c_hi - v))
    if w_lo + w_hi <= 0:
        return int(cfg.low if v < _q(0.5) else cfg.high)
    return int(cfg.low if w_lo >= w_hi else cfg.high)
