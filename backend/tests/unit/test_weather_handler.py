"""天气查询处理程序单元测试。

通过 mock WeatherEngine + 假 I18n 验证 make_weather_handler 的
输入防护、字段形状、round→classify 一致性。
"""

import pytest
from unittest.mock import MagicMock

from ascend.space import WeatherParams
from ascend.config import MAX_WEATHER_QUERY_CHUNKS
from ascend.net.handlers.weather_handler import make_weather_handler


class FakeI18n:
    """记录调用并原样返回键名的假翻译器。"""

    def __init__(self):
        """初始化调用记录列表。

        Returns:
            None。
        """
        self.calls = []

    def t(self, key, **kwargs):
        """记录并返回键名本身（不做翻译）。

        Args:
            key: 翻译键。
            **kwargs: 模板变量（忽略）。

        Returns:
            str，键名本身。
        """
        self.calls.append(key)
        return key

    def __repr__(self):
        """返回含调用数的描述。

        Returns:
            str 描述。
        """
        return f"FakeI18n(calls={len(self.calls)})"


def _make_params(temp=20.0, hum=60.0, wind=3.0, sun=12.0, rain=0.0):
    """构造测试用 WeatherParams。"""
    return WeatherParams(
        temperature=temp, rainfall=rain, sunshine=sun,
        altitude=100.0, humidity=hum, wind_speed=wind,
    )


def _make_engine(params=None, sr=6.0, ss=18.0, intensity=0.9, sun_azimuth=135.0):
    """构造 mock WeatherEngine，get_weather_report 返回固定六元组。"""
    engine = MagicMock()
    if params is None:
        params = _make_params()
    engine.get_weather_report.return_value = (params, sr, ss, ss - sr, intensity, sun_azimuth)
    return engine


def _request(chunks):
    """构造 get_weather 请求消息。"""
    return {
        "type": "request",
        "request_type": "get_weather",
        "payload": {"chunks": chunks},
    }


class TestWeatherHandlerShape:
    """响应字段形状测试。"""

    def test_registration(self):
        """make_weather_handler 返回含 get_weather 键的字典。"""
        handlers = make_weather_handler(_make_engine(), FakeI18n())
        assert "get_weather" in handlers
        assert callable(handlers["get_weather"])

    def test_response_fields(self):
        """单个合法坐标返回完整字段（sunshine 取代 daylight_hours）。"""
        handle = make_weather_handler(_make_engine(), FakeI18n())["get_weather"]
        resp = handle(_request([[0, 0]]))
        assert resp["type"] == "response"
        assert resp["request_type"] == "get_weather"
        results = resp["payload"]["weathers"]
        assert len(results) == 1
        r = results[0]
        for key in ("cx", "cy", "temperature", "temp_tier",
                    "humidity", "hum_tier", "wind_speed",
                    "wind_tier", "sunshine", "sun_tier",
                    "sunrise", "sunset", "sun_azimuth", "sunshine_intensity",
                    "light_tier", "weather"):
            assert key in r, f"缺少字段: {key}"
        assert "daylight_hours" not in r

    def test_empty_chunks(self):
        """空 chunks 返回空结果。"""
        handle = make_weather_handler(_make_engine(), FakeI18n())["get_weather"]
        resp = handle(_request([]))
        assert resp["payload"]["weathers"] == []

    def test_unregistered_chunk_skipped(self):
        """get_weather_report 返回 None 的 chunk 被跳过。"""
        engine = MagicMock()
        engine.get_weather_report.return_value = None
        handle = make_weather_handler(engine, FakeI18n())["get_weather"]
        resp = handle(_request([[99, 99]]))
        assert resp["payload"]["weathers"] == []


class TestWeatherHandlerRoundClassify:
    """先 round 后 classify —— 显示数值与等级标签一致。"""

    def test_temp_boundary_rounds_up_to_warm(self):
        """24.96 → 显示 25.0，temp_tier 为 int（25.0 对应等级 6）。"""
        engine = _make_engine(params=_make_params(temp=24.96))
        handle = make_weather_handler(engine, FakeI18n())["get_weather"]
        r = handle(_request([[0, 0]]))["payload"]["weathers"][0]
        assert r["temperature"] == 25.0
        assert r["temp_tier"] == 6
        assert isinstance(r["temp_tier"], int)

    def test_temp_below_boundary_stays_mild(self):
        """24.9 → 显示 24.9，temp_tier 为 int（24.9 对应等级 5）。"""
        engine = _make_engine(params=_make_params(temp=24.9))
        handle = make_weather_handler(engine, FakeI18n())["get_weather"]
        r = handle(_request([[0, 0]]))["payload"]["weathers"][0]
        assert r["temperature"] == 24.9
        assert r["temp_tier"] == 5
        assert isinstance(r["temp_tier"], int)

    def test_sun_tier_from_displayed_sunshine(self):
        """sun_tier 从显示的 sunshine 值分类（11.96 → 12.0 → tier 4）。"""
        engine = _make_engine(params=_make_params(sun=11.96), sr=7.0, ss=17.0)
        handle = make_weather_handler(engine, FakeI18n())["get_weather"]
        r = handle(_request([[0, 0]]))["payload"]["weathers"][0]
        assert r["sunshine"] == 12.0
        assert r["sun_tier"] == 4
        assert isinstance(r["sun_tier"], int)

    def test_light_tier_from_rounded_intensity(self):
        """light_tier 从 round(intensity, 2) 分类（0.796 → 0.80 → tier 4）。"""
        engine = _make_engine(intensity=0.796)
        handle = make_weather_handler(engine, FakeI18n())["get_weather"]
        r = handle(_request([[0, 0]]))["payload"]["weathers"][0]
        assert r["sunshine_intensity"] == 0.8
        assert r["light_tier"] == 4
        assert isinstance(r["light_tier"], int)


class TestWeatherHandlerPrecip:
    """降水描述测试。"""

    def test_clear_when_no_rain(self):
        """无降雨时 weather 为 weather.clear。"""
        handle = make_weather_handler(_make_engine(), FakeI18n())["get_weather"]
        r = handle(_request([[0, 0]]))["payload"]["weathers"][0]
        assert r["weather"] == "weather.clear"

    def test_snow_when_cold_rain(self):
        """温度 ≤ 0°C 且有降水 → 使用 weather.snow。"""
        i18n = FakeI18n()
        engine = _make_engine(params=_make_params(temp=-5.0, rain=2.0))
        handle = make_weather_handler(engine, i18n)["get_weather"]
        handle(_request([[0, 0]]))
        assert "weather.snow" in i18n.calls
        assert "weather.rain" not in i18n.calls

    def test_rain_when_warm(self):
        """温度 > 0°C 且有降水 → 使用 weather.rain。"""
        i18n = FakeI18n()
        engine = _make_engine(params=_make_params(temp=15.0, rain=2.0))
        handle = make_weather_handler(engine, i18n)["get_weather"]
        handle(_request([[0, 0]]))
        assert "weather.rain" in i18n.calls


class TestWeatherHandlerValidation:
    """输入防护测试 —— 单条坏数据不毁掉整批。"""

    def test_malformed_coord_skipped_valid_kept(self):
        """畸形坐标跳过，其余合法坐标正常返回。"""
        handle = make_weather_handler(_make_engine(), FakeI18n())["get_weather"]
        resp = handle(_request([["abc", "def"], [3, 4]]))
        results = resp["payload"]["weathers"]
        assert len(results) == 1
        assert results[0]["cx"] == 3
        assert results[0]["cy"] == 4

    def test_short_coord_skipped(self):
        """长度不足 2 的坐标跳过。"""
        handle = make_weather_handler(_make_engine(), FakeI18n())["get_weather"]
        resp = handle(_request([[1]]))
        assert resp["payload"]["weathers"] == []

    def test_non_list_coord_skipped(self):
        """非序列坐标（int/None/dict）跳过。"""
        handle = make_weather_handler(_make_engine(), FakeI18n())["get_weather"]
        resp = handle(_request([5, None, {"x": 1}, [0, 0]]))
        assert len(resp["payload"]["weathers"]) == 1

    def test_bool_coord_skipped(self):
        """bool 元素（int 子类）跳过。"""
        handle = make_weather_handler(_make_engine(), FakeI18n())["get_weather"]
        resp = handle(_request([[True, False]]))
        assert resp["payload"]["weathers"] == []

    def test_whole_float_accepted(self):
        """JSON 传输产生的整值浮点（3.0）接受并取整。"""
        handle = make_weather_handler(_make_engine(), FakeI18n())["get_weather"]
        resp = handle(_request([[3.0, -4.0]]))
        results = resp["payload"]["weathers"]
        assert len(results) == 1
        assert results[0]["cx"] == 3
        assert results[0]["cy"] == -4

    def test_fractional_float_skipped(self):
        """非整值浮点（10.9）跳过——不静默截断到错误 chunk。"""
        handle = make_weather_handler(_make_engine(), FakeI18n())["get_weather"]
        resp = handle(_request([[10.9, 20.9]]))
        assert resp["payload"]["weathers"] == []

    def test_chunks_not_a_list(self):
        """chunks 非 list（字符串）→ 空结果，不抛异常。"""
        handle = make_weather_handler(_make_engine(), FakeI18n())["get_weather"]
        resp = handle(_request("abc"))
        assert resp["payload"]["weathers"] == []

    def test_batch_capped(self):
        """超过 MAX_WEATHER_QUERY_CHUNKS 的部分被截断。"""
        engine = _make_engine()
        handle = make_weather_handler(engine, FakeI18n())["get_weather"]
        coords = [[i, 0] for i in range(MAX_WEATHER_QUERY_CHUNKS + 10)]
        resp = handle(_request(coords))
        assert len(resp["payload"]["weathers"]) == MAX_WEATHER_QUERY_CHUNKS
        assert engine.get_weather_report.call_count == MAX_WEATHER_QUERY_CHUNKS
