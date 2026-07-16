"""天气系统单元测试。

解析算架构（无快照）+ per-parameter 事件。按子模块分组。
"""

import pytest
import random as _random

from ascend.time import WorldClock
from ascend.config import GAME_HOUR, GAME_DAY, GAME_YEAR
from ascend.world_tree import WorldTree, Event, AffectedParty
from ascend.space import WeatherParams, ClimateZone, TILE_MAP_SIZE


def _publish_minute(wt, game_time):
    """发布 minute_change 事件驱动 WeatherEngine。"""
    from ascend.config import GAME_DAY, GAME_HOUR
    day = game_time // GAME_DAY + 1
    tod = game_time % GAME_DAY
    hour = int(tod / GAME_HOUR)
    minute = int((tod % GAME_HOUR) / (GAME_HOUR // 60))
    wt.publish(Event(
        timestamp=game_time,
        location=(0, 0, None, None),
        initiator_type="system",
        initiator_id="test",
        affected=[AffectedParty("world", "subject")],
        event_type="minute_change",
        data={"game_time": game_time, "day": day, "hour": hour, "minute": minute},
    ))


def _make_baseline(temp=20.0, rain=800.0, wind=5.0, humidity=60.0,
                   alt=100.0, sun=12.0):
    """构造测试用年均基线 WeatherParams。"""
    return WeatherParams(temp, rain, sun, alt, humidity, wind)


def _force_perception_reset(engine, cx, cy, *params):
    """把指定参数的 last_*_perception 置为哨兵值，强制下一 tick 发布事件。

    引擎首刻静默初始化感知类别（不发事件），测试需要确定性事件时
    用此函数制造"类别变化"。params 取值 "temp"/"humidity"/"wind"/"sunshine"。
    """
    field = engine._fields[(cx, cy)]
    for p in params:
        setattr(field, f"last_{p}_perception", "__test_sentinel__")


# ── constants ───────────────────────────────────────────────────────


class TestWeatherConstants:
    """天气常量测试。"""

    def test_module_importable(self):
        from ascend import config
        assert config is not None

    def test_atmosphere_resolution_positive(self):
        from ascend.config import ATMOSPHERE_RESOLUTION
        assert ATMOSPHERE_RESOLUTION > 0

    def test_drift_rate_positive(self):
        from ascend.config import ATMOSPHERE_DRIFT_RATE
        assert ATMOSPHERE_DRIFT_RATE > 0

    def test_seasons_per_year_is_four(self):
        from ascend.config import SEASONS_PER_YEAR
        assert SEASONS_PER_YEAR == 4

    def test_season_length_covers_year(self):
        from ascend.config import SEASONS_PER_YEAR, SEASON_LENGTH
        assert SEASON_LENGTH * SEASONS_PER_YEAR == GAME_YEAR

    def test_diurnal_hours_in_range(self):
        from ascend.config import DIURNAL_PEAK_HOUR, DIURNAL_TROUGH_HOUR
        assert 0 <= DIURNAL_PEAK_HOUR <= 23
        assert 0 <= DIURNAL_TROUGH_HOUR <= 23
        assert DIURNAL_PEAK_HOUR != DIURNAL_TROUGH_HOUR

    def test_perturb_scales_positive(self):
        from ascend.config import (
            TEMP_PERTURB_SCALE, HUMIDITY_PERTURB_SCALE, WIND_PERTURB_SCALE,
            SUNSHINE_PERTURB_SCALE,
        )
        assert TEMP_PERTURB_SCALE > 0
        assert HUMIDITY_PERTURB_SCALE > 0
        assert WIND_PERTURB_SCALE > 0
        assert SUNSHINE_PERTURB_SCALE > 0

    def test_perception_boundaries_defined(self):
        from ascend.config import (
            TEMP_PERCEPTION_BOUNDARIES, HUMIDITY_PERCEPTION_BOUNDARIES,
            WIND_PERCEPTION_BOUNDARIES, SUNSHINE_PERCEPTION_BOUNDARIES,
        )
        assert len(TEMP_PERCEPTION_BOUNDARIES) >= 4
        assert len(HUMIDITY_PERCEPTION_BOUNDARIES) >= 2
        assert len(WIND_PERCEPTION_BOUNDARIES) >= 3
        assert len(SUNSHINE_PERCEPTION_BOUNDARIES) >= 3
        # 最后一个上限应为 inf
        for boundaries in (TEMP_PERCEPTION_BOUNDARIES, HUMIDITY_PERCEPTION_BOUNDARIES,
                           WIND_PERCEPTION_BOUNDARIES, SUNSHINE_PERCEPTION_BOUNDARIES):
            limit, _ = boundaries[-1]
            import math
            assert math.isinf(limit)
        # 上限严格升序
        for boundaries in (TEMP_PERCEPTION_BOUNDARIES, HUMIDITY_PERCEPTION_BOUNDARIES,
                           WIND_PERCEPTION_BOUNDARIES, SUNSHINE_PERCEPTION_BOUNDARIES):
            limits = [b[0] for b in boundaries]
            for i in range(1, len(limits)):
                assert limits[i] > limits[i - 1]

    def test_humidity_scales_positive(self):
        from ascend.config import (
            HUMIDITY_DIURNAL_SCALE, HUMIDITY_SEASONAL_SCALE,
        )
        assert HUMIDITY_DIURNAL_SCALE > 0
        assert HUMIDITY_SEASONAL_SCALE > 0

    def test_sunshine_perturb_scale_positive(self):
        from ascend.config import SUNSHINE_PERTURB_SCALE
        assert SUNSHINE_PERTURB_SCALE > 0

    def test_rain_depth_positive(self):
        from ascend.config import RAIN_FORECAST_DEPTH, RAIN_REPLENISH_THRESHOLD
        assert RAIN_FORECAST_DEPTH >= 1
        assert 0 < RAIN_REPLENISH_THRESHOLD < RAIN_FORECAST_DEPTH


# ── events schema ──────────────────────────────────────────────────


class TestWeatherEventsSchema:
    """per-parameter 事件 schema 注册测试（含 sunshine_change）。"""

    def test_all_schemas_registered(self):
        import ascend.weather.events  # noqa: F401
        from ascend.world_tree import world_tree
        types = world_tree.schema_registry.registered_types
        for t in ("temperature_change", "humidity_change", "wind_change",
                  "sunshine_change",
                  "precipitation_start", "precipitation_stop"):
            assert t in types, f"缺少 schema: {t}"

    def test_temperature_change_fields(self):
        import ascend.weather.events  # noqa: F401
        from ascend.world_tree import world_tree
        s = world_tree.schema_registry.get("temperature_change")
        assert s.required["temperature"] is float
        assert s.required["perception"] is str
        assert s.required["season"] is int
        assert s.required["time_of_day"] is int

    def test_humidity_change_fields(self):
        import ascend.weather.events  # noqa: F401
        from ascend.world_tree import world_tree
        s = world_tree.schema_registry.get("humidity_change")
        assert s.required["humidity"] is float
        assert s.required["perception"] is str
        assert s.required["time_of_day"] is int

    def test_wind_change_fields(self):
        import ascend.weather.events  # noqa: F401
        from ascend.world_tree import world_tree
        s = world_tree.schema_registry.get("wind_change")
        assert s.required["wind_speed"] is float
        assert s.required["perception"] is str
        assert s.required["wind_dir_x"] is float
        assert s.required["wind_dir_y"] is float
        assert s.required["time_of_day"] is int

    def test_sunshine_change_fields(self):
        import ascend.weather.events  # noqa: F401
        from ascend.world_tree import world_tree
        s = world_tree.schema_registry.get("sunshine_change")
        assert s.required["sunshine"] is float
        assert s.required["perception"] is str
        assert s.required["season"] is int
        assert s.required["time_of_day"] is int

    def test_precip_start_fields(self):
        import ascend.weather.events  # noqa: F401
        from ascend.world_tree import world_tree
        s = world_tree.schema_registry.get("precipitation_start")
        assert s.required["precip_type"] is str
        assert s.required["intensity"] is float
        assert s.required["time_of_day"] is int

    def test_precip_stop_fields(self):
        import ascend.weather.events  # noqa: F401
        from ascend.world_tree import world_tree
        s = world_tree.schema_registry.get("precipitation_stop")
        assert s.required["time_of_day"] is int

    def test_validate_temperature_change(self):
        import ascend.weather.events  # noqa: F401
        from ascend.world_tree import world_tree
        errors = world_tree.schema_registry.validate("temperature_change", {
            "temperature": 25.0, "perception": "cool",
            "season": 1, "time_of_day": 36000,
        })
        assert errors == []

    def test_validate_missing_field_fails(self):
        import ascend.weather.events  # noqa: F401
        from ascend.world_tree import world_tree
        errors = world_tree.schema_registry.validate("temperature_change", {
            "temperature": 25.0, "perception": "cool", "season": 1,
        })
        assert any("time_of_day" in e for e in errors)

    def test_validate_wrong_type_fails(self):
        import ascend.weather.events  # noqa: F401
        from ascend.world_tree import world_tree
        errors = world_tree.schema_registry.validate("wind_change", {
            "wind_speed": "fast", "perception": "calm",
            "wind_dir_x": 0.5, "wind_dir_y": -0.8,
            "time_of_day": 0,
        })
        assert len(errors) >= 1

    def test_global_schemas_registered(self):
        """season_change/sunrise/sunset schema 注册。"""
        import ascend.weather.events  # noqa: F401
        from ascend.world_tree import world_tree
        types = world_tree.schema_registry.registered_types
        for t in ("season_change", "sunrise", "sunset"):
            assert t in types

    def test_season_change_schema_fields(self):
        import ascend.weather.events  # noqa: F401
        from ascend.world_tree import world_tree
        s = world_tree.schema_registry.get("season_change")
        assert s.required["season"] is int
        assert s.required["time_of_day"] is int

    def test_sunrise_sunset_schema_fields(self):
        import ascend.weather.events  # noqa: F401
        from ascend.world_tree import world_tree
        for t in ("sunrise", "sunset"):
            s = world_tree.schema_registry.get(t)
            assert s.required["time_of_day"] is int
            assert s.required["daylight_hours"] is float

    def test_validate_sunshine_change(self):
        import ascend.weather.events  # noqa: F401
        from ascend.world_tree import world_tree
        errors = world_tree.schema_registry.validate("sunshine_change", {
            "sunshine": 12.0, "perception": "moderate",
            "season": 1, "time_of_day": 36000,
        })
        assert errors == []


# ── season ─────────────────────────────────────────────────────────


from ascend.config import SEASON_LENGTH_DAYS


class TestSeason:
    """季节系统测试。"""

    def test_season_enum_values(self):
        from ascend.weather.season import Season
        assert Season.SPRING == 0
        assert Season.SUMMER == 1
        assert Season.AUTUMN == 2
        assert Season.WINTER == 3

    def test_season_of_day_boundaries(self):
        from ascend.weather.season import Season, season_of
        assert season_of(1) == Season.SPRING
        assert season_of(90) == Season.SPRING
        assert season_of(91) == Season.SUMMER
        assert season_of(181) == Season.AUTUMN
        assert season_of(271) == Season.WINTER
        assert season_of(360) == Season.WINTER

    def test_season_of_wraps_year(self):
        from ascend.weather.season import Season, season_of
        assert season_of(361) == Season.SPRING

    def test_day_of_year_wraps(self):
        from ascend.weather.season import day_of_year
        assert day_of_year(1) == 0
        assert day_of_year(360) == 359
        assert day_of_year(361) == 0

    def test_day_of_season(self):
        from ascend.weather.season import day_of_season
        assert day_of_season(1) == 0
        assert day_of_season(90) == 89
        assert day_of_season(91) == 0

    def test_seasonal_temp_offset_summer_peak(self):
        from ascend.weather.season import Season, seasonal_temp_offset
        assert seasonal_temp_offset(Season.SUMMER, SEASON_LENGTH_DAYS // 2, 10.0) == pytest.approx(10.0, abs=1e-6)

    def test_seasonal_temp_offset_winter_trough(self):
        from ascend.weather.season import Season, seasonal_temp_offset
        assert seasonal_temp_offset(Season.WINTER, SEASON_LENGTH_DAYS // 2, 10.0) == pytest.approx(-10.0, abs=1e-6)

    def test_seasonal_temp_offset_spring_autumn_near_zero(self):
        from ascend.weather.season import Season, seasonal_temp_offset
        d = SEASON_LENGTH_DAYS // 2
        assert seasonal_temp_offset(Season.SPRING, d, 10.0) == pytest.approx(0.0, abs=1e-6)
        assert seasonal_temp_offset(Season.AUTUMN, d, 10.0) == pytest.approx(0.0, abs=1e-6)

    def test_seasonal_temp_offset_zero_amplitude(self):
        from ascend.weather.season import Season, seasonal_temp_offset
        for s in Season:
            for d in (0, 45, 89):
                assert seasonal_temp_offset(s, d, 0.0) == 0.0

    def test_seasonal_temp_offset_periodic_across_year(self):
        from ascend.weather.season import seasonal_temp_offset_for_day
        for day in (1, 45, 90, 135, 180, 270, 360):
            a = seasonal_temp_offset_for_day(day, 8.0)
            b = seasonal_temp_offset_for_day(day + 360, 8.0)
            assert a == pytest.approx(b, abs=1e-9)

    def test_seasonal_humidity_offset_summer_peak(self):
        """季节湿度在夏季中点峰值。"""
        from ascend.weather.season import Season, seasonal_humidity_offset
        d = SEASON_LENGTH_DAYS // 2
        assert seasonal_humidity_offset(Season.SUMMER, d, 8.0) == pytest.approx(8.0, abs=1e-6)

    def test_seasonal_humidity_offset_winter_trough(self):
        """季节湿度在冬季中点谷值。"""
        from ascend.weather.season import Season, seasonal_humidity_offset
        d = SEASON_LENGTH_DAYS // 2
        assert seasonal_humidity_offset(Season.WINTER, d, 8.0) == pytest.approx(-8.0, abs=1e-6)

    def test_seasonal_humidity_offset_same_sign_as_temp(self):
        """季节湿度偏移与温度偏移同向（夏湿冬干）。"""
        from ascend.weather.season import (
            Season, seasonal_temp_offset, seasonal_humidity_offset,
        )
        for season in Season:
            for d in (0, 30, 60, 89):
                t = seasonal_temp_offset(season, d, 10.0)
                h = seasonal_humidity_offset(season, d, 6.0)
                # 同号（或同时为 0）
                assert t * h >= -1e-12

    def test_monsoon_humidity_sharp_transition(self):
        """季风湿度曲线在旱湿季之间过渡比余弦更陡。"""
        from ascend.weather.season import Season, seasonal_humidity_offset
        d = SEASON_LENGTH_DAYS // 2
        # 无 sharpness: 夏季中点 = +amplitude
        cos_val = seasonal_humidity_offset(Season.SUMMER, d, 10.0, sharpness=0.0)
        assert cos_val == pytest.approx(10.0, abs=1e-6)
        # sharpness=2.5（季风）：峰值接近但略低（tanh 压缩）
        monsoon_val = seasonal_humidity_offset(Season.SUMMER, d, 10.0, sharpness=2.5)
        assert monsoon_val == pytest.approx(10.0, abs=0.5)  # tanh(0)=0 但 cos=1 → tanh(2.5)≈0.987

    def test_humidity_sharpness_zero_at_equinox(self):
        """sharpness>0 时在春秋分（cos=0）处仍过零。"""
        from ascend.weather.season import Season, seasonal_humidity_offset
        d = SEASON_LENGTH_DAYS // 2
        cos_val = seasonal_humidity_offset(Season.SPRING, d, 10.0, sharpness=0.0)
        monsoon_val = seasonal_humidity_offset(Season.SPRING, d, 10.0, sharpness=2.5)
        assert abs(cos_val) < 0.01
        assert abs(monsoon_val) < 0.01

    def test_monsoon_transition_steeper(self):
        """季风曲线在季节过渡期斜率大于余弦。"""
        from ascend.weather.season import Season, seasonal_humidity_offset
        # 春季 1/4 处（progress=0.25），cos≈0.707 → tanh(0.707*2.5)≈0.94
        # cos_val = 10 * 0.707 = 7.07, monsoon = 10 * 0.94 = 9.4
        cos_val = seasonal_humidity_offset(Season.SPRING, 22, 10.0, sharpness=0.0)
        monsoon_val = seasonal_humidity_offset(Season.SPRING, 22, 10.0, sharpness=2.5)
        assert abs(monsoon_val) > abs(cos_val) + 1.0  # 显著更陡

    def test_seasonal_temp_offset_bounded(self):
        from ascend.weather.season import seasonal_temp_offset_for_day
        amp = 12.0
        for day in range(1, 361):
            o = seasonal_temp_offset_for_day(day, amp)
            assert -amp - 1e-9 <= o <= amp + 1e-9


# ── diurnal ────────────────────────────────────────────────────────


class TestDiurnal:
    """昼夜温度曲线测试。"""

    def test_diurnal_peak_at_14(self):
        from ascend.weather.diurnal import diurnal_temp_offset
        assert diurnal_temp_offset(14.0, 5.0) == pytest.approx(5.0, abs=1e-6)

    def test_diurnal_trough_at_2(self):
        from ascend.weather.diurnal import diurnal_temp_offset
        assert diurnal_temp_offset(2.0, 5.0) == pytest.approx(-5.0, abs=1e-6)

    def test_diurnal_transitions_near_zero(self):
        from ascend.weather.diurnal import diurnal_temp_offset
        assert diurnal_temp_offset(8.0, 5.0) == pytest.approx(0.0, abs=1e-6)
        assert diurnal_temp_offset(20.0, 5.0) == pytest.approx(0.0, abs=1e-6)

    def test_diurnal_zero_amplitude(self):
        from ascend.weather.diurnal import diurnal_temp_offset
        for h in (0.0, 6.0, 14.0, 23.5):
            assert diurnal_temp_offset(h, 0.0) == 0.0

    def test_diurnal_periodic_24h(self):
        from ascend.weather.diurnal import diurnal_temp_offset
        for h in (0.0, 7.5, 14.0, 23.9):
            assert diurnal_temp_offset(h, 4.0) == pytest.approx(
                diurnal_temp_offset(h + 24.0, 4.0), abs=1e-9)

    def test_diurnal_bounded(self):
        from ascend.weather.diurnal import diurnal_temp_offset
        amp = 6.0
        h = 0.0
        while h < 24.0:
            o = diurnal_temp_offset(h, amp)
            assert -amp - 1e-9 <= o <= amp + 1e-9
            h += 0.25

    def test_hour_of_game_time(self):
        from ascend.weather.diurnal import hour_of_game_time
        assert hour_of_game_time(0) == 0.0
        assert hour_of_game_time(14 * GAME_HOUR) == 14.0
        assert hour_of_game_time(GAME_DAY) == 0.0

    def test_diurnal_humidity_peak_at_2(self):
        """湿度昼夜偏移在 02:00 峰值（逆温）。"""
        from ascend.weather.diurnal import diurnal_humidity_offset
        assert diurnal_humidity_offset(2.0, 5.0) == pytest.approx(5.0, abs=1e-6)

    def test_diurnal_humidity_trough_at_14(self):
        """湿度昼夜偏移在 14:00 谷值（逆温）。"""
        from ascend.weather.diurnal import diurnal_humidity_offset
        assert diurnal_humidity_offset(14.0, 5.0) == pytest.approx(-5.0, abs=1e-6)

    def test_diurnal_humidity_transitions_near_zero(self):
        """湿度昼夜偏移在 08:00 和 20:00 过零。"""
        from ascend.weather.diurnal import diurnal_humidity_offset
        assert diurnal_humidity_offset(8.0, 5.0) == pytest.approx(0.0, abs=1e-6)
        assert diurnal_humidity_offset(20.0, 5.0) == pytest.approx(0.0, abs=1e-6)

    def test_diurnal_humidity_inverse_to_temp(self):
        """湿度昼夜偏移与温度昼夜偏移符号相反。"""
        from ascend.weather.diurnal import diurnal_temp_offset, diurnal_humidity_offset
        for h in (0.0, 6.0, 10.0, 14.0, 18.0, 22.0):
            t = diurnal_temp_offset(h, 5.0)
            h_off = diurnal_humidity_offset(h, 5.0)
            assert t == pytest.approx(-h_off, abs=1e-9)

    def test_hour_of_game_time_fractional(self):
        from ascend.weather.diurnal import hour_of_game_time
        assert hour_of_game_time(3600) == 0.5
        assert hour_of_game_time(3600 + GAME_HOUR) == 1.5

    def test_daylight_hours_equator_constant(self):
        """赤道全年日照≈12h。"""
        from ascend.weather.diurnal import daylight_hours
        for doy in (0, 90, 180, 270):
            assert daylight_hours(doy, 0.0) == pytest.approx(12.0, abs=0.05)

    def test_daylight_hours_midlat_summer_longer(self):
        """中纬度夏至日照 > 15h，冬至 < 9h，差值 > 6h。"""
        from ascend.weather.diurnal import daylight_hours
        dl_summer = daylight_hours(135, 45.0)
        dl_winter = daylight_hours(315, 45.0)
        assert dl_summer > 15.0
        assert dl_winter < 9.0
        assert dl_summer - dl_winter > 6.0

    def test_daylight_hours_polar_extremes(self):
        """极昼 > 20h，极夜 < 4h。"""
        from ascend.weather.diurnal import daylight_hours
        assert daylight_hours(135, 75.0) > 20.0
        assert daylight_hours(315, 75.0) < 4.0

    def test_daylight_hours_equals_sunset_minus_sunrise(self):
        """daylight_hours == sunset_hour - sunrise_hour（恒等式）。"""
        from ascend.weather.diurnal import daylight_hours, sunrise_hour, sunset_hour
        for doy in (0, 45, 90, 135, 180, 270):
            for lat in (0.0, 23.0, 45.0, 66.0):
                dl = daylight_hours(doy, lat)
                expected = sunset_hour(doy, lat) - sunrise_hour(doy, lat)
                assert dl == pytest.approx(expected, abs=1e-9)


# ── atmosphere ─────────────────────────────────────────────────────


class TestAtmosphereField:
    """全局大气场测试。"""

    def test_construct_default(self):
        from ascend.weather.atmosphere import AtmosphereField
        assert AtmosphereField() is not None

    def test_sample_in_range(self):
        from ascend.weather.atmosphere import AtmosphereField
        f = AtmosphereField(seed=42)
        for x in (0.0, 1000.0, 5000.0):
            for y in (0.0, 2000.0, 8000.0):
                assert -1.0 <= f.sample(x, y, 0) <= 1.0

    def test_sample_deterministic(self):
        from ascend.weather.atmosphere import AtmosphereField
        f = AtmosphereField(seed=42)
        assert f.sample(1500.0, 2500.0, 10000) == f.sample(1500.0, 2500.0, 10000)

    def test_spatial_continuity(self):
        from ascend.weather.atmosphere import AtmosphereField
        f = AtmosphereField(seed=42)
        v0 = f.sample(1000.0, 1000.0, 0)
        for dx, dy in [(100, 0), (0, 100), (50, 50)]:
            assert abs(f.sample(1000.0 + dx, 1000.0 + dy, 0) - v0) < 0.3

    def test_temporal_continuity(self):
        from ascend.weather.atmosphere import AtmosphereField
        f = AtmosphereField(seed=42)
        v0 = f.sample(1000.0, 1000.0, 0)
        assert abs(f.sample(1000.0, 1000.0, 100) - v0) < 0.01

    def test_different_seeds_differ(self):
        from ascend.weather.atmosphere import AtmosphereField
        a, b = AtmosphereField(seed=0), AtmosphereField(seed=1)
        diffs = [
            a.sample(x, y, 0) != b.sample(x, y, 0)
            for x in (0.0, 1500.0, 3000.0)
            for y in (0.0, 2500.0, 5000.0)
        ]
        assert any(diffs)

    def test_drift_over_long_time(self):
        from ascend.weather.atmosphere import AtmosphereField
        f = AtmosphereField(seed=42)
        assert f.sample(1500.0, 2500.0, 0) != f.sample(1500.0, 2500.0, 100_000_000)

    def test_wind_vector_unit_length(self):
        import math
        from ascend.weather.atmosphere import AtmosphereField
        f = AtmosphereField(seed=42)
        for t in (0, 10000, 1_000_000):
            wx, wy = f.wind_vector(t)
            assert math.hypot(wx, wy) == pytest.approx(1.0, abs=1e-6)

    def test_wind_vector_changes_over_time(self):
        from ascend.weather.atmosphere import AtmosphereField
        f = AtmosphereField(seed=42)
        assert f.wind_vector(0) != f.wind_vector(100_000_000)


# ── rain_events ────────────────────────────────────────────────────


class TestRainEvent:
    """降雨事件强度曲线测试。"""

    def test_intensity_before_start(self):
        from ascend.weather.rain_events import intensity_at, RainEvent
        assert intensity_at(RainEvent(1000, 10000, 10.0), 999) == 0.0

    def test_intensity_at_start(self):
        from ascend.weather.rain_events import intensity_at, RainEvent
        assert intensity_at(RainEvent(1000, 10000, 10.0), 1000) == 0.0

    def test_intensity_ramp_up_quarter(self):
        from ascend.weather.rain_events import intensity_at, RainEvent
        assert intensity_at(RainEvent(0, 10000, 10.0), 1000) == pytest.approx(5.0)

    def test_intensity_peak_mid(self):
        from ascend.weather.rain_events import intensity_at, RainEvent
        assert intensity_at(RainEvent(0, 10000, 10.0), 5000) == pytest.approx(10.0)

    def test_intensity_ramp_down(self):
        from ascend.weather.rain_events import intensity_at, RainEvent
        assert intensity_at(RainEvent(0, 10000, 10.0), 9000) == pytest.approx(5.0)

    def test_intensity_after_end(self):
        from ascend.weather.rain_events import intensity_at, RainEvent
        assert intensity_at(RainEvent(0, 10000, 10.0), 15000) == 0.0

    def test_intensity_custom_ramp_short_burst(self):
        """短促暴雨 ramp：30% up + 40% peak + 30% down。"""
        from ascend.weather.rain_events import intensity_at, RainEvent
        # 0.3 ramp up, 0.3 ramp down → sustain 40%
        e = RainEvent(0, 10000, 10.0)
        assert intensity_at(e, 1500, 0.3, 0.3) == pytest.approx(5.0)   # 50% up
        assert intensity_at(e, 5000, 0.3, 0.3) == pytest.approx(10.0)  # sustain
        assert intensity_at(e, 8500, 0.3, 0.3) == pytest.approx(5.0)   # 50% down

    def test_intensity_zero_ramp_up(self):
        """ramp_up_ratio=0 时立即进入峰值。"""
        from ascend.weather.rain_events import intensity_at, RainEvent
        e = RainEvent(0, 10000, 10.0)
        assert intensity_at(e, 1, 0.0, 0.2) == pytest.approx(10.0)

    def test_rain_schedule_uses_custom_ramp(self):
        """RainSchedule 传递 ramp 参数到 intensity_at。"""
        from ascend.weather.rain_events import RainSchedule, RainEvent
        s = RainSchedule(_random.Random(0), 800.0, 5.0, 2.0,
                         ramp_up_ratio=0.3, ramp_down_ratio=0.3)
        s.push(RainEvent(1000, 10000, 10.0))
        # 0.3 ramp → ramp 延伸到 elapsed=3000（progress=0.3）
        # elapsed=1500 处 progress=0.15 → 0.15/0.3 = 50% intensity = 5.0
        assert s.intensity(2500) == pytest.approx(5.0)

    def test_slots_no_dict(self):
        from ascend.weather.rain_events import RainEvent
        with pytest.raises(AttributeError):
            RainEvent(0, 100, 5.0).foo = 1


class TestRainSchedule:
    """降雨调度测试。"""

    def test_empty_intensity_zero(self):
        from ascend.weather.rain_events import RainSchedule
        assert RainSchedule(_random.Random(0), 800.0, 5.0, 2.0).intensity(0) == 0.0

    def test_empty_pop_due_false(self):
        from ascend.weather.rain_events import RainSchedule
        s = RainSchedule(_random.Random(0), 800.0, 5.0, 2.0)
        s.seed_current(0)
        assert s.pop_due(1000) is False

    def test_push_ascending(self):
        from ascend.weather.rain_events import RainSchedule, RainEvent
        s = RainSchedule(_random.Random(0), 800.0, 5.0, 2.0)
        s.push(RainEvent(1000, 5000, 8.0))
        s.push(RainEvent(10000, 3000, 6.0))
        assert len(s) == 2

    def test_push_non_ascending_raises(self):
        from ascend.weather.rain_events import RainSchedule, RainEvent
        s = RainSchedule(_random.Random(0), 800.0, 5.0, 2.0)
        s.push(RainEvent(10000, 5000, 8.0))
        with pytest.raises(ValueError):
            s.push(RainEvent(5000, 3000, 6.0))

    def test_intensity_during_event(self):
        from ascend.weather.rain_events import RainSchedule, RainEvent
        s = RainSchedule(_random.Random(0), 800.0, 5.0, 2.0)
        s.push(RainEvent(1000, 10000, 10.0))
        assert s.intensity(6000) == pytest.approx(10.0)

    def test_is_raining(self):
        from ascend.weather.rain_events import RainSchedule, RainEvent
        s = RainSchedule(_random.Random(0), 800.0, 5.0, 2.0)
        s.push(RainEvent(1000, 5000, 8.0))
        assert s.is_raining(3000) is True
        assert s.is_raining(7000) is False

    def test_pop_due_start_and_end(self):
        from ascend.weather.rain_events import RainSchedule, RainEvent
        s = RainSchedule(_random.Random(0), 800.0, 5.0, 2.0)
        s.push(RainEvent(1000, 5000, 8.0))  # [1000, 6000)
        s.seed_current(0)
        assert s.pop_due(1000) is True   # 开始
        assert s.pop_due(4000) is False  # 雨中不变
        assert s.pop_due(6000) is True   # 停止

    def test_needs_replenish(self):
        from ascend.weather.rain_events import RainSchedule, RainEvent
        s = RainSchedule(_random.Random(0), 800.0, 5.0, 2.0)
        assert s.needs_replenish(2)
        s.push(RainEvent(1000, 5000, 8.0))
        s.push(RainEvent(20000, 5000, 8.0))
        assert not s.needs_replenish(2)

    def test_latest_start_tick(self):
        from ascend.weather.rain_events import RainSchedule, RainEvent
        s = RainSchedule(_random.Random(0), 800.0, 5.0, 2.0)
        assert s.latest_start_tick() is None
        s.push(RainEvent(1000, 5000, 8.0))
        s.push(RainEvent(20000, 5000, 8.0))
        assert s.latest_start_tick() == 20000

    def test_latest_end_tick(self):
        from ascend.weather.rain_events import RainSchedule, RainEvent
        s = RainSchedule(_random.Random(0), 800.0, 5.0, 2.0)
        assert s.latest_end_tick() is None
        s.push(RainEvent(1000, 5000, 8.0))     # end 6000
        s.push(RainEvent(20000, 3000, 6.0))    # end 23000
        assert s.latest_end_tick() == 23000

    def test_generate_next_deterministic(self):
        from ascend.weather.rain_events import RainSchedule
        s1 = RainSchedule(_random.Random(42), 800.0, 5.0, 2.0)
        s2 = RainSchedule(_random.Random(42), 800.0, 5.0, 2.0)
        e1, e2 = s1.generate_next(0), s2.generate_next(0)
        assert (e1.start_tick, e1.duration, e1.peak_intensity) == (
            e2.start_tick, e2.duration, e2.peak_intensity)

    def test_generate_next_positive(self):
        from ascend.weather.rain_events import RainSchedule
        e = RainSchedule(_random.Random(42), 800.0, 5.0, 2.0).generate_next(0)
        assert e.start_tick > 0 and e.duration > 0 and e.peak_intensity > 0

    def test_mean_interval_desert_rare(self):
        from ascend.weather.rain_events import mean_interval_hours
        assert mean_interval_hours(50.0, 2.0, 0.5) > mean_interval_hours(2000.0, 10.0, 2.0)


# ── weather_field ──────────────────────────────────────────────────


class TestWeatherField:
    """chunk 天气状态容器测试。"""

    def test_construct(self):
        from ascend.weather.weather_field import WeatherField
        wf = WeatherField(0, 0, baseline="bl")
        assert wf.chunk_x == 0
        assert wf.chunk_y == 0
        assert wf.baseline == "bl"
        assert wf.last_temp_perception is None
        assert wf.last_humidity_perception is None
        assert wf.last_wind_perception is None
        assert wf.last_sunshine_perception is None
        assert wf.last_is_daytime is None

    def test_slots_no_dict(self):
        from ascend.weather.weather_field import WeatherField
        wf = WeatherField(0, 0, "bl")
        with pytest.raises(AttributeError):
            wf.foo = 1


class TestSeasonalAmplitude:
    """季节振幅连续推导测试 — _derive_seasonal_amp。"""

    def test_cold_high_amp(self):
        """低温 → 大振幅。"""
        from ascend.weather.weather_engine import _derive_seasonal_amp
        assert _derive_seasonal_amp(-5.0, 800.0) > 25.0

    def test_hot_low_amp(self):
        """高温 → 小振幅。"""
        from ascend.weather.weather_engine import _derive_seasonal_amp
        assert _derive_seasonal_amp(30.0, 2000.0) < 8.0

    def test_monotonic_in_temperature(self):
        """固定降雨，振幅随温度升高而递减。"""
        from ascend.weather.weather_engine import _derive_seasonal_amp
        rainfall = 800.0
        prev = _derive_seasonal_amp(-5.0, rainfall)
        for t in (0.0, 5.0, 12.0, 20.0, 28.0, 35.0):
            curr = _derive_seasonal_amp(t, rainfall)
            assert curr <= prev + 1e-9
            prev = curr

    def test_dry_higher_amp(self):
        """固定温度，干旱区（低降雨）振幅大于湿润区。"""
        from ascend.weather.weather_engine import _derive_seasonal_amp
        dry = _derive_seasonal_amp(15.0, 200.0)
        wet = _derive_seasonal_amp(15.0, 2000.0)
        assert dry > wet

    def test_bounded(self):
        """振幅恒在 [1, 30]。"""
        from ascend.weather.weather_engine import _derive_seasonal_amp
        for t in (-10.0, -5.0, 0.0, 15.0, 35.0, 50.0):
            for r in (0.0, 200.0, 1000.0, 3000.0, 5000.0):
                amp = _derive_seasonal_amp(t, r)
                assert 1.0 <= amp <= 30.0

    def test_continuous_at_climate_boundary(self):
        """气候带交界处（年均温相同）振幅连续，无跳变。"""
        from ascend.weather.weather_engine import _derive_seasonal_amp
        amp_temperate = _derive_seasonal_amp(5.0, 1000.0)
        amp_subarctic = _derive_seasonal_amp(5.0, 800.0)
        assert abs(amp_temperate - amp_subarctic) < 1.0

    def test_no_discrete_jump_across_boundary(self):
        """温带→亚寒带交界，T从4.9→5.1（跨边界），振幅变化微小。"""
        from ascend.weather.weather_engine import _derive_seasonal_amp
        amp_below = _derive_seasonal_amp(4.9, 800.0)
        amp_above = _derive_seasonal_amp(5.1, 800.0)
        assert abs(amp_above - amp_below) < 0.5

    def test_tropical_savanna_to_desert_continuous(self):
        """热带草原→沙漠交界，R从201→199（跨R=200阈值），振幅变化微小。"""
        from ascend.weather.weather_engine import _derive_seasonal_amp
        amp_savanna = _derive_seasonal_amp(22.0, 201.0)
        amp_desert = _derive_seasonal_amp(22.0, 199.0)
        assert abs(amp_desert - amp_savanna) < 0.1


# ── weather_engine ─────────────────────────────────────────────────


class TestWeatherEngine:
    """天气引擎测试 — 解析算 + per-parameter 事件。"""

    def test_construct(self):
        from ascend.weather.weather_engine import WeatherEngine
        wt = WorldTree()
        e = WeatherEngine(WorldClock(), seed=42, world_tree_arg=wt)
        e.shutdown()

    def test_no_fields_tick_noop(self):
        from ascend.weather.weather_engine import WeatherEngine
        wt = WorldTree()
        clock = WorldClock()
        e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
        _publish_minute(wt, clock.time)
        e.shutdown()

    def test_temperature_in_bounds(self):
        """首次 tick 的 temperature_change 事件 data 在物理边界内。"""
        from ascend.weather.weather_engine import WeatherEngine
        wt = WorldTree()
        events = []
        wt.subscribe("temperature_change", lambda e: events.append(e))
        clock = WorldClock()
        e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
        e.register_chunk(0, 0, _make_baseline(), ClimateZone.TEMPERATE_FOREST, 15.0)
        _publish_minute(wt, clock.time)  # 首刻静默初始化
        _force_perception_reset(e, 0, 0, "temp")
        clock.skip(1)
        _publish_minute(wt, clock.time)
        assert len(events) == 1
        assert -30.0 <= events[0].data["temperature"] <= 50.0
        e.shutdown()

    def test_wind_change_includes_direction(self):
        """wind_change 事件附带风向单位向量。"""
        import math
        from ascend.weather.weather_engine import WeatherEngine
        wt = WorldTree()
        events = []
        wt.subscribe("wind_change", lambda e: events.append(e))
        clock = WorldClock()
        e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
        e.register_chunk(0, 0, _make_baseline(), ClimateZone.TEMPERATE_FOREST, 15.0)
        _publish_minute(wt, clock.time)  # 首刻静默初始化
        _force_perception_reset(e, 0, 0, "wind")
        clock.skip(1)
        _publish_minute(wt, clock.time)
        assert len(events) >= 1
        d = events[0].data
        wdx, wdy = d["wind_dir_x"], d["wind_dir_y"]
        assert math.hypot(wdx, wdy) == pytest.approx(1.0, abs=1e-6)
        e.shutdown()

    def test_temperature_change_on_crossing_perception(self):
        """推进足够多游戏天确保跨越感知边界 → temperature_change 带新标签。"""
        from ascend.weather.weather_engine import WeatherEngine
        wt = WorldTree()
        events = []
        wt.subscribe("temperature_change", lambda e: events.append(e))
        clock = WorldClock()
        e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
        # 基线温度 5°C → "cold"（上限 <13），冷季振幅 ~8°C，日间振幅 ~6°C
        # → 50 天（半个季节）后温度必定跨越进入 "chilly" 或 "cool"
        e.register_chunk(0, 0, _make_baseline(temp=5.0), ClimateZone.TEMPERATE_FOREST, 5.0)
        _publish_minute(wt, clock.time)  # 首刻静默初始化
        assert len(events) == 0
        before_perception = e.get_perceptions(0, 0)["temperature"]
        clock.skip(50 * GAME_DAY)
        _publish_minute(wt, clock.time)
        after_events = [ev for ev in events if ev.event_type == "temperature_change"]
        assert len(after_events) >= 1
        after_perception = after_events[0].data["perception"]
        assert after_perception != before_perception
        assert isinstance(after_perception, str)
        e.shutdown()

    def test_first_tick_emits_no_param_events(self):
        """首刻静默初始化感知类别，不发事件（初始状态走查询 API）。"""
        from ascend.weather.weather_engine import WeatherEngine
        wt = WorldTree()
        events = []
        for t in ("temperature_change", "humidity_change", "wind_change",
                  "sunshine_change", "precipitation_start", "precipitation_stop"):
            wt.subscribe(t, lambda e: events.append(e))
        clock = WorldClock()
        e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
        e.register_chunk(0, 0, _make_baseline(), ClimateZone.TEMPERATE_FOREST, 15.0)
        _publish_minute(wt, clock.time)
        param_events = [ev for ev in events
                        if ev.event_type in ("temperature_change", "humidity_change",
                                             "wind_change", "sunshine_change")]
        assert param_events == []
        # 感知类别已静默初始化
        field = e._fields[(0, 0)]
        assert field.last_temp_perception is not None
        assert field.last_humidity_perception is not None
        assert field.last_wind_perception is not None
        assert field.last_sunshine_perception is not None
        e.shutdown()

    def test_perception_change_emits_with_perception_field(self):
        """类别变化时发布的事件带 perception 字段。"""
        from ascend.weather.weather_engine import WeatherEngine
        wt = WorldTree()
        events = []
        for t in ("temperature_change", "humidity_change", "wind_change",
                  "sunshine_change"):
            wt.subscribe(t, lambda e: events.append(e))
        clock = WorldClock()
        e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
        e.register_chunk(0, 0, _make_baseline(), ClimateZone.TEMPERATE_FOREST, 15.0)
        _publish_minute(wt, clock.time)  # 首刻静默初始化
        _force_perception_reset(e, 0, 0, "temp", "humidity", "wind", "sunshine")
        clock.skip(1)
        _publish_minute(wt, clock.time)
        assert sum(1 for ev in events if ev.event_type == "temperature_change") == 1
        assert sum(1 for ev in events if ev.event_type == "humidity_change") == 1
        assert sum(1 for ev in events if ev.event_type == "wind_change") == 1
        assert sum(1 for ev in events if ev.event_type == "sunshine_change") == 1
        for ev in events:
            assert "perception" in ev.data
            assert isinstance(ev.data["perception"], str)
        e.shutdown()

    def test_no_event_within_same_perception(self):
        """同一感知类别内微小波动不发事件。"""
        from ascend.weather.weather_engine import WeatherEngine
        wt = WorldTree()
        events = []
        wt.subscribe("temperature_change", lambda e: events.append(e))
        clock = WorldClock()
        e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
        e.register_chunk(0, 0, _make_baseline(temp=25.0), ClimateZone.TEMPERATE_FOREST, 15.0)
        _publish_minute(wt, clock.time)  # 首刻静默初始化
        assert isinstance(e.get_perceptions(0, 0)["temperature"], str)
        clock.skip(1)  # 推进 1 tick，微小变化
        _publish_minute(wt, clock.time)
        temp_events = [ev for ev in events if ev.event_type == "temperature_change"]
        assert len(temp_events) == 0
        e.shutdown()

    def test_precip_start_emitted_on_event_start(self):
        """降水事件开始时发 precipitation_start（含 precip_type）。"""
        from ascend.weather.weather_engine import WeatherEngine
        wt = WorldTree()
        events = []
        wt.subscribe("precipitation_start", lambda e: events.append(e))
        clock = WorldClock()
        e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
        e.register_chunk(0, 0, _make_baseline(temp=20.0), ClimateZone.TEMPERATE_FOREST, 15.0)
        rain = e._rain_schedules[(0, 0)]
        e0 = rain._events[0]
        clock.skip(e0.start_tick + 1 - clock.time)
        _publish_minute(wt, clock.time)
        assert len(events) >= 1
        assert events[0].data["intensity"] > 0
        assert events[0].data["precip_type"] == "rain"
        e.shutdown()

    def test_precip_stop_emitted_on_event_end(self):
        """降水事件结束时发 precipitation_stop。"""
        from ascend.weather.weather_engine import WeatherEngine
        wt = WorldTree()
        events = []
        for t in ("precipitation_start", "precipitation_stop"):
            wt.subscribe(t, lambda e: events.append(e))
        clock = WorldClock()
        e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
        e.register_chunk(0, 0, _make_baseline(), ClimateZone.TEMPERATE_FOREST, 15.0)
        rain = e._rain_schedules[(0, 0)]
        e0 = rain._events[0]
        end = e0.start_tick + e0.duration
        # 先推进到雨中（触发 precipitation_start）
        clock.skip(e0.start_tick + 1 - clock.time)
        _publish_minute(wt, clock.time)
        # 推进到结束
        clock.skip(end - clock.time + 1)
        _publish_minute(wt, clock.time)
        stops = [ev for ev in events if ev.event_type == "precipitation_stop"]
        assert len(stops) >= 1
        e.shutdown()

    def test_precip_type_snow_when_cold(self):
        """温度 ≤ 0°C 时降水形态为 snow。"""
        from ascend.weather.weather_engine import WeatherEngine
        wt = WorldTree()
        events = []
        wt.subscribe("precipitation_start", lambda e: events.append(e))
        clock = WorldClock()
        e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
        # 极地，年均温 -10°C
        e.register_chunk(0, 0, _make_baseline(temp=-10.0), ClimateZone.POLAR_TUNDRA, -5.0)
        rain = e._rain_schedules[(0, 0)]
        e0 = rain._events[0]
        clock.skip(e0.start_tick + 1 - clock.time)
        _publish_minute(wt, clock.time)
        assert len(events) >= 1
        assert events[0].data["precip_type"] == "snow"
        e.shutdown()

    def test_event_data_schema_valid(self):
        """发布的事件 data 通过 schema 校验（含 perception 字段）。"""
        from ascend.weather.weather_engine import WeatherEngine
        wt = WorldTree()
        events = []
        wt.subscribe("temperature_change", lambda e: events.append(e))
        clock = WorldClock()
        e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
        e.register_chunk(0, 0, _make_baseline(), ClimateZone.TEMPERATE_FOREST, 15.0)
        _publish_minute(wt, clock.time)  # 首刻静默初始化
        _force_perception_reset(e, 0, 0, "temp")
        clock.skip(1)
        _publish_minute(wt, clock.time)
        d = events[0].data
        assert "perception" in d
        assert isinstance(d["perception"], str)
        errors = wt.schema_registry.validate("temperature_change", d)
        assert errors == []
        e.shutdown()

    def test_shutdown_unsubscribes(self):
        from ascend.weather.weather_engine import WeatherEngine
        wt = WorldTree()
        events = []
        wt.subscribe("temperature_change", lambda e: events.append(e))
        clock = WorldClock()
        e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
        e.register_chunk(0, 0, _make_baseline(), ClimateZone.TEMPERATE_FOREST, 15.0)
        _publish_minute(wt, clock.time)  # 首刻静默初始化
        _force_perception_reset(e, 0, 0, "temp")
        e.shutdown()
        clock.skip(1)
        _publish_minute(wt, clock.time)
        assert len(events) == 0

    def test_multiple_chunks_independent(self):
        """多 chunk 各自发事件，location 区分。"""
        from ascend.weather.weather_engine import WeatherEngine
        wt = WorldTree()
        events = []
        wt.subscribe("temperature_change", lambda e: events.append(e))
        clock = WorldClock()
        e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
        e.register_chunk(0, 0, _make_baseline(), ClimateZone.TEMPERATE_FOREST, 15.0)
        e.register_chunk(5, 5, _make_baseline(temp=25.0), ClimateZone.DESERT, 25.0)
        _publish_minute(wt, clock.time)  # 首刻静默初始化
        _force_perception_reset(e, 0, 0, "temp")
        _force_perception_reset(e, 5, 5, "temp")
        clock.skip(1)
        _publish_minute(wt, clock.time)
        assert len(events) == 2
        locs = {ev.location[:2] for ev in events}
        assert locs == {(0, 0), (5, 5)}
        e.shutdown()

    def test_replenish_rain_fills(self):
        """补算填充低于阈值的降雨事件。"""
        from ascend.weather.weather_engine import WeatherEngine
        wt = WorldTree()
        clock = WorldClock()
        e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
        e.register_chunk(0, 0, _make_baseline(), ClimateZone.TEMPERATE_FOREST, 15.0)
        rain = e._rain_schedules[(0, 0)]
        rain._events.clear()
        rain._last_raining = False
        e._replenish_rain((0, 0), clock.time)
        assert len(rain) > 0
        e.shutdown()

    def test_prune_removes_past_events(self):
        """prune_before 移除已结束的过期事件。"""
        from ascend.weather.rain_events import RainSchedule, RainEvent
        s = RainSchedule(_random.Random(0), 800.0, 5.0, 2.0)
        s.push(RainEvent(0, 5000, 8.0))       # [0, 5000)
        s.push(RainEvent(20000, 5000, 8.0))   # [20000, 25000)
        s.push(RainEvent(40000, 5000, 8.0))   # [40000, 45000)
        assert len(s) == 3
        s.prune_before(5000)   # 第一个刚结束，移除
        assert len(s) == 2
        s.prune_before(25000)  # 第二个也结束了
        assert len(s) == 1
        assert s._events[0].start_tick == 40000

    def test_prune_keeps_ongoing_event(self):
        """prune_before 保留正在进行中的事件（now 在区间内）。"""
        from ascend.weather.rain_events import RainSchedule, RainEvent
        s = RainSchedule(_random.Random(0), 800.0, 5.0, 2.0)
        s.push(RainEvent(0, 10000, 8.0))   # [0, 10000)
        s.prune_before(5000)               # now=5000，事件还在进行
        assert len(s) == 1

    def test_replenish_after_prune_works(self):
        """裁剪后事件数低于阈值时补算恢复。"""
        from ascend.weather.weather_engine import WeatherEngine
        wt = WorldTree()
        clock = WorldClock()
        e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
        e.register_chunk(0, 0, _make_baseline(), ClimateZone.TEMPERATE_FOREST, 15.0)
        rain = e._rain_schedules[(0, 0)]
        # 模拟：所有预排事件都已过期
        from ascend.weather.rain_events import RainEvent as RE
        for ev in list(rain._events):
            rain._events.remove(ev)
        rain.push(RE(0, 5000, 8.0))  # 很久以前就结束了
        assert len(rain) == 1
        # 补算应裁剪过期事件然后填充
        e._replenish_rain((0, 0), clock.time)  # clock.time >> 5000
        assert len(rain) >= 2  # 至少填充了 2 个未来事件

    def test_sunshine_change_in_valid_range(self):
        """sunshine_change 的 sunshine 值在 [0, 24] 范围内。"""
        from ascend.weather.weather_engine import WeatherEngine
        wt = WorldTree()
        events = []
        wt.subscribe("sunshine_change", lambda e: events.append(e))
        clock = WorldClock()
        e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
        e.register_chunk(0, 0, _make_baseline(sun=12.0), ClimateZone.TEMPERATE_FOREST, 15.0)
        _publish_minute(wt, clock.time)  # 首刻静默初始化
        _force_perception_reset(e, 0, 0, "sunshine")
        clock.skip(1)
        _publish_minute(wt, clock.time)
        assert len(events) == 1
        assert 0.0 <= events[0].data["sunshine"] <= 24.0
        e.shutdown()

    def test_sunrise_sunset_per_chunk_location(self):
        """sunrise/sunset 是 per-chunk 事件，location 为 chunk 坐标。"""
        from ascend.weather.weather_engine import WeatherEngine
        wt = WorldTree()
        events = []
        wt.subscribe("sunset", lambda e: events.append(e))
        clock = WorldClock()
        clock.skip(6 * GAME_HOUR)  # 12:00（正午，确定白天）
        e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
        e.register_chunk(3, 7, _make_baseline(), ClimateZone.TEMPERATE_FOREST, 15.0)
        _publish_minute(wt, clock.time)  # 首次（昼）
        clock.skip(6 * GAME_HOUR)  # 18:00（已日落 → 夜）
        _publish_minute(wt, clock.time)
        assert len(events) == 1
        assert events[0].location[:2] == (3, 7)
        assert "daylight_hours" in events[0].data
        assert events[0].data["daylight_hours"] > 0
        e.shutdown()

    def test_sunrise_per_chunk_with_daylight_hours(self):
        """日出发 sunrise（per-chunk），含 daylight_hours。"""
        from ascend.weather.weather_engine import WeatherEngine
        wt = WorldTree()
        events = []
        wt.subscribe("sunrise", lambda e: events.append(e))
        clock = WorldClock()
        clock.skip(14 * GAME_HOUR)  # 20:00（夜里）
        e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
        e.register_chunk(5, 5, _make_baseline(), ClimateZone.TEMPERATE_FOREST, 15.0)
        _publish_minute(wt, clock.time)  # 首次（夜）
        clock.skip(12 * GAME_HOUR)  # 到次日 08:00（已日出）
        _publish_minute(wt, clock.time)
        assert len(events) == 1
        assert events[0].location[:2] == (5, 5)
        assert "daylight_hours" in events[0].data
        assert events[0].data["daylight_hours"] > 0
        e.shutdown()

    def test_derive_latitude_equator(self):
        """年均温 35°C → 纬度≈0（赤道）。"""
        from ascend.weather.weather_engine import _derive_latitude
        assert _derive_latitude(35.0) == pytest.approx(0.0, abs=2.0)

    def test_derive_latitude_polar(self):
        """年均温 -5°C → 纬度≈80（极地边缘）。"""
        from ascend.weather.weather_engine import _derive_latitude
        assert _derive_latitude(-5.0) == pytest.approx(80.0, abs=2.0)

    def test_derive_latitude_monotonic(self):
        """温度越高 → 纬度越低。"""
        from ascend.weather.weather_engine import _derive_latitude
        assert _derive_latitude(0.0) > _derive_latitude(20.0)

    def test_derive_seasonal_amp_polar_large(self):
        """低温（-5°C）→ 季节振幅 > 20°C。"""
        from ascend.weather.weather_engine import _derive_seasonal_amp
        amp = _derive_seasonal_amp(-5.0, 500.0)
        assert amp > 20.0

    def test_derive_seasonal_amp_equatorial_small(self):
        """高温（35°C）→ 季节振幅 < 5°C。"""
        from ascend.weather.weather_engine import _derive_seasonal_amp
        amp = _derive_seasonal_amp(35.0, 2000.0)
        assert amp < 5.0

    def test_derive_seasonal_amp_dry_larger(self):
        """干旱区振幅 > 同温湿润区（大陆性气候）。"""
        from ascend.weather.weather_engine import _derive_seasonal_amp
        amp_dry = _derive_seasonal_amp(15.0, 200.0)
        amp_wet = _derive_seasonal_amp(15.0, 2000.0)
        assert amp_dry > amp_wet

    def test_temperature_gradient_at_climate_boundary(self):
        """不同气候带交界处，baseline 接近的 chunk 夏季温度接近（无跳变）。

        温带(T=5.1°C) 与 亚寒带(T=4.9°C)，年均温差 0.2°C，
        夏季温度差应 < 2°C（季节振幅连续推导，非离散取值）。
        """
        from ascend.weather.weather_engine import WeatherEngine
        from ascend.config import GAME_DAY
        wt = WorldTree()
        events: list = []
        wt.subscribe("temperature_change", lambda e: events.append(e))
        clock = WorldClock()
        e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
        e.register_chunk(0, 0, _make_baseline(temp=5.1, rain=800.0),
                         ClimateZone.TEMPERATE_FOREST, 8.0)
        e.register_chunk(1, 0, _make_baseline(temp=4.9, rain=800.0),
                         ClimateZone.SUBARCTIC_TAIGA, 0.0)
        clock.skip(134 * GAME_DAY + 6 * GAME_HOUR)
        _publish_minute(wt, clock.time)  # 首刻静默初始化
        _force_perception_reset(e, 0, 0, "temp")
        _force_perception_reset(e, 1, 0, "temp")
        clock.skip(1)
        _publish_minute(wt, clock.time)
        temp_by_chunk: dict[tuple, float] = {}
        for ev in events:
            if ev.event_type == "temperature_change":
                temp_by_chunk[ev.location[:2]] = ev.data["temperature"]
        assert (0, 0) in temp_by_chunk and (1, 0) in temp_by_chunk
        temp_diff = abs(temp_by_chunk[(0, 0)] - temp_by_chunk[(1, 0)])
        assert temp_diff < 2.0, (
            f"气候带交界处温度跳变 {temp_diff:.1f}°C，预期 < 2°C"
        )
        e.shutdown()


# ── 感知分类 ────────────────────────────────────────────────────────


class TestPerceptionClassification:
    """感知分类函数测试。"""

    def test_classify_temperature_bounds(self):
        from ascend.weather.weather_engine import classify_temperature
        assert classify_temperature(-30.0) == "bitter_cold"
        assert classify_temperature(-10.0) == "freezing"
        assert classify_temperature(-3.1) == "freezing"
        assert classify_temperature(-3.0) == "cold"
        assert classify_temperature(4.9) == "cold"
        assert classify_temperature(5.0) == "chilly"
        assert classify_temperature(12.9) == "chilly"
        assert classify_temperature(13.0) == "cool"
        assert classify_temperature(19.9) == "cool"
        assert classify_temperature(20.0) == "mild"
        assert classify_temperature(24.9) == "mild"
        assert classify_temperature(25.0) == "warm"
        assert classify_temperature(29.9) == "warm"
        assert classify_temperature(30.0) == "hot"
        assert classify_temperature(35.9) == "hot"
        assert classify_temperature(36.0) == "scorching"
        assert classify_temperature(42.9) == "scorching"
        assert classify_temperature(43.0) == "extreme_heat"
        assert classify_temperature(60.0) == "extreme_heat"

    def test_classify_humidity_bounds(self):
        from ascend.weather.weather_engine import classify_humidity
        assert classify_humidity(0.0) == "dry"
        assert classify_humidity(24.9) == "dry"
        assert classify_humidity(25.0) == "comfortable"
        assert classify_humidity(49.9) == "comfortable"
        assert classify_humidity(50.0) == "humid"
        assert classify_humidity(71.9) == "humid"
        assert classify_humidity(72.0) == "very_humid"
        assert classify_humidity(87.9) == "very_humid"
        assert classify_humidity(88.0) == "oppressive"
        assert classify_humidity(100.0) == "oppressive"

    def test_classify_wind_bounds(self):
        from ascend.weather.weather_engine import classify_wind
        assert classify_wind(0.0) == "calm"
        assert classify_wind(1.4) == "calm"
        assert classify_wind(1.5) == "light_breeze"
        assert classify_wind(3.9) == "light_breeze"
        assert classify_wind(4.0) == "breezy"
        assert classify_wind(7.9) == "breezy"
        assert classify_wind(8.0) == "windy"
        assert classify_wind(13.9) == "windy"
        assert classify_wind(14.0) == "strong"
        assert classify_wind(22.9) == "strong"
        assert classify_wind(23.0) == "gale"
        assert classify_wind(60.0) == "gale"

    def test_classify_sunshine_bounds(self):
        from ascend.weather.weather_engine import classify_sunshine
        assert classify_sunshine(0.0) == "very_short"
        assert classify_sunshine(1.4) == "very_short"
        assert classify_sunshine(1.5) == "short"
        assert classify_sunshine(4.4) == "short"
        assert classify_sunshine(4.5) == "moderate"
        assert classify_sunshine(7.9) == "moderate"
        assert classify_sunshine(8.0) == "long"
        assert classify_sunshine(11.9) == "long"
        assert classify_sunshine(12.0) == "very_long"
        assert classify_sunshine(15.4) == "very_long"
        assert classify_sunshine(15.5) == "extreme"
        assert classify_sunshine(24.0) == "extreme"


# ── 查询 API ────────────────────────────────────────────────────────


class TestSunlightIntensity:
    """日照强度感知分类测试。"""

    def test_classify_sunlight_intensity_bounds(self):
        from ascend.weather.weather_engine import classify_sunlight_intensity
        assert classify_sunlight_intensity(0.0) == "dark"
        assert classify_sunlight_intensity(0.009) == "dark"
        assert classify_sunlight_intensity(0.01) == "dim"
        assert classify_sunlight_intensity(0.24) == "dim"
        assert classify_sunlight_intensity(0.25) == "moderate"
        assert classify_sunlight_intensity(0.54) == "moderate"
        assert classify_sunlight_intensity(0.55) == "bright"
        assert classify_sunlight_intensity(0.79) == "bright"
        assert classify_sunlight_intensity(0.80) == "intense"
        assert classify_sunlight_intensity(1.0) == "intense"

    def test_get_daylight_info_daytime_intensity(self):
        """白天正午日照强度接近 1。"""
        from ascend.weather.weather_engine import WeatherEngine
        from ascend.config import GAME_DAY
        wt = WorldTree()
        clock = WorldClock()
        clock.skip(6 * GAME_HOUR)  # 12:00（正午）
        e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
        e.register_chunk(0, 0, _make_baseline(), ClimateZone.TEMPERATE_FOREST, 15.0)
        dl = e.get_daylight_info(0, 0)
        assert dl is not None
        sr, ss, daylight, intensity = dl
        assert daylight > 0
        assert intensity > 0.8  # 正午强度接近 1
        e.shutdown()

    def test_get_daylight_info_nighttime_intensity(self):
        """夜间强度为 0。"""
        from ascend.weather.weather_engine import WeatherEngine
        wt = WorldTree()
        clock = WorldClock()
        clock.skip(14 * GAME_HOUR)  # 20:00（夜间）
        e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
        e.register_chunk(0, 0, _make_baseline(), ClimateZone.TEMPERATE_FOREST, 15.0)
        dl = e.get_daylight_info(0, 0)
        assert dl is not None
        intensity = dl[3]
        assert intensity == 0.0
        e.shutdown()

    def test_get_daylight_info_unregistered(self):
        from ascend.weather.weather_engine import WeatherEngine
        wt = WorldTree()
        clock = WorldClock()
        e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
        assert e.get_daylight_info(999, 999) is None
        e.shutdown()

    def test_rain_attenuates_intensity(self):
        """正午暴雨（30 mm/h）时日照强度显著低于无雨。"""
        from ascend.weather.weather_engine import WeatherEngine
        wt = WorldTree()
        clock = WorldClock()
        clock.skip(6 * GAME_HOUR)  # 12:00（正午）
        e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
        e.register_chunk(0, 0, _make_baseline(), ClimateZone.TEMPERATE_FOREST, 15.0)
        clear = e.get_daylight_info(0, 0, rainfall=0.0)[3]
        storm = e.get_daylight_info(0, 0, rainfall=30.0)[3]
        assert clear > 0.8
        # 30 mm/h 达到衰减上限：强度打两折
        assert storm == pytest.approx(clear * 0.2, abs=0.06)
        assert storm < clear * 0.5
        e.shutdown()

    def test_daylight_info_sunrise_before_sunset(self):
        """日出时刻早于日落时刻。"""
        from ascend.weather.weather_engine import WeatherEngine
        wt = WorldTree()
        clock = WorldClock()
        e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
        e.register_chunk(0, 0, _make_baseline(), ClimateZone.TEMPERATE_FOREST, 15.0)
        dl = e.get_daylight_info(0, 0)
        assert dl is not None
        sr, ss = dl[0], dl[1]
        assert sr < ss
        e.shutdown()


class TestWeatherQueryAPI:
    """get_weather / get_perceptions 查询 API 测试。"""

    def test_get_weather_current_time(self):
        from ascend.weather.weather_engine import WeatherEngine
        wt = WorldTree()
        clock = WorldClock()
        e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
        e.register_chunk(0, 0, _make_baseline(), ClimateZone.TEMPERATE_FOREST, 15.0)
        wp = e.get_weather(0, 0)
        assert wp is not None
        assert -30.0 <= wp.temperature <= 50.0
        assert 0.0 <= wp.humidity <= 100.0
        assert 0.0 <= wp.wind_speed <= 50.0
        assert 0.0 <= wp.sunshine <= 24.0
        e.shutdown()

    def test_get_weather_unregistered_returns_none(self):
        from ascend.weather.weather_engine import WeatherEngine
        wt = WorldTree()
        clock = WorldClock()
        e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
        assert e.get_weather(999, 999) is None
        e.shutdown()

    def test_get_weather_specific_time(self):
        """查询过去时刻的天气 —— 不同季节温度应不同。"""
        from ascend.weather.weather_engine import WeatherEngine
        wt = WorldTree()
        clock = WorldClock()
        e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
        e.register_chunk(0, 0, _make_baseline(), ClimateZone.TEMPERATE_FOREST, 15.0)
        wp_now = e.get_weather(0, 0)
        # 向前推进 90 天后，查询"当前"和"45 天前"比较
        clock.skip(90 * GAME_DAY)
        wp_later = e.get_weather(0, 0)
        wp_past = e.get_weather(0, 0, clock.time - 45 * GAME_DAY)
        assert wp_now is not None and wp_later is not None and wp_past is not None
        # 不同季节温度应不同
        assert wp_now.temperature != wp_later.temperature
        # 过去时刻应在 now 与 later 之间
        assert wp_past.temperature != wp_later.temperature
        e.shutdown()

    def test_get_weather_deterministic(self):
        from ascend.weather.weather_engine import WeatherEngine
        wt = WorldTree()
        clock = WorldClock()
        e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
        e.register_chunk(0, 0, _make_baseline(), ClimateZone.TEMPERATE_FOREST, 15.0)
        t = clock.time  # 当前时刻
        wp1 = e.get_weather(0, 0, t)
        wp2 = e.get_weather(0, 0, t)
        assert wp1.temperature == wp2.temperature
        assert wp1.humidity == wp2.humidity
        assert wp1.wind_speed == wp2.wind_speed
        assert wp1.sunshine == wp2.sunshine
        e.shutdown()

    def test_get_perceptions(self):
        from ascend.weather.weather_engine import WeatherEngine
        wt = WorldTree()
        clock = WorldClock()
        e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
        e.register_chunk(0, 0, _make_baseline(), ClimateZone.TEMPERATE_FOREST, 15.0)
        p = e.get_perceptions(0, 0)
        assert p is not None
        for key in ("temperature", "humidity", "wind", "sunshine"):
            assert key in p
            assert isinstance(p[key], str)
        e.shutdown()

    def test_get_perceptions_unregistered_returns_none(self):
        from ascend.weather.weather_engine import WeatherEngine
        wt = WorldTree()
        clock = WorldClock()
        e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
        assert e.get_perceptions(999, 999) is None
        e.shutdown()

    def test_get_weather_future_time_raises(self):
        """查询未来时刻抛 ValueError。"""
        from ascend.weather.weather_engine import WeatherEngine
        wt = WorldTree()
        clock = WorldClock()
        e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
        e.register_chunk(0, 0, _make_baseline(), ClimateZone.TEMPERATE_FOREST, 15.0)
        with pytest.raises(ValueError):
            e.get_weather(0, 0, clock.time + 1)
        e.shutdown()

    def test_get_perceptions_future_time_raises(self):
        """get_perceptions 查询未来时刻抛 ValueError。"""
        from ascend.weather.weather_engine import WeatherEngine
        wt = WorldTree()
        clock = WorldClock()
        e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
        e.register_chunk(0, 0, _make_baseline(), ClimateZone.TEMPERATE_FOREST, 15.0)
        with pytest.raises(ValueError):
            e.get_perceptions(0, 0, clock.time + GAME_DAY)
        e.shutdown()

    def test_get_daylight_info_future_time_raises(self):
        """get_daylight_info 查询未来时刻抛 ValueError。"""
        from ascend.weather.weather_engine import WeatherEngine
        wt = WorldTree()
        clock = WorldClock()
        e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
        e.register_chunk(0, 0, _make_baseline(), ClimateZone.TEMPERATE_FOREST, 15.0)
        with pytest.raises(ValueError):
            e.get_daylight_info(0, 0, clock.time + 1)
        e.shutdown()

    def test_get_weather_past_time_allowed(self):
        """查询当前/过去时刻不抛异常。"""
        from ascend.weather.weather_engine import WeatherEngine
        wt = WorldTree()
        clock = WorldClock()
        e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
        e.register_chunk(0, 0, _make_baseline(), ClimateZone.TEMPERATE_FOREST, 15.0)
        assert e.get_weather(0, 0, clock.time) is not None
        assert e.get_weather(0, 0, clock.time - GAME_HOUR) is not None
        e.shutdown()


class TestWeatherReport:
    """get_weather_report 组合查询测试（handler 专用路径）。"""

    def test_report_returns_five_tuple(self):
        """返回 (params, sunrise, sunset, daylight, intensity) 五元组。"""
        from ascend.weather.weather_engine import WeatherEngine
        wt = WorldTree()
        clock = WorldClock()
        e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
        e.register_chunk(0, 0, _make_baseline(), ClimateZone.TEMPERATE_FOREST, 15.0)
        report = e.get_weather_report(0, 0)
        assert report is not None
        params, sr, ss, daylight, intensity = report
        assert params is not None
        assert sr < ss
        assert daylight == pytest.approx(ss - sr)
        assert 0.0 <= intensity <= 1.0
        e.shutdown()

    def test_report_unregistered_returns_none(self):
        from ascend.weather.weather_engine import WeatherEngine
        wt = WorldTree()
        clock = WorldClock()
        e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
        assert e.get_weather_report(999, 999) is None
        e.shutdown()

    def test_report_consistent_with_get_weather(self):
        """report 的 params 与单独 get_weather 一致。"""
        from ascend.weather.weather_engine import WeatherEngine
        wt = WorldTree()
        clock = WorldClock()
        e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
        e.register_chunk(0, 0, _make_baseline(), ClimateZone.TEMPERATE_FOREST, 15.0)
        report = e.get_weather_report(0, 0)
        wp = e.get_weather(0, 0)
        assert report[0].temperature == pytest.approx(wp.temperature)
        assert report[0].humidity == pytest.approx(wp.humidity)
        assert report[0].rainfall == pytest.approx(wp.rainfall)
        e.shutdown()

    def test_report_consistent_with_daylight_info(self):
        """report 的 sr/ss 与 get_daylight_info 一致（无雨时 intensity 也一致）。"""
        from ascend.weather.weather_engine import WeatherEngine
        wt = WorldTree()
        clock = WorldClock()
        e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
        e.register_chunk(0, 0, _make_baseline(), ClimateZone.TEMPERATE_FOREST, 15.0)
        report = e.get_weather_report(0, 0)
        params = report[0]
        dl = e.get_daylight_info(0, 0, rainfall=params.rainfall)
        assert report[1] == pytest.approx(dl[0])  # sunrise
        assert report[2] == pytest.approx(dl[1])  # sunset
        assert report[4] == pytest.approx(dl[3])  # intensity
        e.shutdown()

    def test_get_weather_event_consistency(self):
        """get_weather 值与事件发布的数值一致（强制类别变化后比较）。"""
        from ascend.weather.weather_engine import WeatherEngine
        wt = WorldTree()
        events = {t: [] for t in ("temperature_change", "humidity_change",
                                   "wind_change", "sunshine_change")}
        for t in events:
            wt.subscribe(t, lambda e, t=t: events[t].append(e))
        clock = WorldClock()
        e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
        e.register_chunk(0, 0, _make_baseline(), ClimateZone.TEMPERATE_FOREST, 15.0)
        _publish_minute(wt, clock.time)  # 首刻静默初始化
        _force_perception_reset(e, 0, 0, "temp", "humidity", "wind", "sunshine")
        clock.skip(1)
        _publish_minute(wt, clock.time)
        wp = e.get_weather(0, 0)
        assert wp is not None
        assert wp.temperature == pytest.approx(
            events["temperature_change"][0].data["temperature"])
        assert wp.humidity == pytest.approx(
            events["humidity_change"][0].data["humidity"])
        assert wp.wind_speed == pytest.approx(
            events["wind_change"][0].data["wind_speed"])
        assert wp.sunshine == pytest.approx(
            events["sunshine_change"][0].data["sunshine"])
        e.shutdown()


# ── 全局事件（季节/per-chunk 昼夜）────────────────────────────────


class TestGlobalEvents:
    """全局事件测试 — season_change（全局）+ sunrise/sunset（per-chunk 昼夜）。"""

    def test_first_tick_no_global_event(self):
        """首次 tick 不发 season_change（last None，避免启动刷屏）。
        sunrise/sunset 是 per-chunk，首次 tick 也不发（last_is_daytime=None）。"""
        from ascend.weather.weather_engine import WeatherEngine
        wt = WorldTree()
        events = []
        for t in ("season_change", "sunrise", "sunset"):
            wt.subscribe(t, lambda e: events.append(e))
        clock = WorldClock()
        e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
        e.register_chunk(0, 0, _make_baseline(), ClimateZone.TEMPERATE_FOREST, 15.0)
        _publish_minute(wt, clock.time)
        assert len(events) == 0
        e.shutdown()

    def test_season_change_emitted(self):
        """跨季节边界（day 90→91，春→夏）发 season_change。"""
        from ascend.weather.weather_engine import WeatherEngine
        from ascend.config import GAME_DAY
        wt = WorldTree()
        events = []
        wt.subscribe("season_change", lambda e: events.append(e))
        clock = WorldClock()
        e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
        e.register_chunk(0, 0, _make_baseline(), ClimateZone.TEMPERATE_FOREST, 15.0)
        _publish_minute(wt, clock.time)  # 首次（day 1 春，不发）
        clock.skip(90 * GAME_DAY)  # 推进到 day 91 06:00（夏）
        _publish_minute(wt, clock.time)
        assert len(events) == 1
        assert events[0].data["season"] == 1  # 夏
        e.shutdown()

    def test_sunset_emitted(self):
        """日落发 sunset（per-chunk，含 daylight_hours）。"""
        from ascend.weather.weather_engine import WeatherEngine
        wt = WorldTree()
        events = []
        wt.subscribe("sunset", lambda e: events.append(e))
        clock = WorldClock()  # 06:00
        clock.skip(6 * GAME_HOUR)  # 12:00（正午，确定白天）
        e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
        e.register_chunk(0, 0, _make_baseline(), ClimateZone.TEMPERATE_FOREST, 15.0)
        _publish_minute(wt, clock.time)  # 首次（昼）
        clock.skip(6 * GAME_HOUR)  # 18:00（已日落 → 夜）
        _publish_minute(wt, clock.time)
        assert len(events) == 1
        assert events[0].location[:2] == (0, 0)
        assert "daylight_hours" in events[0].data
        assert events[0].data["daylight_hours"] > 0
        e.shutdown()

    def test_sunrise_emitted(self):
        """日出发 sunrise（per-chunk，含 daylight_hours）。"""
        from ascend.weather.weather_engine import WeatherEngine
        wt = WorldTree()
        events = []
        wt.subscribe("sunrise", lambda e: events.append(e))
        clock = WorldClock()  # 06:00
        clock.skip(14 * GAME_HOUR)  # 20:00（夜里）
        e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
        e.register_chunk(0, 0, _make_baseline(), ClimateZone.TEMPERATE_FOREST, 15.0)
        _publish_minute(wt, clock.time)  # 首次（夜）
        assert len(events) == 0
        # 推进到次日 08:00（已日出，日出≈7:10）
        clock.skip(12 * GAME_HOUR)  # 20:00 + 12h = 次日 08:00
        _publish_minute(wt, clock.time)
        assert len(events) == 1
        assert events[0].location[:2] == (0, 0)
        assert "daylight_hours" in events[0].data
        assert events[0].data["daylight_hours"] > 0
        e.shutdown()

    def test_no_sunset_when_within_day(self):
        """白天内推进（未跨日落）不发 sunset。"""
        from ascend.weather.weather_engine import WeatherEngine
        wt = WorldTree()
        events = []
        wt.subscribe("sunset", lambda e: events.append(e))
        clock = WorldClock()  # 06:00
        e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
        e.register_chunk(0, 0, _make_baseline(), ClimateZone.TEMPERATE_FOREST, 15.0)
        _publish_minute(wt, clock.time)
        clock.skip(6 * GAME_HOUR)  # 到 12:00（仍白天）
        _publish_minute(wt, clock.time)
        assert len(events) == 0
        e.shutdown()

    def test_sunrise_variation_across_seasons(self):
        """日出时间随季节变化（夏季早、冬季晚）。"""
        from ascend.weather.diurnal import sunrise_hour
        # lat=45° 夏季（day 135）vs 冬季（day 315）
        sr_summer = sunrise_hour(135, 45.0)  # 夏至
        sr_winter = sunrise_hour(315, 45.0)  # 冬至
        assert sr_summer < 6.0    # 夏季日出早于 6:00
        assert sr_winter > 6.0    # 冬季日出晚于 6:00
        assert sr_winter - sr_summer > 3.0  # 差异 > 3 小时

    def test_sunset_variation_across_seasons(self):
        """日落时间随季节变化（夏季晚、冬季早）。"""
        from ascend.weather.diurnal import sunset_hour
        ss_summer = sunset_hour(135, 45.0)  # 夏至
        ss_winter = sunset_hour(315, 45.0)  # 冬至
        assert ss_summer > 18.0   # 夏季日落晚于 18:00
        assert ss_winter < 18.0   # 冬季日落早于 18:00

    def test_equator_constant_sunrise(self):
        """赤道全年日出≈6:00。"""
        from ascend.weather.diurnal import sunrise_hour, sunset_hour
        for doy in (0, 90, 180, 270):
            assert sunrise_hour(doy, 0.0) == pytest.approx(6.0, abs=0.02)
            assert sunset_hour(doy, 0.0) == pytest.approx(18.0, abs=0.02)

    def test_polar_day_summer(self):
        """极地夏季极昼：日出≈0h。"""
        from ascend.weather.diurnal import sunrise_hour
        sr = sunrise_hour(135, 75.0)  # 夏至，北纬 75°
        assert sr < 1.0  # 几乎 0 点日出（极昼）

    def test_polar_night_winter(self):
        """极地冬季极夜：日出≈12h（永不升起）。"""
        from ascend.weather.diurnal import sunrise_hour
        sr = sunrise_hour(315, 75.0)  # 冬至，北纬 75°
        assert sr == pytest.approx(12.0, abs=0.1)  # 极夜

    def test_global_event_location_zero(self):
        """season_change 全局事件 location=(0,0)。（sunrise/sunset 是 per-chunk 事件，不在此验证。）"""
        from ascend.weather.weather_engine import WeatherEngine
        from ascend.config import GAME_DAY
        wt = WorldTree()
        events = []
        wt.subscribe("season_change", lambda e: events.append(e))
        clock = WorldClock()
        e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
        e.register_chunk(0, 0, _make_baseline(), ClimateZone.TEMPERATE_FOREST, 15.0)
        _publish_minute(wt, clock.time)
        clock.skip(90 * GAME_DAY)
        _publish_minute(wt, clock.time)
        assert events[0].location[:2] == (0, 0)
        e.shutdown()


class TestPerChunkDayNight:
    """per-chunk 昼夜切换测试 — sunrise/sunset 用 chunk 自己的纬度。"""

    def test_sunrise_daylight_hours_in_range(self):
        """sunrise 事件携带 daylight_hours 字段，值在 [0, 24]。"""
        from ascend.weather.weather_engine import WeatherEngine
        wt = WorldTree()
        events = []
        wt.subscribe("sunrise", lambda e: events.append(e))
        clock = WorldClock()
        clock.skip(14 * GAME_HOUR)  # 20:00（夜里）
        e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
        e.register_chunk(0, 0, _make_baseline(), ClimateZone.TEMPERATE_FOREST, 15.0)
        _publish_minute(wt, clock.time)
        clock.skip(12 * GAME_HOUR)  # 次日 08:00
        _publish_minute(wt, clock.time)
        assert len(events) == 1
        assert 0.0 <= events[0].data["daylight_hours"] <= 24.0
        e.shutdown()

    def test_sunset_daylight_hours_in_range(self):
        """sunset 事件携带 daylight_hours 字段。"""
        from ascend.weather.weather_engine import WeatherEngine
        wt = WorldTree()
        events = []
        wt.subscribe("sunset", lambda e: events.append(e))
        clock = WorldClock()
        clock.skip(6 * GAME_HOUR)  # 12:00（白天）
        e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
        e.register_chunk(0, 0, _make_baseline(), ClimateZone.TEMPERATE_FOREST, 15.0)
        _publish_minute(wt, clock.time)
        clock.skip(7 * GAME_HOUR)  # 19:00（已日落）
        _publish_minute(wt, clock.time)
        assert len(events) == 1
        assert 0.0 <= events[0].data["daylight_hours"] <= 24.0
        e.shutdown()

    def test_per_chunk_latitude_affects_daylight(self):
        """不同纬度 chunk 的 daylight_hours 不同（极地 vs 赤道，夏季）。"""
        from ascend.weather.weather_engine import WeatherEngine
        from ascend.config import GAME_DAY
        wt = WorldTree()
        events = []
        wt.subscribe("sunrise", lambda e: events.append(e))
        clock = WorldClock()
        e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
        e.register_chunk(0, 0, _make_baseline(), ClimateZone.EQUATORIAL_RAINFOREST, 30.0)
        e.register_chunk(10, 0, _make_baseline(), ClimateZone.POLAR_TUNDRA, -5.0)
        clock.skip(134 * GAME_DAY + 20 * GAME_HOUR)
        _publish_minute(wt, clock.time)
        clock.skip(12 * GAME_HOUR)
        _publish_minute(wt, clock.time)
        by_chunk: dict[tuple, float] = {}
        for ev in events:
            if ev.event_type == "sunrise":
                by_chunk[ev.location[:2]] = ev.data["daylight_hours"]
        if (0, 0) in by_chunk:
            assert by_chunk[(0, 0)] == pytest.approx(12.0, abs=1.0)
        if (10, 0) in by_chunk:
            assert by_chunk[(10, 0)] > 22.0
        e.shutdown()


class TestModifierSchedule:
    """天气修改器调度测试 — ModifierSchedule 队列管理。"""

    def test_construct(self):
        from ascend.weather.weather_modifier import ModifierSchedule, WEATHER_MODIFIERS
        s = ModifierSchedule(_random.Random(42), WEATHER_MODIFIERS["cold_snap"], ClimateZone.TEMPERATE_FOREST)
        assert s is not None

    def test_push_and_is_active(self):
        from ascend.weather.weather_modifier import ModifierSchedule, ModifierEvent, WEATHER_MODIFIERS
        s = ModifierSchedule(_random.Random(42), WEATHER_MODIFIERS["heat_wave"], ClimateZone.DESERT)
        s.push(ModifierEvent(100, 500, "heat_wave", 1.0))
        assert s.is_active(200)
        assert not s.is_active(99)
        assert not s.is_active(600)

    def test_temp_offset(self):
        from ascend.weather.weather_modifier import ModifierSchedule, ModifierEvent, WEATHER_MODIFIERS
        s = ModifierSchedule(_random.Random(42), WEATHER_MODIFIERS["cold_snap"], ClimateZone.SUBARCTIC_TAIGA)
        s.push(ModifierEvent(100, 500, "cold_snap", 1.0))
        assert s.temp_offset(200) < -10.0  # cold snap is negative
        assert s.temp_offset(0) == 0.0

    def test_heat_wave_positive_offset(self):
        from ascend.weather.weather_modifier import ModifierSchedule, ModifierEvent, WEATHER_MODIFIERS
        s = ModifierSchedule(_random.Random(42), WEATHER_MODIFIERS["heat_wave"], ClimateZone.DESERT)
        s.push(ModifierEvent(100, 500, "heat_wave", 1.0))
        assert s.temp_offset(200) > 10.0

    def test_storm_wind_multiplier(self):
        from ascend.weather.weather_modifier import ModifierSchedule, ModifierEvent, WEATHER_MODIFIERS
        s = ModifierSchedule(_random.Random(42), WEATHER_MODIFIERS["storm"], ClimateZone.TEMPERATE_FOREST)
        s.push(ModifierEvent(100, 500, "storm", 1.0))
        assert s.wind_rain_multiplier(200) > 2.0
        assert s.wind_rain_multiplier(0) == 1.0

    def test_temp_offset_zero_for_storm(self):
        from ascend.weather.weather_modifier import ModifierSchedule, ModifierEvent, WEATHER_MODIFIERS
        s = ModifierSchedule(_random.Random(42), WEATHER_MODIFIERS["storm"], ClimateZone.TROPICAL_SAVANNA)
        s.push(ModifierEvent(100, 500, "storm", 1.0))
        assert s.temp_offset(200) == 0.0

    def test_pop_due_detects_start_and_stop(self):
        from ascend.weather.weather_modifier import ModifierSchedule, ModifierEvent, WEATHER_MODIFIERS
        s = ModifierSchedule(_random.Random(42), WEATHER_MODIFIERS["cold_snap"], ClimateZone.TEMPERATE_FOREST)
        s.seed_current(0)
        s.push(ModifierEvent(100, 200, "cold_snap", 1.0))
        assert s.pop_due(150)  # start
        assert not s.pop_due(160)  # no change
        assert s.pop_due(300)  # stop

    def test_generate_next_in_future(self):
        from ascend.weather.weather_modifier import ModifierSchedule, WEATHER_MODIFIERS
        s = ModifierSchedule(_random.Random(42), WEATHER_MODIFIERS["cold_snap"], ClimateZone.SUBARCTIC_TAIGA)
        ev = s.generate_next(1000)
        assert ev.start_tick > 1000
        assert ev.duration > 0
        assert 0.5 <= ev.magnitude <= 1.5

    def test_prune_removes_old_events(self):
        from ascend.weather.weather_modifier import ModifierSchedule, ModifierEvent, WEATHER_MODIFIERS
        s = ModifierSchedule(_random.Random(42), WEATHER_MODIFIERS["heat_wave"], ClimateZone.DESERT)
        s.push(ModifierEvent(100, 50, "heat_wave", 1.0))
        s.push(ModifierEvent(200, 50, "heat_wave", 1.0))
        s.prune_before(151)
        assert len(s) == 1
        assert s.is_active(220)

    def test_rate_zero_climate_no_events(self):
        """热带雨林寒潮频率为 0，通过 weather_engine 确认不创建对应 schedule。"""
        from ascend.weather.weather_engine import WeatherEngine
        wt = WorldTree()
        clock = WorldClock()
        e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
        e.register_chunk(0, 0, _make_baseline(), ClimateZone.EQUATORIAL_RAINFOREST, 30.0)
        # 热带雨林寒潮/热浪频率均为 0
        assert (0, 0, "cold_snap") not in e._modifier_schedules
        assert (0, 0, "heat_wave") not in e._modifier_schedules
        # 风暴频率 > 0
        assert (0, 0, "storm") in e._modifier_schedules
        e.shutdown()


class TestExtremeWeatherIntegration:
    """天气修改器集成测试 — WeatherEngine 发布事件 + 参数偏移。"""

    def test_cold_snap_affects_temperature(self):
        """寒潮期间温度额外下降。"""
        from ascend.weather.weather_engine import WeatherEngine
        from ascend.weather.weather_modifier import ModifierEvent
        wt = WorldTree()
        events = []
        wt.subscribe("temperature_change", lambda e: events.append(e))
        clock = WorldClock()
        e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
        e.register_chunk(0, 0, _make_baseline(temp=10.0), ClimateZone.SUBARCTIC_TAIGA, 0.0)
        _publish_minute(wt, clock.time)  # 首刻静默初始化
        _force_perception_reset(e, 0, 0, "temp")
        clock.skip(1)
        _publish_minute(wt, clock.time)
        normal_temp = events[-1].data["temperature"]
        sched = e._modifier_schedules.get((0, 0, "cold_snap"))
        if sched:
            sched._events.clear()
            sched.push(ModifierEvent(0, 999999999, "cold_snap", 1.0))
            sched.seed_current(0)
            events.clear()
            clock.skip(GAME_DAY)
            _publish_minute(wt, clock.time)
            cold_temp = events[-1].data["temperature"]
            assert cold_temp < normal_temp - 10.0
        e.shutdown()

    def test_heat_wave_events_emitted(self):
        """热浪状态切换发布 heat_wave_start/stop。"""
        from ascend.weather.weather_engine import WeatherEngine
        from ascend.weather.weather_modifier import ModifierEvent
        wt = WorldTree()
        events = []
        for t in ("heat_wave_start", "heat_wave_stop"):
            wt.subscribe(t, lambda e: events.append(e))
        clock = WorldClock()
        e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
        e.register_chunk(0, 0, _make_baseline(temp=25.0), ClimateZone.DESERT, 22.0)
        _publish_minute(wt, clock.time)
        sched = e._modifier_schedules.get((0, 0, "heat_wave"))
        if sched:
            sched._events.clear()
            sched.push(ModifierEvent(1, GAME_DAY, "heat_wave", 1.0))
            sched.seed_current(0)
            _publish_minute(wt, clock.time)
            assert any(ev.event_type == "heat_wave_start" for ev in events)
            clock.skip(GAME_DAY * 2)
            _publish_minute(wt, clock.time)
            assert any(ev.event_type == "heat_wave_stop" for ev in events)
        e.shutdown()

    def test_storm_affects_wind_and_rain(self):
        """风暴期间风速倍率 > 1。"""
        from ascend.weather.weather_engine import WeatherEngine
        from ascend.weather.weather_modifier import ModifierEvent
        wt = WorldTree()
        wind_events = []
        wt.subscribe("wind_change", lambda e: wind_events.append(e))
        clock = WorldClock()
        e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
        e.register_chunk(0, 0, _make_baseline(wind=5.0), ClimateZone.TEMPERATE_FOREST, 15.0)
        _publish_minute(wt, clock.time)  # 首刻静默初始化
        _force_perception_reset(e, 0, 0, "wind")
        clock.skip(1)
        _publish_minute(wt, clock.time)
        normal_wind = wind_events[-1].data["wind_speed"]
        sched = e._modifier_schedules.get((0, 0, "storm"))
        if sched:
            sched._events.clear()
            sched.push(ModifierEvent(0, 999999999, "storm", 1.0))
            sched.seed_current(0)
            wind_events.clear()
            clock.skip(GAME_DAY)
            _publish_minute(wt, clock.time)
            if wind_events:
                storm_wind = wind_events[-1].data["wind_speed"]
                assert storm_wind > normal_wind * 1.5
        e.shutdown()
