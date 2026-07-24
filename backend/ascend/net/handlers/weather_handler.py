"""天气查询处理程序 — 通过 get_weather API 返回任意 chunk 的当前天气。

通过 make_weather_handler() 工厂函数创建，返回 {request_type: handler} 映射。

输入防护：逐坐标校验（畸形坐标跳过不毁整批）、批量上限
MAX_WEATHER_QUERY_CHUNKS（防超大请求卡游戏线程）。
所有等级标签从"四舍五入后的显示值"分类，保证面板数值与标签一致。
"""

from ascend.config import MAX_WEATHER_QUERY_CHUNKS
from ascend.net.handlers import parse_coord as _parse_coord
from ascend.weather.weather_engine import (
    classify_temperature, classify_humidity, classify_wind, classify_sunshine,
    classify_sunlight_intensity,
)

from ascend.log import get_logger

logger = get_logger(__name__)


def make_weather_handler(weather_engine, i18n):
    """为给定的 WeatherEngine 创建天气查询处理程序。

    Args:
        weather_engine: WeatherEngine 实例。
        i18n: I18n 翻译管理器（必传）。

    Returns:
        一个字典，将 "get_weather" 映射到处理函数。
    """

    def handle_get_weather(msg: dict) -> dict:
        """处理 get_weather 请求。

        Args:
            msg: 请求消息，payload.chunks 为 [[cx, cy], ...] 坐标列表。

        Returns:
            dict 响应，payload.weathers 为逐 chunk 的天气数据列表
            （非法坐标与未注册 chunk 被跳过）。
        """
        payload = msg.get("payload", {})
        coords = payload.get("chunks", []) if isinstance(payload, dict) else []

        if not isinstance(coords, list):
            logger.warning("get_weather: chunks 非列表（%s），忽略", type(coords).__name__)
            coords = []
        if len(coords) > MAX_WEATHER_QUERY_CHUNKS:
            logger.warning("get_weather: 请求 %d 个 chunk 超上限，截断至 %d",
                           len(coords), MAX_WEATHER_QUERY_CHUNKS)
            coords = coords[:MAX_WEATHER_QUERY_CHUNKS]

        results = []
        for coord in coords:
            parsed = _parse_coord(coord)
            if parsed is None:
                logger.warning("get_weather: 非法坐标 %r，跳过", coord)
                continue
            cx, cy = parsed
            report = weather_engine.get_weather_report(cx, cy)
            if report is None:
                continue
            wp, sunrise_h, sunset_h, _, intensity, sun_azimuth = report

            # 先 round 再 classify —— 显示数值与等级一致
            temp = round(wp.temperature, 1)
            hum = round(wp.humidity, 1)
            wind = round(wp.wind_speed, 1)
            sun = round(wp.sunshine, 1)
            intensity = round(intensity, 2)
            rain = wp.rainfall

            if rain > 0:
                precip_type_key = "weather.snow" if temp <= 0 else "weather.rain"
                weather_desc = i18n.t("weather.intensity",
                                      type=i18n.t(precip_type_key),
                                      intensity="%.1f" % rain)
            else:
                weather_desc = i18n.t("weather.clear")

            results.append({
                "cx": cx,
                "cy": cy,
                "temperature": temp,
                "temp_tier": classify_temperature(temp),
                "humidity": hum,
                "hum_tier": classify_humidity(hum),
                "wind_speed": wind,
                "wind_tier": classify_wind(wind),
                "sunshine": sun,
                "sun_tier": classify_sunshine(sun),
                "sunrise": round(sunrise_h, 1),
                "sunset": round(sunset_h, 1),
                "sun_azimuth": round(sun_azimuth, 1),
                "sunshine_intensity": intensity,
                "light_tier": classify_sunlight_intensity(intensity),
                "weather": weather_desc,
            })

        return {
            "type": "response",
            "request_type": "get_weather",
            "payload": {"weathers": results},
        }

    return {
        "get_weather": handle_get_weather,
    }
