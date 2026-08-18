"""weather 指令组 — 天气查询与强制控制（status/set）。

Mixin，依赖宿主 CommandExecutor 提供的:
  self._weather / self._i18n / self._parse_chunk / self._fmt_hour
"""

from ascend.weather.weather_engine import (
    classify_temperature, classify_humidity, classify_wind,
    classify_sunshine, classify_sunlight_intensity, precip_type_for,
)
from ascend.weather.weather_modifier import WEATHER_MODIFIERS

from .result import CommandResult


class WeatherCommandsMixin:
    """weather 指令组实现。"""

    # weather set 的合法状态词
    _ON_OFF: frozenset = frozenset({"on", "off"})

    def _h_weather(self, args: list[str]) -> CommandResult:
        """处理 weather 指令组：status 查询 / set 强制控制。

        Args:
            args: 参数列表。

        Returns:
            执行结果。
        """
        if self._weather is None:
            return CommandResult(
                success=False,
                output=self._i18n.t("console.weather_unavailable"),
            )
        if not args or args[0].lower() == "status":
            return self._h_weather_status(args[1:] if args else [])
        if args[0].lower() == "set":
            return self._h_weather_set(args[1:])
        return CommandResult(
            success=False, output=self._i18n.t("console.weather_usage"),
        )

    def _h_weather_status(self, args: list[str]) -> CommandResult:
        """处理 weather status [cx cy]：查询指定位置当前天气。

        Args:
            args: 坐标参数（空 或 [cx, cy]）。

        Returns:
            执行结果。
        """
        coord = self._parse_chunk(args)
        if coord is None:
            return CommandResult(
                success=False, output=self._i18n.t("console.weather_usage"),
            )
        cx, cy = coord
        report = self._weather.get_weather_report(cx, cy)
        if report is None:
            return CommandResult(
                success=False,
                output=self._i18n.t(
                    "console.weather_chunk_unregistered", cx=cx, cy=cy,
                ),
            )
        wp, sunrise_h, sunset_h, _, intensity, _ = report

        t = self._i18n.t
        temp = round(wp.temperature, 1)
        hum = round(wp.humidity, 1)
        wind = round(wp.wind_speed, 1)
        sun = round(wp.sunshine, 1)
        light = round(intensity, 2)
        if wp.rainfall > 0:
            precip_key = ("weather.snow" if precip_type_for(temp) == "snow"
                          else "weather.rain")
            precip = t("weather.intensity", type=t(precip_key),
                       intensity=f"{wp.rainfall:.1f}")
        else:
            precip = t("weather.clear")
        lines = [
            t("console.weather_header", cx=cx, cy=cy),
            f"  {t('console.weather_temp')}: {temp}°C"
            f" (tier {classify_temperature(temp)})",
            f"  {t('console.weather_hum')}: {hum}%"
            f" (tier {classify_humidity(hum)})",
            f"  {t('console.weather_wind')}: {wind} m/s"
            f" (tier {classify_wind(wind)})",
            f"  {t('console.weather_sun')}: {sun}h"
            f" (tier {classify_sunshine(sun)})"
            f"  |  {t('console.weather_light')}: {light}"
            f" (tier {classify_sunlight_intensity(light)})",
            f"  {t('console.weather_precip')}: {precip}",
            f"  {t('console.weather_sun_times', sunrise=self._fmt_hour(sunrise_h), sunset=self._fmt_hour(sunset_h))}",
        ]
        return CommandResult(success=True, output="\n".join(lines))

    def _h_weather_set(self, args: list[str]) -> CommandResult:
        """处理 weather set <rain|modifier> <on|off> [cx cy]。

        Args:
            args: [target, state, cx?, cy?]。

        Returns:
            执行结果。
        """
        if len(args) < 2:
            return CommandResult(
                success=False, output=self._i18n.t("console.weather_usage"),
            )
        target = args[0].lower()
        state = args[1].lower()
        valid_targets = ["rain"] + list(WEATHER_MODIFIERS.keys())
        if target not in valid_targets:
            return CommandResult(
                success=False,
                output=self._i18n.t(
                    "console.weather_target_unknown",
                    name=target, targets=", ".join(valid_targets),
                ),
            )
        if state not in self._ON_OFF:
            return CommandResult(
                success=False,
                output=self._i18n.t("console.weather_state_invalid"),
            )
        coord = self._parse_chunk(args[2:])
        if coord is None:
            return CommandResult(
                success=False, output=self._i18n.t("console.weather_usage"),
            )
        cx, cy = coord
        active = state == "on"

        if target == "rain":
            changed = self._weather.set_rain(cx, cy, active)
        else:
            changed = self._weather.set_modifier(cx, cy, target, active)

        if changed is None:
            return CommandResult(
                success=False,
                output=self._i18n.t(
                    "console.weather_chunk_unregistered", cx=cx, cy=cy,
                ),
            )
        if not changed:
            return CommandResult(
                success=True,
                output=self._i18n.t(
                    "console.weather_set_noop", target=target, cx=cx, cy=cy,
                ),
            )
        key = "console.weather_set_on" if active else "console.weather_set_off"
        return CommandResult(
            success=True,
            output=self._i18n.t(key, target=target, cx=cx, cy=cy),
        )
