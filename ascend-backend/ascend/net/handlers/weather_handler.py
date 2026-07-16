"""天气查询处理程序 — 通过 get_weather API 返回任意 chunk 的当前天气。

通过 make_weather_handler() 工厂函数创建，返回 {request_type: handler} 映射。
"""

from ascend.weather.weather_engine import (
    classify_temperature, classify_humidity, classify_wind, classify_sunshine,
    classify_sunlight_intensity,
)

from ascend.log import get_logger

logger = get_logger(__name__)

PERCEPTION_NS = {
    "temp": "perception.temp",
    "hum": "perception.hum",
    "wind": "perception.wind",
    "sun": "perception.sun",
    "light": "perception.light",
}


def _tr_perception(i18n, category: str, label: str) -> str:
    key = "%s.%s" % (PERCEPTION_NS[category], label)
    return i18n.t(key)


def make_weather_handler(weather_engine, i18n=None):
    """为给定的 WeatherEngine 创建天气查询处理程序。

    Args:
        weather_engine: WeatherEngine 实例。
        i18n: I18n 翻译管理器。

    Returns:
        一个字典，将 "get_weather" 映射到处理函数。
    """

    def handle_get_weather(msg: dict) -> dict:
        payload = msg.get("payload", {})
        coords = payload.get("chunks", [])

        if not coords:
            return {
                "type": "response",
                "request_type": "get_weather",
                "payload": {"weathers": []},
            }

        results = []
        for coord in coords:
            cx, cy = int(coord[0]), int(coord[1])
            wp = weather_engine.get_weather(cx, cy)
            if wp is None:
                continue

            temp = wp.temperature
            hum = wp.humidity
            wind = wp.wind_speed
            sun = wp.sunshine
            rain = wp.rainfall

            if rain > 0:
                precip_type_key = "weather.snow" if temp <= 0 else "weather.rain"
                precip_type = i18n.t(precip_type_key) if i18n else ("雪" if temp <= 0 else "雨")
                weather_desc = (i18n.t("weather.intensity", type=precip_type, intensity="%.1f" % rain)
                                if i18n else "%s (%.1f mm/h)" % (precip_type, rain))
            else:
                weather_desc = i18n.t("weather.clear") if i18n else "晴"

            temp_label = classify_temperature(temp)
            hum_label = classify_humidity(hum)
            wind_label = classify_wind(wind)
            sun_label = classify_sunshine(sun)

            # 日照信息：日出/日落 + 当前强度
            # 传入 get_weather 的降雨值（含暴雨修改器效果），避免 get_daylight_info 内部重复计算
            dl = weather_engine.get_daylight_info(cx, cy, rainfall=rain)
            if dl is not None:
                sunrise_h, sunset_h, daylight_h, intensity = dl
                light_label = classify_sunlight_intensity(intensity)
            else:
                sunrise_h = sunset_h = daylight_h = 0.0
                intensity = 0.0
                light_label = "dark"

            results.append({
                "cx": cx,
                "cy": cy,
                "temperature": round(temp, 1),
                "temp_perception": (_tr_perception(i18n, "temp", temp_label)
                                    if i18n else temp_label),
                "humidity": round(hum, 1),
                "hum_perception": (_tr_perception(i18n, "hum", hum_label)
                                   if i18n else hum_label),
                "wind_speed": round(wind, 1),
                "wind_perception": (_tr_perception(i18n, "wind", wind_label)
                                    if i18n else wind_label),
                "daylight_hours": round(daylight_h, 1),
                "sun_perception": (_tr_perception(i18n, "sun", sun_label)
                                   if i18n else sun_label),
                "sunrise": round(sunrise_h, 1),
                "sunset": round(sunset_h, 1),
                "sunshine_intensity": round(intensity, 2),
                "light_perception": (_tr_perception(i18n, "light", light_label)
                                     if i18n else light_label),
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
