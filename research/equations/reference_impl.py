"""非表达式机制声明的独立参考实现。

方程字符串可解析的机制由 ``expression.py`` 直接求值；本模块覆盖其余
机制：C 单源标量（按 ``_hydrology.c`` 语义独立复刻）、气候/群系模板
数据查表、群系主隶属。实现全部是研究侧独立代码，**不 import 生产求值
函数**；与生产实现的一致性由 ``reference_check.py`` 的随机/见证点对拍
锁定（verify V4）。

模板数据（data/climate.json、data/biome.json）是声明数据，参考与生产
同源读取；算法（决策树/隶属函数）为独立复刻。
"""

from __future__ import annotations

import json
from pathlib import Path

from ascend.config import (
    ALPINE_ALTITUDE,
    DESERT_RAINFALL,
    LAPSE_RATE,
    OCEAN_COLD_CUTOFF,
    OCEAN_WARM_CUTOFF,
    POLAR_TEMP,
    RAINFOREST_RAINFALL,
    RAINFALL_MAX,
    RAINFALL_MIN,
    SEA_LEVEL_ELEV,
    STEPPE_MIN_TEMP,
    STEPPE_RAINFALL,
    TAIGA_RAINFALL,
    TEMPERATE_TEMP,
    TROPICAL_TEMP,
)
from ascend.space.biome import BiomeType
from ascend.space.climate import ClimateZone

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parents[1]


def _load(name: str) -> dict:
    return json.loads(
        (_ROOT / "data" / name).read_text(encoding="utf-8")
    )


def _local(ns_id: str) -> str:
    return ns_id.split(":", 1)[-1].upper()


# ── 气候模板（data/climate.json，独立读取）─────────────────────

def _climate_templates() -> dict[int, dict]:
    doc = _load("climate.json")
    out: dict[int, dict] = {}
    for ns_id, raw in doc["climate"].items():
        zone = ClimateZone[_local(ns_id)]
        out[int(zone.value)] = {
            "humidity": tuple(float(x) for x in raw["humidity_range"]),
            "wind": tuple(float(x) for x in raw["wind_speed_range"]),
            "mean_precip_intensity": float(raw["mean_precip_intensity"]),
            "humidity_sharpness": float(raw["humidity_sharpness"]),
        }
    return out


_TEMPLATES = _climate_templates()


# ── 群系细分配置（data/biome.json，独立读取）───────────────────

def _subdiv_configs() -> dict[int, dict]:
    doc = _load("biome.json")
    out: dict[int, dict] = {}
    for ns_id, raw in doc["subdiv"].items():
        zone = ClimateZone[_local(ns_id)]
        out[int(zone.value)] = {
            "dimension": str(raw["dimension"]),
            "low": int(BiomeType[_local(str(raw["low"]))].value),
            "high": int(BiomeType[_local(str(raw["high"]))].value),
            "value_min": float(raw["value_min"]),
            "value_max": float(raw["value_max"]),
        }
    return out


_SUBDIV = _subdiv_configs()


# ── C 单源标量的独立复刻（语义源：space/_hydrology.c）───────────

def sea_level_temperature(latitude_noise: float) -> float:
    return max(-20.0, min(38.0, latitude_noise * 25.0 + 10.0))


def rainfall_from_noise(rain_noise: float) -> float:
    value = RAINFALL_MIN + (rain_noise + 1.0) * 0.5 * (
        RAINFALL_MAX - RAINFALL_MIN
    )
    return max(0.0, min(5000.0, value))


def apply_lapse_rate(sea_level_temp: float, altitude: float) -> float:
    if altitude <= 0.0:
        return sea_level_temp
    return max(-20.0, min(36.0, sea_level_temp - altitude * LAPSE_RATE / 1000.0))


def classify_climate(temp: float, rainfall: float, altitude: float) -> int:
    """8 档决策树（阈值源：config，经 C 注入同值）。"""
    if altitude >= ALPINE_ALTITUDE:
        return int(ClimateZone.ALPINE.value)
    if temp < POLAR_TEMP:
        return int(ClimateZone.POLAR_TUNDRA.value)
    if rainfall < DESERT_RAINFALL:
        return int(ClimateZone.DESERT.value)
    if rainfall < STEPPE_RAINFALL and temp > STEPPE_MIN_TEMP:
        return int(ClimateZone.STEPPE.value)
    if temp >= TROPICAL_TEMP:
        if rainfall >= RAINFOREST_RAINFALL:
            return int(ClimateZone.EQUATORIAL_RAINFOREST.value)
        return int(ClimateZone.TROPICAL_SAVANNA.value)
    if temp >= TEMPERATE_TEMP:
        return int(ClimateZone.TEMPERATE_FOREST.value)
    if rainfall >= TAIGA_RAINFALL:
        return int(ClimateZone.SUBARCTIC_TAIGA.value)
    return int(ClimateZone.POLAR_TUNDRA.value)


# ── 模板查表与群系（独立算法）──────────────────────────────────

def baseline_humidity(climate_zone: int, humidity_noise: float) -> float:
    lo, hi = _TEMPLATES[int(climate_zone)]["humidity"]
    return max(0.0, min(100.0, lo + (humidity_noise + 1.0) * 0.5 * (hi - lo)))


def baseline_wind_speed(climate_zone: int, wind_noise: float) -> float:
    lo, hi = _TEMPLATES[int(climate_zone)]["wind"]
    return max(0.0, min(50.0, lo + (wind_noise + 1.0) * 0.5 * (hi - lo)))


def mean_precip_intensity(climate_zone: int) -> float:
    return _TEMPLATES[int(climate_zone)]["mean_precip_intensity"]


def humidity_sharpness(climate_zone: int) -> float:
    return _TEMPLATES[int(climate_zone)]["humidity_sharpness"]


def biome_membership(
    mean_temp: float,
    annual_rainfall: float,
    altitude: float,
    sea_level_temp: float,
    moisture_noise: float,
) -> list[tuple[int, float]]:
    """群系隶属（独立复刻；静态细分值域，与生产切片口径一致）。"""
    if altitude < SEA_LEVEL_ELEV:
        if sea_level_temp >= OCEAN_WARM_CUTOFF:
            return [(int(BiomeType.WARM_OCEAN.value), 1.0)]
        if sea_level_temp >= OCEAN_COLD_CUTOFF:
            return [(int(BiomeType.TEMPERATE_OCEAN.value), 1.0)]
        return [(int(BiomeType.COLD_OCEAN.value), 1.0)]

    climate = classify_climate(mean_temp, annual_rainfall, altitude)
    cfg = _SUBDIV.get(climate)
    if cfg is None:
        return [(int(BiomeType.TEMPERATE_DECIDUOUS_FOREST.value), 1.0)]

    dimension = cfg["dimension"]
    raw = {
        "rainfall": annual_rainfall,
        "temperature": mean_temp,
        "altitude": altitude,
        "moisture": moisture_noise,
    }[dimension]

    span = cfg["value_max"] - cfg["value_min"]
    if span <= 0:
        v = 0.5
    else:
        v = max(0.0, min(1.0, (raw - cfg["value_min"]) / span))

    w_lo = max(0.0, 1.0 - abs(v - 0.25) / 0.5)
    w_hi = max(0.0, 1.0 - abs(v - 0.75) / 0.5)
    total = w_lo + w_hi
    if total <= 0:
        return [(cfg["low"] if v < 0.5 else cfg["high"], 1.0)]
    return [
        (cfg["low"], w_lo / total),
        (cfg["high"], w_hi / total),
    ]


def biome(mean_temp: float, annual_rainfall: float, altitude: float,
          sea_level_temp: float, moisture_noise: float) -> int:
    """主隶属群系。"""
    membership = biome_membership(
        mean_temp, annual_rainfall, altitude, sea_level_temp, moisture_noise,
    )
    return max(membership, key=lambda item: item[1])[0]


# ── 机制级参考映射（env = 按 argument 名绑定的输入）─────────────

def _impl_sea_level(env) -> float:
    return sea_level_temperature(env["latitude_noise"])


def _impl_rainfall(env) -> float:
    return rainfall_from_noise(env["rainfall_noise"])


def _impl_lapse(env) -> float:
    return apply_lapse_rate(env["sea_level_temperature"], env["altitude"])


def _impl_climate_zone(env) -> int:
    return classify_climate(
        env["mean_temperature"], env["annual_rainfall"], env["altitude"],
    )


def _impl_biome(env) -> int:
    return biome(
        env["mean_temperature"], env["annual_rainfall"], env["altitude"],
        env["sea_level_temperature"], env["moisture_noise"],
    )


def _impl_baseline_humidity(env) -> float:
    return baseline_humidity(env["climate_zone"], env["humidity_noise"])


def _impl_baseline_wind(env) -> float:
    return baseline_wind_speed(env["climate_zone"], env["wind_noise"])


def _impl_mean_precip(env) -> float:
    return mean_precip_intensity(env["climate_zone"])


def _impl_sharpness(env) -> float:
    return humidity_sharpness(env["climate_zone"])


REFERENCE_IMPLS: dict[str, object] = {
    "world.gen.derive_sea_level_temperature.v1": _impl_sea_level,
    "world.gen.derive_annual_rainfall.v1": _impl_rainfall,
    "world.gen.derive_annual_mean_temperature.v1": _impl_lapse,
    "world.gen.classify_climate_zone.v1": _impl_climate_zone,
    "world.gen.classify_biome.v1": _impl_biome,
    "world.gen.derive_baseline_humidity.v1": _impl_baseline_humidity,
    "world.gen.derive_baseline_wind_speed.v1": _impl_baseline_wind,
    "world.gen.derive_mean_precip_intensity.v1": _impl_mean_precip,
    "world.gen.derive_humidity_sharpness.v1": _impl_sharpness,
}


__all__ = ["REFERENCE_IMPLS", "biome", "classify_climate"]
