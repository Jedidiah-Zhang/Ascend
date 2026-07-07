"""昼夜曲线 — 日内余弦波动 + 日出日落计算。

纯函数，无状态，线程安全。振幅由调用方提供（海洋区小、沙漠大）。

温度偏移用余弦曲线：14:00 取 +amplitude（峰值），02:00 取 -amplitude
（谷值），08:00 与 20:00 ≈ 0（过渡）。

日出/日落基于太阳赤纬 + 纬度计算，支持季节和纬度浮动。
"""

import math

from ascend.time.constants import GAME_DAY, GAME_HOUR
from .constants import DIURNAL_PEAK_HOUR


def diurnal_temp_offset(hour: float, amplitude: float) -> float:
    """日内温度偏移（余弦曲线）。

    phase = (hour - 14) / 24 * 2π，14:00 余弦为 1（峰值），02:00 为 -1（谷值）。

    Args:
        hour: 当前小时 [0, 24)，可带小数。
        amplitude: 昼夜振幅 (°C)。

    Returns:
        温度偏移 (°C)，范围 [-amplitude, +amplitude]。
    """
    phase = (hour - DIURNAL_PEAK_HOUR) / 24.0 * 2 * math.pi
    return amplitude * math.cos(phase)


def diurnal_humidity_offset(hour: float, amplitude: float) -> float:
    """日内湿度偏移 — 反比于温度曲线。

    温度峰值 14:00 = 湿度谷值（-amplitude），温度谷值 02:00 = 湿度峰值（+amplitude）。

    Args:
        hour: 当前小时 [0, 24)，可带小数。
        amplitude: 昼夜湿度振幅 (pp)。

    Returns:
        湿度偏移 (pp)，范围 [-amplitude, +amplitude]。
    """
    phase = (hour - DIURNAL_PEAK_HOUR) / 24.0 * 2 * math.pi
    return -amplitude * math.cos(phase)


def hour_of_game_time(game_time: int) -> float:
    """游戏 tick → 当日小时（带小数）。

    Args:
        game_time: 游戏时间（tick 数）。

    Returns:
        当日小时 [0, 24)。
    """
    return (game_time % GAME_DAY) / GAME_HOUR


# ── 日出/日落 ──────────────────────────────────────────────────

# 黄赤交角（游戏性取整）
_OBLIQUITY_DEG: float = 23.44


def _solar_declination(day_of_year: int) -> float:
    """太阳赤纬（弧度）。

    春分（day_of_year=45）为 0，夏至（135）为 +23.44°，
    秋分（225）为 0，冬至（315）为 -23.44°。

    Args:
        day_of_year: 年内日 [0, 360)。

    Returns:
        赤纬（弧度）。
    """
    return math.radians(
        _OBLIQUITY_DEG * math.sin(2 * math.pi * (day_of_year - 45) / 360)
    )


def sunrise_hour(day_of_year: int, latitude_deg: float) -> float:
    """给定日期和纬度 → 日出时刻（小时）。

    基于太阳赤纬公式，处理极昼（0h）和极夜（12h）边界。

    Args:
        day_of_year: 年内日 [0, 360)。
        latitude_deg: 纬度（度），北纬为正 [-90, 90]。

    Returns:
        日出时刻 [0, 12]。
    """
    decl = _solar_declination(day_of_year)
    lat = math.radians(latitude_deg)
    tan_product = math.tan(lat) * math.tan(decl)
    tan_product = max(-1.0, min(1.0, tan_product))
    half_day_deg = math.degrees(math.acos(-tan_product))
    half_day_h = half_day_deg / 15.0
    return 12.0 - half_day_h


def sunset_hour(day_of_year: int, latitude_deg: float) -> float:
    """给定日期和纬度 → 日落时刻（小时）。

    Args:
        day_of_year: 年内日 [0, 360)。
        latitude_deg: 纬度（度），北纬为正 [-90, 90]。

    Returns:
        日落时刻 [12, 24]。
    """
    decl = _solar_declination(day_of_year)
    lat = math.radians(latitude_deg)
    tan_product = math.tan(lat) * math.tan(decl)
    tan_product = max(-1.0, min(1.0, tan_product))
    half_day_deg = math.degrees(math.acos(-tan_product))
    half_day_h = half_day_deg / 15.0
    return 12.0 + half_day_h
