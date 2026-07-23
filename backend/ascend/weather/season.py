"""季节系统 — 四季划分与温度季节偏移。

纯函数，无状态，线程安全。振幅由调用方（weather_engine）通过
_derive_seasonal_amp 从年均温+年降雨连续推导后传入。

温度偏移用余弦曲线：夏季中点 +amplitude，冬季中点 -amplitude，
春秋分（季节中点）≈ 0，过渡平滑。
"""

import math
from enum import IntEnum

from ascend.config import SEASON_LENGTH_DAYS, SEASONS_PER_YEAR


class Season(IntEnum):
    """四季 — 0=春 1=夏 2=秋 3=冬。"""

    SPRING = 0
    SUMMER = 1
    AUTUMN = 2
    WINTER = 3


# 年内天数 = 季节天数 × 季节数 = 90 × 4 = 360
_DAYS_PER_YEAR: int = SEASON_LENGTH_DAYS * SEASONS_PER_YEAR


def season_of(day: int) -> Season:
    """游戏日 → 季节。

    每季 SEASON_LENGTH_DAYS 天，跨年回绕。

    Args:
        day: 游戏日（从 1 开始）。

    Returns:
        对应季节。
    """
    return Season((day - 1) // SEASON_LENGTH_DAYS % SEASONS_PER_YEAR)


def day_of_season(day: int) -> int:
    """游戏日 → 季节内日序号。

    Args:
        day: 游戏日（从 1 开始）。

    Returns:
        季节内日 [0, SEASON_LENGTH_DAYS)。
    """
    return (day - 1) % SEASON_LENGTH_DAYS


def day_of_year(day: int) -> int:
    """游戏日 → 年内日序号。

    Args:
        day: 游戏日（从 1 开始）。

    Returns:
        年内日 [0, 360)。
    """
    return (day - 1) % _DAYS_PER_YEAR


def seasonal_temp_offset(
    season: Season, day_of_season_val: int, amplitude: float,
) -> float:
    """季节温度偏移（余弦曲线）。

    连续季节进度 progress = season + day_of_season/SEASON_LENGTH_DAYS，
    夏季中点（progress=1.5）取 +amplitude，冬季中点（progress=3.5）
    取 -amplitude，春秋中点 ≈ 0。

    Args:
        season: 当前季节。
        day_of_season_val: 季节内日 [0, SEASON_LENGTH_DAYS)。
        amplitude: 季节振幅 (°C)，由气候档位决定。

    Returns:
        温度偏移 (°C)，范围 [-amplitude, +amplitude]。
    """
    progress = season + day_of_season_val / SEASON_LENGTH_DAYS
    phase = (progress - 1.5) / SEASONS_PER_YEAR * 2 * math.pi
    return amplitude * math.cos(phase)


def seasonal_humidity_offset(
    season: Season, day_of_season_val: int, amplitude: float,
    sharpness: float = 0.0,
) -> float:
    """季节湿度偏移 — 同向于温度曲线（夏季高湿、冬季低湿）。

    当 sharpness=0 时使用标准余弦曲线；sharpness>0 时用 tanh 阶梯化
    （适合季风气候 — 旱湿两季切换更突兀）。

    Args:
        season: 当前季节。
        day_of_season_val: 季节内日 [0, SEASON_LENGTH_DAYS)。
        amplitude: 季节湿度振幅 (pp)。
        sharpness: 阶梯化强度，0=余弦，2.5=典型季风。越大过渡越陡。

    Returns:
        湿度偏移 (pp)，范围 [-amplitude, +amplitude]。
    """
    progress = season + day_of_season_val / SEASON_LENGTH_DAYS
    phase = (progress - 1.5) / SEASONS_PER_YEAR * 2 * math.pi
    raw = math.cos(phase)
    if sharpness > 0:
        return amplitude * math.tanh(raw * sharpness)
    return amplitude * raw


def seasonal_temp_offset_for_day(day: int, amplitude: float) -> float:
    """便捷：游戏日 → 季节温度偏移。

    Args:
        day: 游戏日（从 1 开始）。
        amplitude: 季节振幅 (°C)。

    Returns:
        温度偏移 (°C)。
    """
    return seasonal_temp_offset(season_of(day), day_of_season(day), amplitude)
