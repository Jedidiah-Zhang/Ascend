"""天气系统单元测试。

解析算架构（无快照）+ per-parameter 事件。按子模块分组。
"""

import pytest
import random as _random
from dataclasses import fields

from ascend.time import WorldClock
from ascend.config import GAME_HOUR, GAME_DAY, GAME_YEAR
from ascend.world_tree import WorldTree, Event, AffectedParty
from ascend.space import WeatherParams, ClimateZone, TILE_MAP_SIZE
from ascend.weather.events import (
    TemperatureChange, HumidityChange, WindChange, SunshineChange,
    PrecipitationStart, PrecipitationStop, SeasonChange, Sunrise, Sunset,
    ColdSnapStart, ColdSnapStop, HeatWaveStart, HeatWaveStop,
    StormStart, StormStop,
)


def _node_evaluate(node_id, parents, *, frame=0, instance=()):
    """测试用节点求值入口（直连注册表；生产路径见 WeatherEngine.evaluate_node）。"""
    from ascend.causal.world import ASCEND_MECHANISMS
    return ASCEND_MECHANISMS.evaluate(node_id, parents)


def _precip_threshold(annual_rainfall: float) -> float:
    from ascend.weather import mechanisms as m
    return _node_evaluate(
        m.PRECIPITATION_THRESHOLD, {m.ANNUAL_RAINFALL: annual_rainfall},
    )


def _calibrate_precip(
    signal: float, annual_rainfall: float, mean_intensity: float = 5.0,
    threshold: float | None = None,
) -> float:
    from ascend.weather import mechanisms as m
    if threshold is None:
        threshold = _precip_threshold(annual_rainfall)
    return _node_evaluate(m.INSTANT_PRECIPITATION_INTENSITY, {
        m.FIELD_PRECIPITATION_SIGNAL: signal,
        m.PRECIPITATION_THRESHOLD: threshold,
        m.MEAN_PRECIP_INTENSITY: mean_intensity,
    })


def _advance_weather(engine, game_time):
    """驱动 WeatherEngine 到指定时刻（声明更新点；替代旧事件总线驱动）。"""
    engine.advance(game_time)


def _make_baseline(temp=20.0, rain=800.0, wind=5.0, humidity=60.0,
                   alt=100.0, sun=12.0):
    """构造测试用年均基线 WeatherParams。"""
    return WeatherParams(temp, rain, sun, alt, humidity, wind)


def _find_dry_chunk(engine, now: int, annual: float = 800.0):
    """扫描一个"自然干燥、但高于阈下界"的 chunk（阈值窗口干预测试用）。

    要求：自然阈值下干燥（信号 < 阈 − 裕度），但信号 > 0.25（阈值下界）
    ——干预把阈值压到 0.25 后该 chunk 即变成降雨。
    """
    from ascend.weather import mechanisms as m
    from ascend.weather.field import CH_PRECIPITATION

    threshold = engine.evaluate_node(
        m.PRECIPITATION_THRESHOLD, {m.ANNUAL_RAINFALL: annual},
        frame=now, instance=(0, 0),
    )
    for cx in range(-8, 9):
        for cy in range(-8, 9):
            wx = (cx + 0.5) * TILE_MAP_SIZE
            wy = (cy + 0.5) * TILE_MAP_SIZE
            signal = engine._field.sample(CH_PRECIPITATION, wx, wy, now)
            if 0.27 < signal < threshold - 0.03:
                return (cx, cy)
    return None


def _force_perception_reset(engine, cx, cy, *params):
    """把指定参数的 last_*_tier 置为哨兵值，强制下一 tick 发布事件。

    引擎首刻静默初始化等级（不发事件），测试需要确定性事件时
    用此函数制造"等级变化"。params 取值 "temp"/"humidity"/"wind"/"sunshine"。
    """
    field = engine._fields[(cx, cy)]
    for p in params:
        setattr(field, f"last_{p}_tier", -1)


# ── constants ───────────────────────────────────────────────────────


class TestWeatherConstants:
    """天气常量测试。"""

    def test_module_importable(self):
        from ascend import config
        assert config is not None

    def test_field_grid_size_positive(self):
        from ascend.config import WEATHER_FIELD_GRID_SIZE
        assert WEATHER_FIELD_GRID_SIZE > 0

    def test_tile_noise_params_positive(self):
        from ascend.config import (
            WEATHER_FIELD_TILE_NOISE_WAVELENGTH,
            WEATHER_FIELD_TILE_NOISE_SCALE,
        )
        assert WEATHER_FIELD_TILE_NOISE_WAVELENGTH > 0
        assert 0 < WEATHER_FIELD_TILE_NOISE_SCALE < 1

    def test_texture_wavelengths_positive(self):
        from ascend.config import (
            TEXTURE_WAVELENGTH_TEMP, TEXTURE_WAVELENGTH_WIND,
            TEXTURE_WAVELENGTH_PRECIP,
        )
        assert TEXTURE_WAVELENGTH_TEMP > TEXTURE_WAVELENGTH_WIND > \
            TEXTURE_WAVELENGTH_PRECIP > 0

    def test_texture_octaves_positive(self):
        from ascend.config import (
            TEXTURE_OCTAVES, TEXTURE_PERSISTENCE, TEXTURE_LACUNARITY,
            TEXTURE_DRIFT_RATE,
        )
        assert TEXTURE_OCTAVES >= 2
        assert 0 < TEXTURE_PERSISTENCE < 1
        assert TEXTURE_LACUNARITY > 1
        assert TEXTURE_DRIFT_RATE > 0

    def test_feature_block_params_positive(self):
        from ascend.config import (
            FEATURE_BLOCK_SIZE, FEATURE_MAX_RADIUS,
        )
        assert FEATURE_BLOCK_SIZE > 0
        assert FEATURE_MAX_RADIUS > 0
        assert FEATURE_BLOCK_SIZE / FEATURE_MAX_RADIUS >= 2

    def test_climate_proxy_params_positive(self):
        from ascend.config import (
            CLIMATE_PROXY_TEMP_WAVELENGTH, CLIMATE_PROXY_RAIN_WAVELENGTH,
            CLIMATE_PROXY_OCTAVES,
        )
        assert CLIMATE_PROXY_TEMP_WAVELENGTH > CLIMATE_PROXY_RAIN_WAVELENGTH > 0
        assert CLIMATE_PROXY_OCTAVES >= 1

    def test_precip_calibration_params(self):
        from ascend.config import (
            PRECIP_SIGNAL_MIN, PRECIP_SIGNAL_MAX,
            PRECIP_THRESHOLD_DRY, PRECIP_THRESHOLD_WET,
            PRECIP_ANNUAL_DRY, PRECIP_ANNUAL_WET,
            PRECIP_INTENSITY_SCALE,
        )
        assert 0 <= PRECIP_SIGNAL_MIN < PRECIP_SIGNAL_MAX
        assert PRECIP_THRESHOLD_WET < PRECIP_THRESHOLD_DRY
        assert PRECIP_ANNUAL_DRY < PRECIP_ANNUAL_WET
        assert PRECIP_INTENSITY_SCALE > 0

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

    def test_tier_boundaries_defined(self):
        from ascend.config import (
            TEMP_TIER_BOUNDARIES, HUMIDITY_TIER_BOUNDARIES,
            WIND_TIER_BOUNDARIES, SUNSHINE_TIER_BOUNDARIES,
        )
        assert len(TEMP_TIER_BOUNDARIES) >= 4
        assert len(HUMIDITY_TIER_BOUNDARIES) >= 2
        assert len(WIND_TIER_BOUNDARIES) >= 3
        assert len(SUNSHINE_TIER_BOUNDARIES) >= 3
        # 所有边界值应为 float
        for boundaries in (TEMP_TIER_BOUNDARIES, HUMIDITY_TIER_BOUNDARIES,
                           WIND_TIER_BOUNDARIES, SUNSHINE_TIER_BOUNDARIES):
            assert isinstance(boundaries[0], float)
        # 阈值严格升序
        for boundaries in (TEMP_TIER_BOUNDARIES, HUMIDITY_TIER_BOUNDARIES,
                           WIND_TIER_BOUNDARIES, SUNSHINE_TIER_BOUNDARIES):
            for i in range(1, len(boundaries)):
                assert boundaries[i] > boundaries[i - 1]

    def test_humidity_scales_positive(self):
        from ascend.config import (
            HUMIDITY_DIURNAL_SCALE, HUMIDITY_SEASONAL_SCALE,
        )
        assert HUMIDITY_DIURNAL_SCALE > 0
        assert HUMIDITY_SEASONAL_SCALE > 0

    def test_sunshine_perturb_scale_positive(self):
        from ascend.config import SUNSHINE_PERTURB_SCALE
        assert SUNSHINE_PERTURB_SCALE > 0


# ── 事件契约 ───────────────────────────────────────────────────────


class TestWeatherEventContracts:
    """天气事件 data 契约：as_dict 键 == dataclass 字段，event_type 唯一非空。"""

    SAMPLES: dict[type, dict] = {
        TemperatureChange: dict(
            temperature=25.0, prev_tier=3, tier=4, season=1, time_of_day=36000,
        ),
        HumidityChange: dict(
            humidity=70.0, prev_tier=2, tier=3, time_of_day=36000,
        ),
        WindChange: dict(
            wind_speed=3.5, prev_tier=0, tier=1,
            wind_dir_x=0.5, wind_dir_y=-0.8, time_of_day=36000,
        ),
        SunshineChange: dict(
            sunshine=12.0, prev_tier=2, tier=3, season=1, time_of_day=36000,
        ),
        PrecipitationStart: dict(
            precip_type="rain", intensity=2.5, time_of_day=36000,
            chunks=[[0, 0], [1, 1]],
        ),
        PrecipitationStop: dict(time_of_day=36000, chunks=[[0, 0]]),
        SeasonChange: dict(season=1, time_of_day=36000),
        Sunrise: dict(time_of_day=36000, daylight_hours=10.5),
        Sunset: dict(time_of_day=36000, daylight_hours=10.5),
        ColdSnapStart: dict(temperature_offset=-12.0, time_of_day=36000),
        HeatWaveStart: dict(temperature_offset=14.0, time_of_day=36000),
        StormStart: dict(
            wind_multiplier=2.0, rain_multiplier=1.5, time_of_day=36000,
        ),
        ColdSnapStop: dict(time_of_day=36000),
        HeatWaveStop: dict(time_of_day=36000),
        StormStop: dict(time_of_day=36000),
    }

    @pytest.mark.parametrize("cls", list(SAMPLES))
    def test_contract(self, cls):
        """as_dict 键 == 字段集合，样本值完整往返。"""
        ev = cls(**self.SAMPLES[cls])
        d = ev.as_dict()
        assert set(d) == {f.name for f in fields(cls)}
        for name, value in self.SAMPLES[cls].items():
            assert d[name] == value

    def test_all_feature_type_events_covered(self):
        """FEATURE_TYPES 注册表的 start/stop 事件类全部在契约样本中。

        全局 event_type 唯一性由 test_event_contracts.py 统一断言。
        """
        from ascend.weather.features import FEATURE_TYPES
        for config in FEATURE_TYPES.values():
            if config.start_event_cls is not None:
                assert config.start_event_cls in self.SAMPLES
            if config.stop_event_cls is not None:
                assert config.stop_event_cls in self.SAMPLES

    def test_as_dict_is_json_serializable(self):
        """所有事件 data 可被 json.dumps 直接序列化（EventBridge 契约）。"""
        import json
        for cls in self.SAMPLES:
            d = cls(**self.SAMPLES[cls]).as_dict()
            json.dumps(d, ensure_ascii=False)


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


# ── texture field ─────────────────────────────────────────────────


class TestTextureField:
    """纹理分量测试（多通道多八度噪声 + 漂移）。"""

    def test_construct_default(self):
        from ascend.weather.atmosphere import TextureField
        assert TextureField() is not None

    def test_sample_in_range(self):
        from ascend.weather.atmosphere import TextureField, CH_TEMPERATURE
        f = TextureField(seed=42)
        for x in (0.0, 1000.0, 5000.0):
            for y in (0.0, 2000.0, 8000.0):
                assert -1.0 <= f.sample(CH_TEMPERATURE, x, y, 0) <= 1.0

    def test_sample_deterministic(self):
        from ascend.weather.atmosphere import TextureField, CH_PRECIP
        f = TextureField(seed=42)
        assert f.sample(CH_PRECIP, 1500.0, 2500.0, 10000) == \
            f.sample(CH_PRECIP, 1500.0, 2500.0, 10000)

    def test_spatial_continuity(self):
        from ascend.weather.atmosphere import TextureField, CH_TEMPERATURE
        f = TextureField(seed=42)
        v0 = f.sample(CH_TEMPERATURE, 1000.0, 1000.0, 0)
        for dx, dy in [(100, 0), (0, 100), (50, 50)]:
            assert abs(f.sample(CH_TEMPERATURE, 1000.0 + dx, 1000.0 + dy, 0) - v0) < 0.3

    def test_temporal_continuity(self):
        from ascend.weather.atmosphere import TextureField, CH_TEMPERATURE
        f = TextureField(seed=42)
        v0 = f.sample(CH_TEMPERATURE, 1000.0, 1000.0, 0)
        assert abs(f.sample(CH_TEMPERATURE, 1000.0, 1000.0, 100) - v0) < 0.01

    def test_different_seeds_differ(self):
        from ascend.weather.atmosphere import TextureField, CH_TEMPERATURE
        a, b = TextureField(seed=0), TextureField(seed=1)
        diffs = [
            a.sample(CH_TEMPERATURE, x, y, 0) != b.sample(CH_TEMPERATURE, x, y, 0)
            for x in (0.0, 1500.0, 3000.0)
            for y in (0.0, 2500.0, 5000.0)
        ]
        assert any(diffs)

    def test_drift_over_long_time(self):
        from ascend.weather.atmosphere import TextureField, CH_PRECIP
        f = TextureField(seed=42)
        assert f.sample(CH_PRECIP, 1500.0, 2500.0, 0) != \
            f.sample(CH_PRECIP, 1500.0, 2500.0, 100_000_000)

    def test_wind_vector_unit_length(self):
        import math
        from ascend.weather.atmosphere import TextureField
        f = TextureField(seed=42)
        for t in (0, 10000, 1_000_000):
            wx, wy = f.wind_vector(t)
            assert math.hypot(wx, wy) == pytest.approx(1.0, abs=1e-6)

    def test_wind_vector_changes_over_time(self):
        from ascend.weather.atmosphere import TextureField
        f = TextureField(seed=42)
        assert f.wind_vector(0) != f.wind_vector(100_000_000)

    def test_wind_vector_at_unit_length_and_local(self):
        """空间风向：单位长度 + 相邻位置接近 + 随时间演化。"""
        import math
        from ascend.weather.atmosphere import TextureField
        f = TextureField(seed=42)
        for pos in ((0.0, 0.0), (1000.0, 500.0)):
            wx, wy = f.wind_vector_at(*pos, 10000)
            assert math.hypot(wx, wy) == pytest.approx(1.0, abs=1e-6)
        v0 = f.wind_vector_at(1000.0, 1000.0, 0)
        for dx, dy in [(200, 0), (0, 200)]:
            v1 = f.wind_vector_at(1000.0 + dx, 1000.0 + dy, 0)
            assert math.hypot(v0[0] - v1[0], v0[1] - v1[1]) < 0.5
        assert f.wind_vector_at(1000.0, 1000.0, 0) != \
            f.wind_vector_at(1000.0, 1000.0, 100_000_000)


# ── 降水校准 ──────────────────────────────────────────────────────


class TestPrecipCalibration:
    """降水信号 → 阈值/强度节点求值（唯一实现 = 注册表方程）。"""

    def test_threshold_dry_high_wet_low(self):
        assert _precip_threshold(50.0) == pytest.approx(0.55)
        assert _precip_threshold(3500.0) == pytest.approx(0.25)
        assert _precip_threshold(800.0) < _precip_threshold(100.0)

    def test_threshold_clamped_outside_range(self):
        assert _precip_threshold(0.0) == pytest.approx(0.55)
        assert _precip_threshold(100000.0) == pytest.approx(0.25)

    def test_calibrate_below_threshold_zero(self):
        assert _calibrate_precip(0.1, 100.0, 5.0) == 0.0

    def test_calibrate_above_threshold_scaled(self):
        # 阈值 0.25（湿润），信号 0.5 → 超阈 0.25 × 2 × 10 = 5.0
        assert _calibrate_precip(0.5, 3500.0, 10.0) == pytest.approx(5.0)

    def test_calibrate_signal_capped(self):
        # 信号超 PRECIP_SIGNAL_MAX 时按饱和值计
        assert _calibrate_precip(5.0, 3500.0, 10.0) == \
            _calibrate_precip(1.2, 3500.0, 10.0)

    def test_calibrate_precomputed_threshold(self):
        th = _precip_threshold(800.0)
        assert _calibrate_precip(0.6, 800.0, 5.0, threshold=th) == \
            _calibrate_precip(0.6, 800.0, 5.0)


# ── 特征场（FeatureField）────────────────────────────────────────


class TestFeatureField:
    """特征分量测试 — 确定性 / 类型约束 / 注入核。"""

    def test_sample_deterministic(self):
        from ascend.weather.features import FeatureField
        a, b = FeatureField(seed=42), FeatureField(seed=42)
        t = 10000000
        assert a.sample_temperature_offset(1000.0, 2000.0, t) == \
            b.sample_temperature_offset(1000.0, 2000.0, t)
        assert a.sample_precip_boost(1000.0, 2000.0, t) == \
            b.sample_precip_boost(1000.0, 2000.0, t)
        assert a.sample_multiplier(1000.0, 2000.0, t) == \
            b.sample_multiplier(1000.0, 2000.0, t)

    def test_different_seeds_differ(self):
        """不同种子的核布局不同：同块同段的派生核（类型/出生/中心）不同。"""
        from ascend.weather.features import FeatureField
        a, b = FeatureField(seed=0), FeatureField(seed=1)
        layout_a = [(c.type_name, c.born_tick, round(c.center_x, 1),
                     round(c.center_y, 1), c.radius) for c in a._segment(0, 0, 0)]
        layout_b = [(c.type_name, c.born_tick, round(c.center_x, 1),
                     round(c.center_y, 1), c.radius) for c in b._segment(0, 0, 0)]
        assert layout_a != layout_b

    def test_multiplier_min_one(self):
        """无核时倍率恒为 1.0（乘法中性元）。"""
        from ascend.weather.features import FeatureField
        f = FeatureField(seed=42)
        for t in (0, 100000, 10000000):
            assert f.sample_multiplier(0.0, 0.0, t) >= 1.0

    def test_core_id_stable(self):
        """核身份稳定：同一种子/坐标/段 → 同 core_id。"""
        from ascend.weather.features import FeatureField
        f = FeatureField(seed=42)
        t = 10000000
        cores = f.cores_overlapping(0, 0, 2000, 2000, t)
        if cores:
            c = cores[0]
            assert c.core_id.startswith("b:")
            again = FeatureField(seed=42).cores_overlapping(0, 0, 2000, 2000, t)
            assert c.core_id in {x.core_id for x in again}

    def test_segment_snapshot_missing_returns_none(self):
        """只读查询：段未生成返回 None（不触发惰性生成）。"""
        from ascend.weather.features import FeatureField
        f = FeatureField(seed=42)
        assert f.segment_snapshot(0, 0, 0) is None, "未生成段应返回 None"

    def test_segment_snapshot_returns_copy(self):
        """已生成段返回副本：修改副本不影响内部时间线。"""
        from ascend.weather.features import FeatureField
        f = FeatureField(seed=42)
        cores = f._segment(0, 0, 0)
        snap = f.segment_snapshot(0, 0, 0)
        assert snap is not None and len(snap) == len(cores)
        snap.clear()
        assert len(f.segment_snapshot(0, 0, 0)) == len(cores), "内部时间线不应被改动"


    def test_inject_core_immediate_effect(self):
        """注入核立即满强度（no_ramp），采样与自然核同路径。"""
        from ascend.weather.features import FeatureField
        from ascend.config import GAME_YEAR
        f = FeatureField(seed=42)
        t = 5000000
        assert f.sample_multiplier(100.0, 100.0, t) == 1.0
        f.inject_core(
            0, 0, "storm", center_x=100.0, center_y=100.0, radius=3000.0,
            born_tick=t, duration=GAME_YEAR,
        )
        assert f.sample_multiplier(100.0, 100.0, t) > 2.0
        assert f.remove_injected(0, 0, "storm") is True
        assert f.sample_multiplier(100.0, 100.0, t) == 1.0

    def test_synthesis_independent_of_insertion_order(self):
        """同一核集合、不同插入顺序 → 合成值逐位一致（#52 稳定求和）。

        判别力前提：该量级组合在未排序时 float 求和确实分叉
        （1e16 与 1 相加丢小项），因此本测试不是恒真。
        """
        from ascend.weather.features import FEATURE_TYPES, FeatureField

        base = FEATURE_TYPES["cold_snap"].base_intensity
        magnitudes = (1.0, 1.0, 1e16)
        raw_a = 0.0
        for magnitude in magnitudes:
            raw_a += base * magnitude
        raw_b = 0.0
        for magnitude in (magnitudes[2], magnitudes[0], magnitudes[1]):
            raw_b += base * magnitude
        assert raw_a != raw_b, "前提：未排序时该组合确实分叉"

        def build(order: tuple[int, ...]) -> FeatureField:
            field = FeatureField(seed=42)
            for index in order:
                field.inject_core(
                    index, 0, "cold_snap",
                    center_x=100.0, center_y=100.0, radius=3000.0,
                    born_tick=0, duration=None,
                    magnitude=magnitudes[index],
                )
            return field

        t = 5000000
        a = build((0, 1, 2))
        b = build((2, 0, 1))
        assert a.sample_temperature_offset(100.0, 100.0, t) == \
            b.sample_temperature_offset(100.0, 100.0, t), \
            "合成顺序必须与插入/恢复顺序无关（规范顺序）"

    def test_inject_unknown_type_raises(self):
        from ascend.weather.features import FeatureField
        f = FeatureField(seed=42)
        with pytest.raises(ValueError):
            f.inject_core(0, 0, "tornado", center_x=0.0, center_y=0.0,
                          radius=100.0, born_tick=0, duration=100)

    # ── 注入核持久化（P4：W_t 的不可重算部分）──────────────────

    def test_injected_cores_round_trip(self):
        """persist_injected → restore_injected 逐字段一致。"""
        from ascend.weather.features import FeatureField
        source = FeatureField(seed=42)
        source.inject_core(0, 0, "storm", center_x=100.0, center_y=200.0,
                           radius=500.0, born_tick=10, duration=None)
        source.inject_core(1, 2, "front", center_x=1.0, center_y=2.0,
                           radius=300.0, born_tick=20, duration=50,
                           vel_x=0.5, vel_y=0.3)
        payload = source.persist_injected()

        target = FeatureField(seed=42)
        assert target.restore_injected(payload) == 2
        assert target.persist_injected() == payload
        core = target.get_injected(0, 0, "storm")
        assert core.no_ramp is True and core.duration is None
        assert target.get_injected(1, 2, "front").vel_x == 0.5

    def test_restore_injected_replaces_existing_set(self):
        """恢复是整体替换：残留注入核不得存活（存档是唯一事实源）。"""
        from ascend.weather.features import FeatureField
        source = FeatureField(seed=42)
        source.inject_core(0, 0, "storm", center_x=0.0, center_y=0.0,
                           radius=100.0, born_tick=0, duration=None)
        target = FeatureField(seed=42)
        target.inject_core(1, 1, "cold_snap", center_x=0.0, center_y=0.0,
                           radius=100.0, born_tick=0, duration=None)
        target.restore_injected(source.persist_injected())
        assert target.get_injected(0, 0, "storm") is not None
        assert target.get_injected(1, 1, "cold_snap") is None

    @pytest.mark.parametrize("field_name,value", [
        ("core_id", "inj:9:9:storm"),
        ("type_name", "tsunami"),
        ("chunk", [0, 0, 0]),
        ("born_tick", -1),
        ("duration", 0),
        ("radius", float("nan")),
        ("magnitude", float("inf")),
    ])
    def test_restore_injected_rejects_bad_field(self, field_name, value):
        from ascend.weather.features import FeatureField
        source = FeatureField(seed=42)
        source.inject_core(0, 0, "storm", center_x=0.0, center_y=0.0,
                           radius=100.0, born_tick=5, duration=None)
        payload = source.persist_injected()
        payload[0][field_name] = value
        with pytest.raises(ValueError):
            FeatureField(seed=42).restore_injected(payload)

    def test_restore_injected_rejects_unknown_field(self):
        from ascend.weather.features import FeatureField
        source = FeatureField(seed=42)
        source.inject_core(0, 0, "storm", center_x=0.0, center_y=0.0,
                           radius=100.0, born_tick=5, duration=None)
        payload = source.persist_injected()
        payload[0]["ghost"] = 1
        with pytest.raises(ValueError, match="未知字段"):
            FeatureField(seed=42).restore_injected(payload)

    def test_restore_injected_rejects_non_list(self):
        from ascend.weather.features import FeatureField
        with pytest.raises(ValueError, match="列表"):
            FeatureField(seed=42).restore_injected({"a": 1})

    def test_restore_injected_is_all_or_nothing(self):
        """任一核非法 → 整体拒绝，已有注入核不被清空。"""
        from ascend.weather.features import FeatureField
        source = FeatureField(seed=42)
        source.inject_core(0, 0, "storm", center_x=0.0, center_y=0.0,
                           radius=100.0, born_tick=5, duration=None)
        payload = source.persist_injected()
        payload.append({"chunk": [1, 1]})       # 缺字段
        target = FeatureField(seed=42)
        target.inject_core(2, 2, "front", center_x=0.0, center_y=0.0,
                           radius=100.0, born_tick=0, duration=None)
        with pytest.raises(ValueError):
            target.restore_injected(payload)
        assert target.get_injected(2, 2, "front") is not None, \
            "拒绝时必须保持原状，不留半成品"

    def test_persist_injected_is_json_safe(self):
        import json
        from ascend.weather.features import FeatureField
        f = FeatureField(seed=42)
        f.inject_core(0, 0, "storm", center_x=1.0, center_y=2.0,
                      radius=100.0, born_tick=5, duration=None)
        payload = f.persist_injected()
        assert json.loads(json.dumps(payload)) == payload

    def test_front_has_no_event_classes(self):
        """锋面是纯降水带（带形），无 start/stop 事件类。"""
        from ascend.weather.features import FEATURE_TYPES, T_FRONT
        assert FEATURE_TYPES[T_FRONT].start_event_cls is None
        assert FEATURE_TYPES[T_FRONT].stop_event_cls is None

    def test_feature_config_rates_cover_all_zones(self):
        """所有特征类型的 rates 覆盖全部气候带（0 = 不发生）。"""
        from ascend.weather.features import FEATURE_TYPES
        from ascend.space import ClimateZone
        for cfg in FEATURE_TYPES.values():
            assert set(cfg.rates) == set(ClimateZone)


# ── 统一天气场（UnifiedWeatherField）─────────────────────────────


class TestUnifiedWeatherField:
    """统一天气场测试 — C1 连续 / tile 噪声 / 批量采样。"""

    def test_sample_deterministic(self):
        from ascend.weather.field import UnifiedWeatherField
        f = UnifiedWeatherField(seed=42)
        assert f.sample("temperature", 1500.0, 2500.0, 10000) == \
            f.sample("temperature", 1500.0, 2500.0, 10000)

    def test_spatial_continuity(self):
        """相邻位置值接近（C1 插值平滑，含特征核）。"""
        from ascend.weather.field import UnifiedWeatherField
        f = UnifiedWeatherField(seed=42)
        t = 10000000
        v0 = f.sample("temperature", 1000.0, 1000.0, t)
        for dx, dy in [(100, 0), (0, 100), (50, 50)]:
            assert abs(f.sample("temperature", 1000.0 + dx, 1000.0 + dy, t) - v0) < 0.3

    def test_c1_smooth_across_grid_boundary(self):
        """跨 1km 网格边界采样连续（无折痕）。"""
        from ascend.weather.field import UnifiedWeatherField
        f = UnifiedWeatherField(seed=42)
        t = 10000000
        for channel in ("temperature", "precip", "wind"):
            v_left = f.sample(channel, 999.9, 500.0, t)
            v_right = f.sample(channel, 1000.1, 500.0, t)
            assert abs(v_left - v_right) < 0.2, channel

    def test_temporal_continuity(self):
        from ascend.weather.field import UnifiedWeatherField
        f = UnifiedWeatherField(seed=42)
        v0 = f.sample("temperature", 1000.0, 1000.0, 0)
        assert abs(f.sample("temperature", 1000.0, 1000.0, 100) - v0) < 0.01

    def test_different_seeds_differ(self):
        from ascend.weather.field import UnifiedWeatherField
        a, b = UnifiedWeatherField(seed=0), UnifiedWeatherField(seed=1)
        diffs = [
            a.sample("precip", x, y, 0) != b.sample("precip", x, y, 0)
            for x in (0.0, 1500.0, 3000.0)
            for y in (0.0, 2500.0, 5000.0)
        ]
        assert any(diffs)

    def test_sample_grid_matches_single(self):
        """批量栅格采样与逐点采样同路径同值。"""
        from ascend.weather.field import UnifiedWeatherField
        f = UnifiedWeatherField(seed=42)
        t = 10000000
        grid = f.sample_grid("temperature", 0.0, 0.0, 3, 2, t)
        assert len(grid) == 6
        for j in range(2):
            for i in range(3):
                x = (i + 0.5) * 1000.0
                y = (j + 0.5) * 1000.0
                assert grid[j * 3 + i] == pytest.approx(
                    f.sample("temperature", x, y, t), abs=1e-9)

    def test_precip_signal_nonnegative(self):
        from ascend.weather.field import UnifiedWeatherField
        f = UnifiedWeatherField(seed=42)
        for t in (0, 100000, 10000000):
            assert f.sample("precip", 500.0, 500.0, t) >= 0.0

    def test_unknown_channel_raises(self):
        from ascend.weather.field import UnifiedWeatherField
        f = UnifiedWeatherField(seed=42)
        with pytest.raises(KeyError):
            f.sample("humidity_extra", 0.0, 0.0, 0)


# ── 区域跟踪器（RegionTracker）───────────────────────────────────


class _ScriptedField:
    """脚本化降水场替身：sample 按 (x, y, t) 返回调用方给定信号。"""

    def __init__(self, fn) -> None:
        self._fn = fn

    def sample(self, channel, x, y, t):
        return self._fn(x, y, t)


class TestRegionTracker:
    """区域观测器测试 — 纯函数派生 / 连通域 / 出现消失 / 无隐藏状态。"""

    _WET = (3000.0, 10.0)
    _DOMAIN = tuple(
        (cx, cy) for cx in range(-2, 3) for cy in range(-2, 3)
    )

    def _make_tracker(self, signal, climate=None):
        from ascend.weather import RegionTracker
        return RegionTracker(
            _ScriptedField(signal),
            evaluate=_node_evaluate,
            climate_baseline=climate
            if climate is not None else (lambda cx, cy: self._WET),
        )

    @staticmethod
    def _by_minute(per_minute):
        """按游戏分钟返回信号（t // GAME_MINUTE 索引）。"""
        from ascend.config import GAME_MINUTE
        return lambda x, y, t: per_minute[
            (t // GAME_MINUTE) % len(per_minute)
        ]

    def test_region_start_and_stop(self):
        """信号开启 → start（含质心/强度/域成员）；关闭 → stop。"""
        tr = self._make_tracker(self._by_minute([0.0, 1.0, 0.0]))
        starts = tr.observe(1 * 120, self._DOMAIN)
        assert [e.kind for e in starts] == ["start"]
        assert starts[0].intensity > 0.0
        assert starts[0].cells
        assert starts[0].chunks == tuple(sorted(starts[0].chunks))
        assert set(starts[0].chunks) == set(self._DOMAIN), "全湿 → 单一连通域"
        stops = tr.observe(2 * 120, self._DOMAIN)
        assert [e.kind for e in stops] == ["stop"]

    def test_observe_is_pure(self):
        """同一 (now, 域) 重复观测结果相同；新实例同样结果（无隐藏状态）。"""
        tr = self._make_tracker(self._by_minute([0.0, 1.0]))
        first = tr.observe(2 * 120, self._DOMAIN)
        assert tr.observe(2 * 120, self._DOMAIN) == first
        fresh = self._make_tracker(self._by_minute([0.0, 1.0]))
        assert fresh.observe(2 * 120, self._DOMAIN) == first

    def test_steady_region_no_repeat(self):
        """区域持续（前后帧重叠）→ 不发 start。"""
        tr = self._make_tracker(self._by_minute([1.0]))
        events = tr.observe(5 * 120, self._DOMAIN)
        assert not any(e.kind == "start" for e in events)

    def test_connected_components_split(self):
        """域内两块不连通湿区 → 两个区域（各自 start）。"""
        def signal(x, y, t):
            cx = int(x // TILE_MAP_SIZE)
            wet = (t // 120) % 2 == 1
            return 1.0 if wet and cx in (-2, 2) else 0.0

        tr = self._make_tracker(signal)
        starts = [e for e in tr.observe(1 * 120, self._DOMAIN)
                  if e.kind == "start"]
        assert len(starts) == 2
        assert {e.center_chunk[0] for e in starts} == {-2, 2}

    def test_domain_empty_no_events(self):
        tr = self._make_tracker(self._by_minute([1.0]))
        assert tr.observe(10000000, ()) == []

    def test_missing_climate_rejected(self):
        """未接入气候基线：observe 显式拒绝（不静默空转）。"""
        from ascend.weather import RegionTracker
        tr = RegionTracker(
            _ScriptedField(lambda x, y, t: 1.0), evaluate=_node_evaluate,
        )
        with pytest.raises(RuntimeError, match="气候基线"):
            tr.observe(10000000, ((0, 0),))

    def test_dry_climate_never_rains(self):
        """极干旱校准（高阈）→ 信号不越阈，无 start。"""
        tr = self._make_tracker(
            lambda x, y, t: 0.3, climate=lambda cx, cy: (10.0, 2.0),
        )
        events = tr.observe(2 * 120, ((0, 0),))
        assert not any(e.kind == "start" for e in events)

    def test_loading_set_independent(self):
        """观测只依赖 (now, 域)：气候查询与加载集无关（无注册即可观测）。"""
        calls: list[tuple[int, int]] = []

        def climate(cx, cy):
            calls.append((cx, cy))
            return self._WET

        tr = self._make_tracker(self._by_minute([0.0, 1.0]), climate=climate)
        domain = ((0, 0), (1, 0))
        events = tr.observe(1 * 120, domain)
        assert [e.kind for e in events] == ["start"]
        assert set(calls) >= set(domain), "域内每 chunk 均经气候查询"

    def test_domain_move_semantics(self):
        """域移动：走入既有雨带 → start；走出 → stop（前后帧各用当时的域）。"""
        def signal(x, y, t):
            cx = int(x // TILE_MAP_SIZE)
            return 1.0 if cx in (0, 1) else 0.0  # (0,*)/(1,*) 列恒降雨

        tr = self._make_tracker(signal)
        dry_window = ((5, 5),)
        wet_window = ((0, 0),)
        # 首帧：previous 缺省 = 当前域 → 不产生伪事件（无隐藏状态）
        assert tr.observe(2 * 120, wet_window) == []
        moved_in = tr.observe(3 * 120, wet_window, previous_domain=dry_window)
        assert [e.kind for e in moved_in] == ["start"]
        moved_out = tr.observe(4 * 120, dry_window, previous_domain=wet_window)
        assert [e.kind for e in moved_out] == ["stop"]

    def test_diff_overlap_split_merge(self):
        """前后帧重叠/分裂/合并 → 无事件；全新/消失 → start/stop。"""
        tr = self._make_tracker(self._by_minute([1.0]))
        a = {(0, 0), (1, 0)}
        b = {(0, 1)}
        previous = [a | b]
        assert tr._diff([a, b], previous, 0) == []
        assert tr._diff([a | b], [a, b], 0) == []
        mixed = tr._diff([{(5, 5)}], previous, 0)
        assert [e.kind for e in mixed] == ["stop", "start"]
        stops = tr._diff([a], [a, {(9, 9)}], 0)
        assert [e.kind for e in stops] == ["stop"]

    def test_minute_sweep_has_start_and_stop(self):
        """分钟扫描：降雨区出现与消失都会被通报（含更替）。"""
        tr = self._make_tracker(self._by_minute([1.0, 0.0]))
        seen = set()
        for step in range(4):
            for event in tr.observe((step + 2) * 120, self._DOMAIN):
                seen.add(event.kind)
        assert seen == {"start", "stop"}


# ── weather_field ──────────────────────────────────────────────────


class TestWeatherField:
    """chunk 天气状态容器测试。"""

    def test_construct(self):
        from ascend.weather.weather_field import WeatherField
        wf = WeatherField(0, 0, baseline="bl")
        assert wf.chunk_x == 0
        assert wf.chunk_y == 0
        assert wf.baseline == "bl"
        assert wf.last_temp_tier is None
        assert wf.last_humidity_tier is None
        assert wf.last_wind_tier is None
        assert wf.last_sunshine_tier is None
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
        from ascend.weather.derive import derive_seasonal_amp as _derive_seasonal_amp
        assert _derive_seasonal_amp(-5.0, 800.0) > 25.0

    def test_hot_low_amp(self):
        """高温 → 小振幅。"""
        from ascend.weather.derive import derive_seasonal_amp as _derive_seasonal_amp
        assert _derive_seasonal_amp(30.0, 2000.0) < 8.0

    def test_monotonic_in_temperature(self):
        """固定降雨，振幅随温度升高而递减。"""
        from ascend.weather.derive import derive_seasonal_amp as _derive_seasonal_amp
        rainfall = 800.0
        prev = _derive_seasonal_amp(-5.0, rainfall)
        for t in (0.0, 5.0, 12.0, 20.0, 28.0, 35.0):
            curr = _derive_seasonal_amp(t, rainfall)
            assert curr <= prev + 1e-9
            prev = curr

    def test_dry_higher_amp(self):
        """固定温度，干旱区（低降雨）振幅大于湿润区。"""
        from ascend.weather.derive import derive_seasonal_amp as _derive_seasonal_amp
        dry = _derive_seasonal_amp(15.0, 200.0)
        wet = _derive_seasonal_amp(15.0, 2000.0)
        assert dry > wet

    def test_bounded(self):
        """振幅恒在 [1, 30]。"""
        from ascend.weather.derive import derive_seasonal_amp as _derive_seasonal_amp
        for t in (-10.0, -5.0, 0.0, 15.0, 35.0, 50.0):
            for r in (0.0, 200.0, 1000.0, 3000.0, 5000.0):
                amp = _derive_seasonal_amp(t, r)
                assert 1.0 <= amp <= 30.0

    def test_continuous_at_climate_boundary(self):
        """气候带交界处（年均温相同）振幅连续，无跳变。"""
        from ascend.weather.derive import derive_seasonal_amp as _derive_seasonal_amp
        amp_temperate = _derive_seasonal_amp(5.0, 1000.0)
        amp_subarctic = _derive_seasonal_amp(5.0, 800.0)
        assert abs(amp_temperate - amp_subarctic) < 1.0

    def test_no_discrete_jump_across_boundary(self):
        """温带→亚寒带交界，T从4.9→5.1（跨边界），振幅变化微小。"""
        from ascend.weather.derive import derive_seasonal_amp as _derive_seasonal_amp
        amp_below = _derive_seasonal_amp(4.9, 800.0)
        amp_above = _derive_seasonal_amp(5.1, 800.0)
        assert abs(amp_above - amp_below) < 0.5

    def test_tropical_savanna_to_desert_continuous(self):
        """热带草原→沙漠交界，R从201→199（跨R=200阈值），振幅变化微小。"""
        from ascend.weather.derive import derive_seasonal_amp as _derive_seasonal_amp
        amp_savanna = _derive_seasonal_amp(22.0, 201.0)
        amp_desert = _derive_seasonal_amp(22.0, 199.0)
        assert abs(amp_desert - amp_savanna) < 0.1


# ── weather_engine ─────────────────────────────────────────────────


class TestFrameDeferredRecords:
    """天气事件与观察缓存只在帧提交后生效（WC-7.6，注入 state_store）。"""

    def _engine(self, store=None):
        from ascend.weather.weather_engine import WeatherEngine
        wt = WorldTree()
        events: list = []
        wt.subscribe("temperature_change", lambda e: events.append(e))
        clock = WorldClock()
        engine = WeatherEngine(
            clock, seed=42, world_tree_arg=wt, state_store=store,
        )
        engine.register_chunk(
            0, 0, _make_baseline(), ClimateZone.TEMPERATE_FOREST, 15.0,
        )
        return engine, clock, events

    def test_rollback_leaves_no_events_and_retries(self):
        """回滚的帧不留事件、不推进观察缓存；重试帧提交后发布（不丢事件）。"""
        from ascend.runtime.state_store import FrameStateStore
        store = FrameStateStore()
        engine, clock, events = self._engine(store)
        _advance_weather(engine, clock.time)  # 首刻静默初始化
        _force_perception_reset(engine, 0, 0, "temp")
        clock.skip(1)

        store.begin_frame()
        engine.advance(clock.time)
        store.abort()
        assert events == [], "回滚的帧不留事件"
        assert engine._fields[(0, 0)].last_temp_tier == -1, (
            "回滚的帧不推进观察缓存"
        )

        with store.frame():
            engine.advance(clock.time)
        assert len(events) == 1, "重试帧提交后发布（不丢事件）"
        assert engine._fields[(0, 0)].last_temp_tier != -1
        engine.shutdown()

    def test_no_store_publishes_immediately(self):
        """未注入帧事务（测试/独立使用）：行为与旧路径一致（立即生效）。"""
        engine, clock, events = self._engine(store=None)
        _advance_weather(engine, clock.time)
        _force_perception_reset(engine, 0, 0, "temp")
        clock.skip(1)
        engine.advance(clock.time)
        assert len(events) == 1
        engine.shutdown()


class TestWeatherEngine:
    """天气引擎测试 — 解析算 + per-parameter 事件。"""

    def test_construct(self):
        from ascend.weather.weather_engine import WeatherEngine
        wt = WorldTree()
        e = WeatherEngine(WorldClock(), seed=42, world_tree_arg=wt)
        e.shutdown()

    def test_field_drift_no_short_cycle(self):
        """纹理漂移不得在 ~1 年内精确重复（场值长期演化）。"""
        from ascend.weather.field import UnifiedWeatherField
        from ascend.config import GAME_YEAR
        f = UnifiedWeatherField(seed=42)
        v0 = f.sample("precip", 1000.0, 1000.0, 0)
        vy = f.sample("precip", 1000.0, 1000.0, GAME_YEAR)
        assert abs(vy - v0) > 0.001, f"1 年漂移后场值未变（{vy - v0:.4f}）"

    def test_no_fields_tick_noop(self):
        from ascend.weather.weather_engine import WeatherEngine
        wt = WorldTree()
        clock = WorldClock()
        e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
        _advance_weather(e, clock.time)
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
        _advance_weather(e, clock.time)  # 首刻静默初始化
        _force_perception_reset(e, 0, 0, "temp")
        clock.skip(1)
        _advance_weather(e, clock.time)
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
        _advance_weather(e, clock.time)  # 首刻静默初始化
        _force_perception_reset(e, 0, 0, "wind")
        clock.skip(1)
        _advance_weather(e, clock.time)
        assert len(events) >= 1
        d = events[0].data
        wdx, wdy = d["wind_dir_x"], d["wind_dir_y"]
        assert math.hypot(wdx, wdy) == pytest.approx(1.0, abs=1e-6)
        e.shutdown()

    def test_temperature_change_on_crossing_perception(self):
        """推进足够多游戏天确保跨越等级边界 → temperature_change 带新 tier。"""
        from ascend.weather.weather_engine import WeatherEngine
        wt = WorldTree()
        events = []
        wt.subscribe("temperature_change", lambda e: events.append(e))
        clock = WorldClock()
        e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
        # 基线温度 5°C → 等级 2（阈值 5），冷季振幅 ~8°C，日间振幅 ~6°C
        # → 50 天（半个季节）后温度必定跨越进入等级 1 或 3
        e.register_chunk(0, 0, _make_baseline(temp=5.0), ClimateZone.TEMPERATE_FOREST, 5.0)
        _advance_weather(e, clock.time)  # 首刻静默初始化
        assert len(events) == 0
        before_tier = e.get_tiers(0, 0)["temperature"]
        assert isinstance(before_tier, int)
        clock.skip(50 * GAME_DAY)
        _advance_weather(e, clock.time)
        after_events = [ev for ev in events if ev.event_type == "temperature_change"]
        assert len(after_events) >= 1
        after_tier = after_events[0].data["tier"]
        assert after_tier != before_tier
        assert isinstance(after_tier, int)
        e.shutdown()

    def test_first_tick_emits_no_param_events(self):
        """首刻静默初始化等级，不发事件（初始状态走查询 API）。"""
        from ascend.weather.weather_engine import WeatherEngine
        wt = WorldTree()
        events = []
        for t in ("temperature_change", "humidity_change", "wind_change",
                  "sunshine_change", "precipitation_start", "precipitation_stop"):
            wt.subscribe(t, lambda e: events.append(e))
        clock = WorldClock()
        e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
        e.register_chunk(0, 0, _make_baseline(), ClimateZone.TEMPERATE_FOREST, 15.0)
        _advance_weather(e, clock.time)
        param_events = [ev for ev in events
                        if ev.event_type in ("temperature_change", "humidity_change",
                                             "wind_change", "sunshine_change")]
        assert param_events == []
        # 等级已静默初始化
        field = e._fields[(0, 0)]
        assert field.last_temp_tier is not None
        assert field.last_humidity_tier is not None
        assert field.last_wind_tier is not None
        assert field.last_sunshine_tier is not None
        e.shutdown()

    def test_tier_change_emits_with_tier_field(self):
        """等级变化时发布的事件带 prev_tier / tier 字段（均为 int）。"""
        from ascend.weather.weather_engine import WeatherEngine
        wt = WorldTree()
        events = []
        for t in ("temperature_change", "humidity_change", "wind_change",
                  "sunshine_change"):
            wt.subscribe(t, lambda e: events.append(e))
        clock = WorldClock()
        e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
        e.register_chunk(0, 0, _make_baseline(), ClimateZone.TEMPERATE_FOREST, 15.0)
        _advance_weather(e, clock.time)  # 首刻静默初始化
        _force_perception_reset(e, 0, 0, "temp", "humidity", "wind", "sunshine")
        clock.skip(1)
        _advance_weather(e, clock.time)
        assert sum(1 for ev in events if ev.event_type == "temperature_change") == 1
        assert sum(1 for ev in events if ev.event_type == "humidity_change") == 1
        assert sum(1 for ev in events if ev.event_type == "wind_change") == 1
        assert sum(1 for ev in events if ev.event_type == "sunshine_change") == 1
        for ev in events:
            assert "prev_tier" in ev.data
            assert "tier" in ev.data
            assert isinstance(ev.data["prev_tier"], int)
            assert isinstance(ev.data["tier"], int)
        e.shutdown()

    def test_no_event_within_same_tier(self):
        """同一等级内微小波动不发事件。"""
        from ascend.weather.weather_engine import WeatherEngine
        wt = WorldTree()
        events = []
        wt.subscribe("temperature_change", lambda e: events.append(e))
        clock = WorldClock()
        e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
        e.register_chunk(0, 0, _make_baseline(temp=25.0), ClimateZone.TEMPERATE_FOREST, 15.0)
        _advance_weather(e, clock.time)  # 首刻静默初始化
        assert isinstance(e.get_tiers(0, 0)["temperature"], int)
        clock.skip(1)  # 推进 1 tick，微小变化
        _advance_weather(e, clock.time)
        temp_events = [ev for ev in events if ev.event_type == "temperature_change"]
        assert len(temp_events) == 0
        e.shutdown()

    def test_precip_start_emitted_on_event_start(self):
        """阈值窗口干预起效 → 区域 precipitation_start（含强度与类型）。

        事件是纯函数（当前帧 vs 上一分钟）：窗口干预当前帧生效、
        上一分钟尚未生效，因此派生出 start。
        """
        from ascend.config import GAME_MINUTE
        from ascend.causal import PlannedIntervention
        from ascend.weather import mechanisms as m
        from ascend.weather.weather_engine import WeatherEngine

        wt = WorldTree()
        events = []
        wt.subscribe("precipitation_start", lambda e: events.append(e))
        clock = WorldClock()
        domain: list[tuple[int, int]] = []
        e = WeatherEngine(
            clock, seed=42, world_tree_arg=wt,
            climate_lookup=lambda cx, cy: (800.0, 5.0),
            region_domain=lambda: tuple(domain),
        )
        now = clock.time
        dry = _find_dry_chunk(e, now)
        assert dry is not None, "应存在自然干燥且信号高于阈下界的 chunk"
        domain.append(dry)
        e.register_chunk(
            dry[0], dry[1], _make_baseline(temp=20.0),
            ClimateZone.TEMPERATE_FOREST, 15.0,
        )
        e.intervention_table.plan(PlannedIntervention(
            target_space="node", target=m.PRECIPITATION_THRESHOLD,
            instance=dry, value=0.25,
            start_frame=now, stop_frame=now + GAME_MINUTE, source="test",
        ))
        _advance_weather(e, now)
        starts = [ev for ev in events if ev.event_type == "precipitation_start"]
        assert len(starts) >= 1
        assert starts[0].data["intensity"] > 0
        assert starts[0].data["precip_type"] in ("rain", "snow")
        assert starts[0].fate_path == (
            f"weather/precip/{dry[0]}/{dry[1]}@{starts[0].timestamp}"
        )
        e.shutdown()

    def test_precip_stop_emitted_on_event_end(self):
        """阈值窗口干预失效 → 区域 precipitation_stop。

        当前帧阈恢复（自然干燥）、上一分钟干预生效（降雨）→ stop。
        """
        from ascend.config import GAME_MINUTE
        from ascend.causal import PlannedIntervention
        from ascend.weather import mechanisms as m
        from ascend.weather.weather_engine import WeatherEngine

        wt = WorldTree()
        events = []
        for t in ("precipitation_start", "precipitation_stop"):
            wt.subscribe(t, lambda e: events.append(e))
        clock = WorldClock()
        domain: list[tuple[int, int]] = []
        e = WeatherEngine(
            clock, seed=42, world_tree_arg=wt,
            climate_lookup=lambda cx, cy: (50.0, 5.0),
            region_domain=lambda: tuple(domain),
        )
        now = clock.time
        dry = _find_dry_chunk(e, now, annual=50.0)
        assert dry is not None, "应存在自然干燥且信号高于阈下界的 chunk"
        domain.append(dry)
        e.register_chunk(
            dry[0], dry[1], _make_baseline(rain=50.0),
            ClimateZone.TEMPERATE_FOREST, 15.0,
        )
        e.intervention_table.plan(PlannedIntervention(
            target_space="node", target=m.PRECIPITATION_THRESHOLD,
            instance=dry, value=0.25,
            start_frame=now, stop_frame=now + GAME_MINUTE, source="test",
        ))
        _advance_weather(e, now)
        assert any(ev.event_type == "precipitation_start" for ev in events)
        clock.skip(GAME_MINUTE)
        _advance_weather(e, clock.time)
        stops = [ev for ev in events if ev.event_type == "precipitation_stop"]
        assert len(stops) >= 1
        assert tuple(tuple(c) for c in stops[0].data["chunks"]) == (dry,)
        e.shutdown()

    def test_domain_move_emits_region_start_and_stop(self):
        """观察域移动：走入既有雨带 → start；走出 → stop（各用当时的域）。"""
        from ascend.config import GAME_MINUTE
        from ascend.causal import PlannedIntervention
        from ascend.weather import mechanisms as m
        from ascend.weather.weather_engine import WeatherEngine

        wt = WorldTree()
        events = []
        for t in ("precipitation_start", "precipitation_stop"):
            wt.subscribe(t, lambda e: events.append(e))
        clock = WorldClock()
        domain: list[tuple[int, int]] = []
        e = WeatherEngine(
            clock, seed=42, world_tree_arg=wt,
            climate_lookup=lambda cx, cy: (800.0, 5.0),
            region_domain=lambda: tuple(domain),
        )
        now = clock.time
        dry = _find_dry_chunk(e, now)
        assert dry is not None, "应存在自然干燥且信号高于阈下界的 chunk"
        e.register_chunk(
            dry[0], dry[1], _make_baseline(temp=20.0),
            ClimateZone.TEMPERATE_FOREST, 15.0,
        )
        e.intervention_table.plan(PlannedIntervention(
            target_space="node", target=m.PRECIPITATION_THRESHOLD,
            instance=dry, value=0.25,
            start_frame=now, stop_frame=now + 10 * GAME_MINUTE,
            source="test",
        ))
        _advance_weather(e, now)  # 域空：无区域事件
        assert events == []
        domain.append(dry)  # 走入雨带
        clock.skip(GAME_MINUTE)
        _advance_weather(e, clock.time)
        assert [ev.event_type for ev in events] == ["precipitation_start"]
        events.clear()
        domain.clear()  # 走出雨带
        clock.skip(GAME_MINUTE)
        _advance_weather(e, clock.time)
        assert [ev.event_type for ev in events] == ["precipitation_stop"]
        e.shutdown()

    def test_precip_type_snow_when_cold(self):
        """温度 ≤ 0°C 时降水形态为 snow。"""
        from ascend.config import GAME_MINUTE
        from ascend.causal import PlannedIntervention
        from ascend.weather import mechanisms as m
        from ascend.weather.weather_engine import WeatherEngine

        wt = WorldTree()
        events = []
        wt.subscribe("precipitation_start", lambda e: events.append(e))
        clock = WorldClock()
        domain: list[tuple[int, int]] = []
        e = WeatherEngine(
            clock, seed=42, world_tree_arg=wt,
            climate_lookup=lambda cx, cy: (800.0, 5.0),
            region_domain=lambda: tuple(domain),
        )
        now = clock.time
        dry = _find_dry_chunk(e, now)
        assert dry is not None
        domain.append(dry)
        # 极地，年均温 -10°C
        e.register_chunk(
            dry[0], dry[1], _make_baseline(temp=-10.0),
            ClimateZone.POLAR_TUNDRA, -5.0,
        )
        e.intervention_table.plan(PlannedIntervention(
            target_space="node", target=m.PRECIPITATION_THRESHOLD,
            instance=dry, value=0.25,
            start_frame=now, stop_frame=now + GAME_MINUTE, source="test",
        ))
        _advance_weather(e, now)
        starts = [ev for ev in events if ev.event_type == "precipitation_start"]
        assert len(starts) >= 1
        assert starts[0].data["precip_type"] == "snow"
        e.shutdown()

    def test_event_data_matches_contract(self):
        """发布的事件 data 与 TemperatureChange 契约字段一致。"""
        from ascend.weather.weather_engine import WeatherEngine
        wt = WorldTree()
        events = []
        wt.subscribe("temperature_change", lambda e: events.append(e))
        clock = WorldClock()
        e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
        e.register_chunk(0, 0, _make_baseline(), ClimateZone.TEMPERATE_FOREST, 15.0)
        _advance_weather(e, clock.time)  # 首刻静默初始化
        _force_perception_reset(e, 0, 0, "temp")
        clock.skip(1)
        _advance_weather(e, clock.time)
        d = events[0].data
        assert set(d) == {f.name for f in fields(TemperatureChange)}
        assert "prev_tier" in d
        assert "tier" in d
        assert isinstance(d["prev_tier"], int)
        assert isinstance(d["tier"], int)
        e.shutdown()

    def test_world_tree_events_do_not_drive_engine(self):
        """事件总线不再是驱动路径：minute_change 不触发天气推进。"""
        from ascend.config import GAME_DAY, GAME_HOUR
        from ascend.weather.weather_engine import WeatherEngine
        wt = WorldTree()
        events = []
        wt.subscribe("temperature_change", lambda e: events.append(e))
        clock = WorldClock()
        e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
        e.register_chunk(0, 0, _make_baseline(), ClimateZone.TEMPERATE_FOREST, 15.0)
        _advance_weather(e, clock.time)  # 首刻静默初始化
        _force_perception_reset(e, 0, 0, "temp")
        clock.skip(1)
        tod = clock.time % GAME_DAY
        wt.publish(Event(
            timestamp=clock.time,
            location=(0, 0, None, None),
            initiator_type="system",
            initiator_id="test",
            affected=[AffectedParty("world", "subject")],
            event_type="minute_change",
            data={
                "game_time": clock.time,
                "day": clock.time // GAME_DAY + 1,
                "hour": int(tod / GAME_HOUR),
                "minute": int((tod % GAME_HOUR) / (GAME_HOUR // 60)),
            },
        ))
        assert events == [], "发布 minute_change 不应驱动天气求值"
        _advance_weather(e, clock.time)
        assert len(events) == 1, "调度器推进才产生天气事件"
        e.shutdown()

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
        _advance_weather(e, clock.time)  # 首刻静默初始化
        _force_perception_reset(e, 0, 0, "temp")
        _force_perception_reset(e, 5, 5, "temp")
        clock.skip(1)
        _advance_weather(e, clock.time)
        assert len(events) == 2
        locs = {ev.location[:2] for ev in events}
        assert locs == {(0, 0), (5, 5)}
        e.shutdown()

    def test_rainfall_derived_from_field_signal(self):
        """降雨强度完全由场信号 + 气候带校准推导（无调度状态）。"""
        from ascend.weather.weather_engine import WeatherEngine
        wt = WorldTree()
        clock = WorldClock()
        e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
        e.register_chunk(0, 0, _make_baseline(rain=3000.0),
                         ClimateZone.EQUATORIAL_RAINFOREST, 27.0)
        wp = e.get_weather(0, 0)
        assert wp.rainfall >= 0.0
        # 任意过去时刻可精确重算（解析量，无窗口修剪）
        wp_past = e.get_weather(0, 0, time=0)
        assert wp_past.rainfall >= 0.0
        e.shutdown()

    def test_rainfall_changes_with_force(self):
        """强制降雨改变降雨强度（场信号 + 特征核）。"""
        from ascend.weather.weather_engine import WeatherEngine
        wt = WorldTree()
        clock = WorldClock()
        e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
        e.register_chunk(0, 0, _make_baseline(), ClimateZone.TEMPERATE_FOREST, 15.0)
        before = e.get_weather(0, 0).rainfall
        e.force_feature(0, 0, "storm", True)
        after = e.get_weather(0, 0).rainfall
        assert after > before
        e.shutdown()

    def test_sunshine_change_in_valid_range(self):
        """sunshine_change 的 sunshine 值在 [0, 24] 范围内。"""
        from ascend.weather.weather_engine import WeatherEngine
        wt = WorldTree()
        events = []
        wt.subscribe("sunshine_change", lambda e: events.append(e))
        clock = WorldClock()
        e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
        e.register_chunk(0, 0, _make_baseline(sun=12.0), ClimateZone.TEMPERATE_FOREST, 15.0)
        _advance_weather(e, clock.time)  # 首刻静默初始化
        _force_perception_reset(e, 0, 0, "sunshine")
        clock.skip(1)
        _advance_weather(e, clock.time)
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
        _advance_weather(e, clock.time)  # 首次（昼）
        clock.skip(6 * GAME_HOUR)  # 18:00（已日落 → 夜）
        _advance_weather(e, clock.time)
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
        _advance_weather(e, clock.time)  # 首次（夜）
        clock.skip(12 * GAME_HOUR)  # 到次日 08:00（已日出）
        _advance_weather(e, clock.time)
        assert len(events) == 1
        assert events[0].location[:2] == (5, 5)
        assert "daylight_hours" in events[0].data
        assert events[0].data["daylight_hours"] > 0
        e.shutdown()

    def test_derive_latitude_equator(self):
        """年均温 35°C → 纬度≈0（赤道）。"""
        from ascend.weather.derive import derive_latitude as _derive_latitude
        assert _derive_latitude(35.0) == pytest.approx(0.0, abs=2.0)

    def test_derive_latitude_polar(self):
        """年均温 -5°C → 纬度≈80（极地边缘）。"""
        from ascend.weather.derive import derive_latitude as _derive_latitude
        assert _derive_latitude(-5.0) == pytest.approx(80.0, abs=2.0)

    def test_derive_latitude_monotonic(self):
        """温度越高 → 纬度越低。"""
        from ascend.weather.derive import derive_latitude as _derive_latitude
        assert _derive_latitude(0.0) > _derive_latitude(20.0)

    def test_derive_seasonal_amp_polar_large(self):
        """低温（-5°C）→ 季节振幅 > 20°C。"""
        from ascend.weather.derive import derive_seasonal_amp as _derive_seasonal_amp
        amp = _derive_seasonal_amp(-5.0, 500.0)
        assert amp > 20.0

    def test_derive_seasonal_amp_equatorial_small(self):
        """高温（35°C）→ 季节振幅 < 5°C。"""
        from ascend.weather.derive import derive_seasonal_amp as _derive_seasonal_amp
        amp = _derive_seasonal_amp(35.0, 2000.0)
        assert amp < 5.0

    def test_derive_seasonal_amp_dry_larger(self):
        """干旱区振幅 > 同温湿润区（大陆性气候）。"""
        from ascend.weather.derive import derive_seasonal_amp as _derive_seasonal_amp
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
        _advance_weather(e, clock.time)  # 首刻静默初始化
        _force_perception_reset(e, 0, 0, "temp")
        _force_perception_reset(e, 1, 0, "temp")
        clock.skip(1)
        _advance_weather(e, clock.time)
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


# ── 等级分类 ────────────────────────────────────────────────────────


class TestTierClassification:
    """等级分类函数测试。"""

    def test_classify_temperature_bounds(self):
        from ascend.weather.derive import classify_temperature
        assert classify_temperature(-30.0) == 0
        assert classify_temperature(-10.0) == 1
        assert classify_temperature(-3.1) == 1
        assert classify_temperature(-3.0) == 2
        assert classify_temperature(4.9) == 2
        assert classify_temperature(5.0) == 3
        assert classify_temperature(12.9) == 3
        assert classify_temperature(13.0) == 4
        assert classify_temperature(19.9) == 4
        assert classify_temperature(20.0) == 5
        assert classify_temperature(24.9) == 5
        assert classify_temperature(25.0) == 6
        assert classify_temperature(29.9) == 6
        assert classify_temperature(30.0) == 7
        assert classify_temperature(35.9) == 7
        assert classify_temperature(36.0) == 8
        assert classify_temperature(42.9) == 8
        assert classify_temperature(43.0) == 9
        assert classify_temperature(60.0) == 9

    def test_classify_humidity_bounds(self):
        from ascend.weather.derive import classify_humidity
        assert classify_humidity(0.0) == 0
        assert classify_humidity(24.9) == 0
        assert classify_humidity(25.0) == 1
        assert classify_humidity(49.9) == 1
        assert classify_humidity(50.0) == 2
        assert classify_humidity(71.9) == 2
        assert classify_humidity(72.0) == 3
        assert classify_humidity(87.9) == 3
        assert classify_humidity(88.0) == 4
        assert classify_humidity(100.0) == 4

    def test_classify_wind_bounds(self):
        from ascend.weather.derive import classify_wind
        assert classify_wind(0.0) == 0
        assert classify_wind(1.4) == 0
        assert classify_wind(1.5) == 1
        assert classify_wind(3.9) == 1
        assert classify_wind(4.0) == 2
        assert classify_wind(7.9) == 2
        assert classify_wind(8.0) == 3
        assert classify_wind(13.9) == 3
        assert classify_wind(14.0) == 4
        assert classify_wind(22.9) == 4
        assert classify_wind(23.0) == 5
        assert classify_wind(60.0) == 5

    def test_classify_sunshine_bounds(self):
        from ascend.weather.derive import classify_sunshine
        assert classify_sunshine(0.0) == 0
        assert classify_sunshine(1.4) == 0
        assert classify_sunshine(1.5) == 1
        assert classify_sunshine(4.4) == 1
        assert classify_sunshine(4.5) == 2
        assert classify_sunshine(7.9) == 2
        assert classify_sunshine(8.0) == 3
        assert classify_sunshine(11.9) == 3
        assert classify_sunshine(12.0) == 4
        assert classify_sunshine(15.4) == 4
        assert classify_sunshine(15.5) == 5
        assert classify_sunshine(24.0) == 5


# ── 查询 API ────────────────────────────────────────────────────────


class TestSunlightIntensity:
    """日照强度等级分类测试。"""

    def test_classify_sunlight_intensity_bounds(self):
        from ascend.weather.derive import classify_sunlight_intensity
        assert classify_sunlight_intensity(0.0) == 0
        assert classify_sunlight_intensity(0.009) == 0
        assert classify_sunlight_intensity(0.01) == 1
        assert classify_sunlight_intensity(0.24) == 1
        assert classify_sunlight_intensity(0.25) == 2
        assert classify_sunlight_intensity(0.54) == 2
        assert classify_sunlight_intensity(0.55) == 3
        assert classify_sunlight_intensity(0.79) == 3
        assert classify_sunlight_intensity(0.80) == 4
        assert classify_sunlight_intensity(1.0) == 4

    def test_report_daytime_intensity(self):
        """白天正午日照强度接近 1。"""
        from ascend.weather.weather_engine import WeatherEngine
        from ascend.config import GAME_DAY
        wt = WorldTree()
        clock = WorldClock()
        clock.skip(6 * GAME_HOUR)  # 12:00（正午）
        e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
        e.register_chunk(0, 0, _make_baseline(), ClimateZone.TEMPERATE_FOREST, 15.0)
        report = e.get_weather_report(0, 0)
        assert report is not None
        _params, _sr, _ss, daylight, intensity, _azimuth = report
        assert daylight > 0
        assert intensity > 0.8  # 正午强度接近 1
        e.shutdown()

    def test_report_nighttime_intensity(self):
        """夜间强度为 0。"""
        from ascend.weather.weather_engine import WeatherEngine
        wt = WorldTree()
        clock = WorldClock()
        clock.skip(14 * GAME_HOUR)  # 20:00（夜间）
        e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
        e.register_chunk(0, 0, _make_baseline(), ClimateZone.TEMPERATE_FOREST, 15.0)
        report = e.get_weather_report(0, 0)
        assert report is not None
        assert report[4] == 0.0
        e.shutdown()

    def test_rain_attenuates_report_intensity(self):
        """降雨衰减日照：强制降雨后的 report 强度显著低于无雨。"""
        from ascend.weather.weather_engine import WeatherEngine
        wt = WorldTree()
        clock = WorldClock()
        clock.skip(6 * GAME_HOUR)  # 12:00（正午）
        e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
        e.register_chunk(0, 0, _make_baseline(), ClimateZone.TEMPERATE_FOREST, 15.0)
        clear = e.get_weather_report(0, 0)
        assert clear is not None and clear[4] > 0.8
        assert e.force_feature(0, 0, "storm", True) is True
        storm = e.get_weather_report(0, 0)
        assert storm is not None
        assert storm[4] < clear[4]
        assert storm[4] < clear[4] * 0.95
        e.shutdown()


class TestWeatherQueryAPI:
    """get_weather / get_tiers 查询 API 测试。"""

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

    def test_get_tiers(self):
        from ascend.weather.weather_engine import WeatherEngine
        wt = WorldTree()
        clock = WorldClock()
        e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
        e.register_chunk(0, 0, _make_baseline(), ClimateZone.TEMPERATE_FOREST, 15.0)
        p = e.get_tiers(0, 0)
        assert p is not None
        for key in ("temperature", "humidity", "wind", "sunshine"):
            assert key in p
            assert isinstance(p[key], int)
        e.shutdown()

    def test_get_tiers_unregistered_returns_none(self):
        from ascend.weather.weather_engine import WeatherEngine
        wt = WorldTree()
        clock = WorldClock()
        e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
        assert e.get_tiers(999, 999) is None
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

    def test_get_tiers_future_time_raises(self):
        """get_tiers 查询未来时刻抛 ValueError。"""
        from ascend.weather.weather_engine import WeatherEngine
        wt = WorldTree()
        clock = WorldClock()
        e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
        e.register_chunk(0, 0, _make_baseline(), ClimateZone.TEMPERATE_FOREST, 15.0)
        with pytest.raises(ValueError):
            e.get_tiers(0, 0, clock.time + GAME_DAY)
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

    def test_report_returns_six_tuple(self):
        """返回 (params, sunrise, sunset, daylight, intensity, sun_azimuth) 六元组。"""
        from ascend.weather.weather_engine import WeatherEngine
        wt = WorldTree()
        clock = WorldClock()
        e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
        e.register_chunk(0, 0, _make_baseline(), ClimateZone.TEMPERATE_FOREST, 15.0)
        report = e.get_weather_report(0, 0)
        assert report is not None
        params, sr, ss, daylight, intensity, sun_azimuth = report
        assert params is not None
        assert sr < ss
        assert daylight == pytest.approx(ss - sr)
        assert 0.0 <= intensity <= 1.0
        assert 0.0 <= sun_azimuth <= 360.0
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

    def test_report_sun_azimuth_daily_constant_seasonal(self):
        """sun_azimuth 日内恒定（光照轨道基准）、随季节渐变（P2-01）。"""
        from ascend.weather.weather_engine import WeatherEngine
        from ascend.weather.diurnal import sunrise_azimuth, _solar_declination
        wt = WorldTree()
        clock = WorldClock()
        e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
        e.register_chunk(0, 0, _make_baseline(), ClimateZone.TEMPERATE_FOREST, 15.0)
        field = e._fields[(0, 0)]
        # 日内恒定：t0 与 t0+6h 方位角相同（前端轨道基准不随小时跳变）
        t0 = clock.time
        az0 = e.get_weather_report(0, 0)[5]
        clock.skip(6 * GAME_HOUR)
        az1 = e.get_weather_report(0, 0)[5]
        assert az0 == az1
        # 对拍直接计算（不重复实现公式）
        day_of_year = (clock.time // GAME_DAY) % 360
        expected = sunrise_azimuth(
            day_of_year, field.baseline.latitude,
            solar_decl=_solar_declination(day_of_year))
        assert az1 == pytest.approx(expected)
        # 跨季渐变：180 天后（不同季节）方位角不同
        clock.skip(180 * GAME_DAY)
        az2 = e.get_weather_report(0, 0)[5]
        assert az2 != az1
        e.shutdown()

    def test_get_weather_event_consistency(self):
        """get_weather 值与事件发布的数值一致（强制等级变化后比较）。"""
        from ascend.weather.weather_engine import WeatherEngine
        wt = WorldTree()
        events = {t: [] for t in ("temperature_change", "humidity_change",
                                   "wind_change", "sunshine_change")}
        for t in events:
            wt.subscribe(t, lambda e, t=t: events[t].append(e))
        clock = WorldClock()
        e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
        e.register_chunk(0, 0, _make_baseline(), ClimateZone.TEMPERATE_FOREST, 15.0)
        _advance_weather(e, clock.time)  # 首刻静默初始化
        _force_perception_reset(e, 0, 0, "temp", "humidity", "wind", "sunshine")
        clock.skip(1)
        _advance_weather(e, clock.time)
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
        _advance_weather(e, clock.time)
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
        _advance_weather(e, clock.time)  # 首次（day 1 春，不发）
        clock.skip(90 * GAME_DAY)  # 推进到 day 91 06:00（夏）
        _advance_weather(e, clock.time)
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
        _advance_weather(e, clock.time)  # 首次（昼）
        clock.skip(6 * GAME_HOUR)  # 18:00（已日落 → 夜）
        _advance_weather(e, clock.time)
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
        _advance_weather(e, clock.time)  # 首次（夜）
        assert len(events) == 0
        # 推进到次日 08:00（已日出，日出≈7:10）
        clock.skip(12 * GAME_HOUR)  # 20:00 + 12h = 次日 08:00
        _advance_weather(e, clock.time)
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
        _advance_weather(e, clock.time)
        clock.skip(6 * GAME_HOUR)  # 到 12:00（仍白天）
        _advance_weather(e, clock.time)
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
        _advance_weather(e, clock.time)
        clock.skip(90 * GAME_DAY)
        _advance_weather(e, clock.time)
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
        _advance_weather(e, clock.time)
        clock.skip(12 * GAME_HOUR)  # 次日 08:00
        _advance_weather(e, clock.time)
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
        _advance_weather(e, clock.time)
        clock.skip(7 * GAME_HOUR)  # 19:00（已日落）
        _advance_weather(e, clock.time)
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
        _advance_weather(e, clock.time)
        clock.skip(12 * GAME_HOUR)
        _advance_weather(e, clock.time)
        by_chunk: dict[tuple, float] = {}
        for ev in events:
            if ev.event_type == "sunrise":
                by_chunk[ev.location[:2]] = ev.data["daylight_hours"]
        if (0, 0) in by_chunk:
            assert by_chunk[(0, 0)] == pytest.approx(12.0, abs=1.0)
        if (10, 0) in by_chunk:
            assert by_chunk[(10, 0)] > 22.0
        e.shutdown()


class TestExtremeWeatherIntegration:
    """极端天气集成测试 — 特征核注入 → 参数偏移 + 引擎事件。"""

    def test_cold_snap_affects_temperature(self):
        """注入寒潮核 → chunk 温度显著下降。"""
        from ascend.weather.weather_engine import WeatherEngine
        wt = WorldTree()
        clock = WorldClock()
        e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
        e.register_chunk(0, 0, _make_baseline(temp=10.0), ClimateZone.SUBARCTIC_TAIGA, 0.0)
        normal = e.get_weather(0, 0).temperature
        assert e.force_feature(0, 0, "cold_snap", True) is True
        cold = e.get_weather(0, 0).temperature
        assert cold < normal - 10.0, f"寒潮应降温 ≥10°C（{normal}→{cold}）"
        e.shutdown()

    def test_heat_wave_events_emitted(self):
        """热浪核注入/解除 → heat_wave_start/stop 事件。"""
        from ascend.weather.weather_engine import WeatherEngine
        wt = WorldTree()
        events = []
        for t in ("heat_wave_start", "heat_wave_stop"):
            wt.subscribe(t, lambda e: events.append(e))
        clock = WorldClock()
        e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
        e.register_chunk(0, 0, _make_baseline(temp=25.0), ClimateZone.DESERT, 22.0)
        _advance_weather(e, clock.time)  # 首刻静默初始化
        e.force_feature(0, 0, "heat_wave", True)
        clock.skip(1)
        _advance_weather(e, clock.time)
        assert any(ev.event_type == "heat_wave_start" for ev in events)
        e.force_feature(0, 0, "heat_wave", False)
        clock.skip(1)
        _advance_weather(e, clock.time)
        assert any(ev.event_type == "heat_wave_stop" for ev in events)
        e.shutdown()

    def test_storm_affects_wind_and_rain(self):
        """注入风暴核 → 风速倍率 > 1 且降雨上升。"""
        from ascend.weather.weather_engine import WeatherEngine
        wt = WorldTree()
        clock = WorldClock()
        e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
        e.register_chunk(0, 0, _make_baseline(wind=5.0), ClimateZone.TEMPERATE_FOREST, 15.0)
        normal = e.get_weather(0, 0)
        e.force_feature(0, 0, "storm", True)
        storm = e.get_weather(0, 0)
        assert storm.wind_speed > normal.wind_speed * 1.5
        assert storm.rainfall > normal.rainfall
        e.shutdown()


class TestForceControl:
    """强制特征核控制 API 测试 — force_feature（终端调试指令）。"""

    @staticmethod
    def _make_engine():
        """构造含 chunk (0,0) 温带森林的引擎（独立 WorldTree）。"""
        from ascend.weather.weather_engine import WeatherEngine
        wt = WorldTree()
        clock = WorldClock()
        e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
        e.register_chunk(0, 0, _make_baseline(), ClimateZone.TEMPERATE_FOREST, 15.0)
        return e, wt, clock

    def test_unregistered_returns_none(self):
        """未注册 chunk 的 force_feature 返回 None。"""
        e, _, _ = self._make_engine()
        assert e.force_feature(9, 9, "cold_snap", True) is None
        assert e.force_feature(9, 9, "cold_snap", False) is None
        e.shutdown()

    def test_on_starts_immediately(self):
        """force_feature(on) 立即生效：注入核存在且参数偏移反映。"""
        e, _, clock = self._make_engine()
        normal = e.get_weather(0, 0).temperature
        assert e.force_feature(0, 0, "cold_snap", True) is True
        assert (0, 0, "cold_snap") in e._field.features._injected
        assert e.get_weather(0, 0).temperature < normal - 10.0
        # 重复开启为 no-op
        assert e.force_feature(0, 0, "cold_snap", True) is False
        e.shutdown()

    def test_off_stops_immediately(self):
        """force_feature(off) 立即移除核并恢复参数。"""
        e, _, clock = self._make_engine()
        normal = e.get_weather(0, 0).temperature
        e.force_feature(0, 0, "cold_snap", True)
        assert e.force_feature(0, 0, "cold_snap", False) is True
        assert (0, 0, "cold_snap") not in e._field.features._injected
        assert e.get_weather(0, 0).temperature == pytest.approx(normal, abs=0.01)
        # 重复关闭为 no-op
        assert e.force_feature(0, 0, "cold_snap", False) is False
        e.shutdown()

    def test_emits_events_on_next_minute(self):
        """强制核的状态切换由下一次 minute_change 发布 start/stop。"""
        e, wt, clock = self._make_engine()
        events = []
        for t in ("cold_snap_start", "cold_snap_stop"):
            wt.subscribe(t, lambda ev: events.append(ev))
        _advance_weather(e, clock.time)  # 静默初始化
        e.force_feature(0, 0, "cold_snap", True)
        clock.skip(1)
        _advance_weather(e, clock.time)
        assert any(ev.event_type == "cold_snap_start" for ev in events)
        e.force_feature(0, 0, "cold_snap", False)
        clock.skip(1)
        _advance_weather(e, clock.time)
        assert any(ev.event_type == "cold_snap_stop" for ev in events)
        e.shutdown()

    def test_front_injects_velocity(self):
        """锋面核带移动矢量（带形需要速度，wind set 指令的底层）。"""
        e, _, _ = self._make_engine()
        assert e.force_feature(0, 0, "front", True) is True
        core = e._field.features._injected[(0, 0, "front")]
        assert core.vel_x != 0.0 or core.vel_y != 0.0
        e.shutdown()

    def test_unknown_type_raises(self):
        """未知特征类型抛 ValueError。"""
        e, _, _ = self._make_engine()
        with pytest.raises(ValueError):
            e.force_feature(0, 0, "tsunami", True)
        e.shutdown()


class TestWeatherQueryConcurrency:
    """handler 线程查询与引擎线程推进并发安全（P2-12）。"""

    def test_concurrent_query_and_tick(self):
        import threading
        from ascend.weather.weather_engine import WeatherEngine
        wt = WorldTree()
        clock = WorldClock()
        e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
        for cx in range(3):
            e.register_chunk(cx, 0, _make_baseline(),
                             ClimateZone.TEMPERATE_FOREST, 15.0)
        errors: list[Exception] = []
        stop = threading.Event()

        def query_loop() -> None:
            i = 0
            try:
                while not stop.is_set() and i < 5000:
                    cx = i % 3
                    report = e.get_weather_report(cx, 0)
                    if report is not None:
                        assert 0.0 <= report[5] <= 180.0
                        assert report[1] < report[2]
                    wp = e.get_weather(cx, 0)
                    if wp is not None:
                        assert -30.0 <= wp.temperature <= 50.0
                    i += 1
            except Exception as ex:  # noqa: BLE001
                errors.append(ex)

        threads = [threading.Thread(target=query_loop) for _ in range(4)]
        for t in threads:
            t.start()
        try:
            for day in range(10):
                clock.skip(GAME_DAY)
                _advance_weather(e, clock.time)
                # 引擎线程典型操作：重注册与注销交替（迭代期间 dict 增删）
                e.register_chunk(day % 3, 0, _make_baseline(),
                                 ClimateZone.TEMPERATE_FOREST, 15.0)
                other = (day + 1) % 3
                if day % 2 == 0:
                    e.unregister_chunk(other, 0)
                else:
                    e.register_chunk(other, 0, _make_baseline(),
                                     ClimateZone.TEMPERATE_FOREST, 15.0)
        finally:
            stop.set()
            for t in threads:
                t.join(timeout=10)
        assert not errors
        assert all(not t.is_alive() for t in threads)
        e.shutdown()


class TestRegistryProductionAudit:
    """注册表-生产消费审计：已声明机制必须全部进入生产求值路径。

    防回归：chunk 振幅族与区域降水校准若被改回内联公式（与注册表
    分叉），以下测试立即失败（review 2026-09-08 发现的未消费机制）。
    """

    def test_register_chunk_amplitudes_flow_through_registry(self):
        """register_chunk 落盘的振幅族 == 注册表链式求值结果。"""
        from ascend.weather.weather_engine import WeatherEngine
        from ascend.weather import mechanisms as m
        from ascend.causal.world import ASCEND_MECHANISMS as reg

        wt = WorldTree()
        clock = WorldClock()
        e = WeatherEngine(clock, seed=7, world_tree_arg=wt)
        try:
            e.register_chunk(0, 0, _make_baseline(temp=5.0, rain=1000.0),
                             ClimateZone.TEMPERATE_FOREST, 10.0)
            bl = e._fields[(0, 0)].baseline
            seasonal = reg.evaluate(m.SEASONAL_TEMPERATURE_AMPLITUDE, {
                m.ANNUAL_TEMPERATURE: bl.temperature,
                m.ANNUAL_RAINFALL: bl.rainfall,
            })
            assert bl.seasonal_amp == pytest.approx(seasonal, abs=1e-12)
            for got, node in (
                (bl.diurnal_amp, m.DIURNAL_TEMPERATURE_AMPLITUDE),
                (bl.humidity_seasonal_amp, m.SEASONAL_HUMIDITY_AMPLITUDE),
                (bl.humidity_diurnal_amp, m.DIURNAL_HUMIDITY_AMPLITUDE),
            ):
                expected = reg.evaluate(node, {
                    m.SEASONAL_TEMPERATURE_AMPLITUDE: seasonal,
                })
                assert got == pytest.approx(expected, abs=1e-12)
        finally:
            e.shutdown()

    def test_region_tracker_uses_injected_evaluator(self):
        """区域事件路径与查询路径共用注入的求值入口（无旁路公式）。"""
        from ascend.weather import UnifiedWeatherField, RegionTracker
        from ascend.weather import mechanisms as m
        from ascend.causal.world import ASCEND_MECHANISMS as reg

        calls: list[str] = []

        def spy(node_id, parents, *, frame=0, instance=()):
            calls.append(node_id)
            return reg.evaluate(node_id, parents)

        tr = RegionTracker(
            UnifiedWeatherField(seed=42), evaluate=spy,
            climate_baseline=lambda cx, cy: (3000.0, 10.0),
        )
        domain = ((0, 0),)
        events = tr.observe(10000000, domain)
        assert m.PRECIPITATION_THRESHOLD in calls
        # 出现区域时强度也经同一入口求值
        if any(e.kind == "start" for e in events):
            assert m.INSTANT_PRECIPITATION_INTENSITY in calls
