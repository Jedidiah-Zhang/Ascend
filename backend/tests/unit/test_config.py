"""config 常量关系一致性测试。

验证时间/季节/边界常量之间的数学关系，防止单独改动一处导致失配。
"""

from ascend import config


class TestTimeConstants:
    """时间常量关系。"""

    def test_T1_tick_dt_matches_rate(self):
        """TICK_DT × TICK_RATE = 1 秒。"""
        assert config.TICK_DT * config.TICK_RATE == 1.0

    def test_T2_minute_hour_day_chain(self):
        """分/时/天换算链一致。"""
        assert config.GAME_HOUR == 60 * config.GAME_MINUTE
        assert config.GAME_DAY == 24 * config.GAME_HOUR

    def test_T3_year_is_360_days(self):
        """1 年 = 360 天（4 季 × 90 天）。"""
        assert config.GAME_YEAR == 360 * config.GAME_DAY
        assert config.SEASONS_PER_YEAR * config.SEASON_LENGTH_DAYS == 360
        assert config.SEASON_LENGTH * config.SEASONS_PER_YEAR == config.GAME_YEAR


class TestBoundsConstants:
    """物理边界一致性。"""

    def test_T4_param_bounds_align_with_dedicated_bounds(self):
        """PARAM_BOUNDS 与单独定义的 *_BOUNDS 数值一致。"""
        assert config.PARAM_BOUNDS["temperature"] == config.TEMP_BOUNDS
        assert config.PARAM_BOUNDS["humidity"] == config.HUMIDITY_BOUNDS
        assert config.PARAM_BOUNDS["wind_speed"] == config.WIND_BOUNDS
        assert config.PARAM_BOUNDS["sunshine"] == config.SUNSHINE_BOUNDS

    def test_T5_bounds_are_ordered(self):
        """所有边界 (lo, hi) 满足 lo < hi。"""
        for name, (lo, hi) in config.PARAM_BOUNDS.items():
            assert lo < hi, f"PARAM_BOUNDS[{name}] 无序"


class TestWorldConstants:
    """世界生成常量。"""

    def test_T6_birth_elevation_range_ordered(self):
        """出生点海拔范围有序。"""
        assert config.BIRTH_ELEV_MIN < config.BIRTH_ELEV_MAX

    def test_T7_positive_sizes(self):
        """尺寸/容量常量为正。"""
        assert config.TILE_MAP_SIZE > 0
        assert config.CHUNK_STORE_MAX_SIZE > 0
        assert config.INITIAL_CHUNK_RADIUS >= 0
        assert config.TILE_WORKERS > 0
