"""昼夜曲线纯函数测试 — diurnal.py 单一事实来源。

日出/日落/日照时长/日出方位角均为纯函数（无状态、线程安全）。
日出方位角契约：一天内恒定、随季节渐变（前端光照轨道基准方位）。
"""

import pytest

from ascend.weather.diurnal import (
    daylight_hours,
    sunrise_azimuth,
    sunrise_hour,
    sunset_hour,
    _solar_declination,
)


class TestSunriseSunset:
    def test_equinox_sunrise_east(self):
        # 春分（day 45）北纬 45°：日出正东 90°、正西日落 270°
        sr = sunrise_hour(45, 45.0)
        ss = sunset_hour(45, 45.0)
        assert sr == pytest.approx(6.0, abs=0.05)
        assert ss == pytest.approx(18.0, abs=0.05)

    def test_solstice_daylight_extremes(self):
        # 北纬 45°：夏至（day 135）昼长 > 冬至（day 315）
        summer = daylight_hours(135, 45.0)
        winter = daylight_hours(315, 45.0)
        assert summer > 12.0
        assert winter < 12.0

    def test_polar_day_and_night(self):
        # 极地（lat=80°）：夏至极昼（24h），冬至极夜（0h）
        assert daylight_hours(135, 80.0) == pytest.approx(24.0, abs=0.01)
        assert daylight_hours(315, 80.0) == pytest.approx(0.0, abs=0.01)


class TestSunriseAzimuth:
    def test_equinox_due_east(self):
        # 春分赤纬≈0：日出方位 = 90°（正东），任何纬度
        assert sunrise_azimuth(45, 45.0) == pytest.approx(90.0, abs=0.5)
        assert sunrise_azimuth(45, 0.0) == pytest.approx(90.0, abs=0.5)

    def test_northern_hemisphere_seasonal_trend(self):
        # 北纬 45°：夏至偏东北（<90°），冬至偏东南（>90°）
        summer = sunrise_azimuth(135, 45.0)
        winter = sunrise_azimuth(315, 45.0)
        assert summer < 90.0
        assert winter > 90.0

    def test_southern_hemisphere_mirrored(self):
        # 南纬 45°：与北半球镜像（夏至偏东南、冬至偏东北）
        summer = sunrise_azimuth(315, -45.0)  # 南半球夏至
        winter = sunrise_azimuth(135, -45.0)  # 南半球冬至
        assert summer > 90.0
        assert winter < 90.0

    def test_range_all_seasons(self):
        # 全年任意纬度取值 [0, 180]，极地边界不越界不抛错
        for lat in (-89.9, -45.0, 0.0, 45.0, 89.9):
            for day in (0, 45, 90, 135, 180, 225, 270, 315):
                az = sunrise_azimuth(day, lat)
                assert 0.0 <= az <= 180.0

    def test_polar_clamped(self):
        # 极地：夏至日出方位趋正北（0°）不越界
        az = sunrise_azimuth(135, 89.9)
        assert 0.0 <= az <= 180.0

    def test_deterministic(self):
        assert sunrise_azimuth(135, 45.0) == sunrise_azimuth(135, 45.0)

    def test_uses_precomputed_decl(self):
        # 预计算赤纬与内部推导一致
        decl = _solar_declination(135)
        assert sunrise_azimuth(135, 45.0) == pytest.approx(
            sunrise_azimuth(135, 45.0, solar_decl=decl))


class TestSolarDeclination:
    def test_extrema(self):
        # 夏至 +23.44°，冬至 -23.44°，春/秋分 0
        assert _solar_declination(135) == pytest.approx(0.4091, abs=0.002)  # 23.44° 弧度
        assert _solar_declination(315) == pytest.approx(-0.4091, abs=0.002)
        assert abs(_solar_declination(45)) < 1e-6
        assert abs(_solar_declination(225)) < 1e-6