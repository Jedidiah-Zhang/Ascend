"""天气分区 — 展示玩家所在 chunk 的实时天气。

数据经 on_world_response("get_weather") 接收：main_world 负责轮询
get_weather（1s 间隔）并广播响应，本分区不再自行轮询（单一 poller）。
"""

class_name WeatherSection
extends "res://scripts/ui/debug_section.gd"


## 当前天气描述（降水）
var current_weather: String = "晴"

var temperature: float = 0.0
var temp_tier: int = -1
var _has_temp: bool = false

var humidity: float = 0.0
var hum_tier: int = -1
var _has_hum: bool = false

var wind_speed: float = 0.0
var wind_tier: int = -1
var _has_wind: bool = false

var sunshine: float = 0.0
var sun_tier: int = -1
var _has_sun: bool = false

var sunrise: float = 0.0
var sunset: float = 0.0
var _has_daylight_info: bool = false

var sunshine_intensity: float = 0.0
var light_tier: int = -1
var _has_intensity: bool = false


func _init() -> void:
	label = "天气"


func on_world_response(request_type: String, payload: Dictionary) -> void:
	if request_type != "get_weather":
		return
	var weathers: Array = payload.get("weathers", [])
	if weathers.size() > 0:
		_apply_weather_data(weathers[0])


func _apply_weather_data(data: Dictionary) -> void:
	if data.has("weather"):
		current_weather = str(data["weather"])
	if data.has("temperature"):
		temperature = float(data["temperature"])
		temp_tier = int(data.get("temp_tier", -1))
		_has_temp = true
	if data.has("humidity"):
		humidity = float(data["humidity"])
		hum_tier = int(data.get("hum_tier", -1))
		_has_hum = true
	if data.has("wind_speed"):
		wind_speed = float(data["wind_speed"])
		wind_tier = int(data.get("wind_tier", -1))
		_has_wind = true
	if data.has("sunshine"):
		sunshine = float(data["sunshine"])
		sun_tier = int(data.get("sun_tier", -1))
		_has_sun = true
	if data.has("sunrise"):
		sunrise = float(data["sunrise"])
		sunset = float(data.get("sunset", 0.0))
		_has_daylight_info = true
	if data.has("sunshine_intensity"):
		sunshine_intensity = float(data["sunshine_intensity"])
		light_tier = int(data.get("light_tier", -1))
		_has_intensity = true


func get_lines() -> PackedStringArray:
	var lines: PackedStringArray = []
	lines.append("天气: %s" % current_weather)

	var meteo: PackedStringArray = []
	if _has_temp:
		meteo.append("%.1f°C(L%d)" % [temperature, temp_tier])
	if _has_hum:
		meteo.append("%.0f%%(L%d)" % [humidity, hum_tier])
	if _has_wind:
		meteo.append("%.1f m/s(L%d)" % [wind_speed, wind_tier])
	if not meteo.is_empty():
		lines.append("  ".join(meteo))

	if _has_sun:
		lines.append("日照 %.1fh(L%d)" % [sunshine, sun_tier])

	var sun_parts: PackedStringArray = []
	if _has_intensity:
		sun_parts.append("光照 %.2f(L%d)" % [sunshine_intensity, light_tier])
	if _has_daylight_info:
		var sr_h: int = int(sunrise)
		var sr_m: int = int((sunrise - sr_h) * 60)
		var ss_h: int = int(sunset)
		var ss_m: int = int((sunset - ss_h) * 60)
		sun_parts.append("日出 %s → 日落 %s" % [
			SaveInfoFormatter.hhmm_string(sr_h, sr_m),
			SaveInfoFormatter.hhmm_string(ss_h, ss_m)])
	if not sun_parts.is_empty():
		lines.append("  ".join(sun_parts))

	return lines
