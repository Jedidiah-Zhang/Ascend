"""天气分区 — 展示玩家所在 chunk 的实时天气。

数据经 on_world_response("get_weather") 接收：main_world 负责轮询
get_weather（1s 间隔）并广播响应，本分区不再自行轮询（单一 poller）。
"""

class_name WeatherSection
extends "res://scripts/ui/debug_section.gd"


## 当前天气描述（后端返回的本地化文案；未收到数据时为空）
var current_weather: String = ""

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


## 构造函数：设置分区标签翻译键。
func _init() -> void:
	label_key = "debug.section.weather"


## 响应 get_weather 响应：取 weathers 数组首个条目交给 _apply_weather_data
## 逐字段刷新（数据由 main_world 以 1s 间隔轮询，本分区不再自行拉取）。
##
## Args:
##     request_type: 请求类型，仅处理 "get_weather"。
##     payload: 响应载荷，weathers 为天气条目数组。
func on_world_response(request_type: String, payload: Dictionary) -> void:
	if request_type != "get_weather":
		return
	var weathers: Array = payload.get("weathers", [])
	if weathers.size() > 0:
		_apply_weather_data(weathers[0])


## 应用单条天气数据：按字段存在性逐项刷新降水描述、温度/湿度/风速
## （各带分级 L 值）、日照时长、日出日落时间与光照强度，
## 仅更新响应中已提供的字段，并相应置位对应 _has_* 标记。
##
## Args:
##     data: 单个 weathers 条目字典（后端 get_weather 返回格式）。
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


## 生成天气分区文本行：第一行恒为降水描述；其后按已收到的字段
## 拼装气象（温度/湿度/风速）、日照时长与光照/日出日落行，
## 日出日落时间用 SaveInfoFormatter 格式化为 HH:MM。
##
## Returns:
##     按可用字段数量生成的 PackedStringArray。
func get_lines() -> PackedStringArray:
	var lines: PackedStringArray = []
	var weather_name: String = current_weather if not current_weather.is_empty() else "—"
	lines.append(TranslationServer.tr("debug.weather_label").format({"name": weather_name}))

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
		lines.append(TranslationServer.tr("debug.weather_sunshine").format({
			"hours": "%.1f" % sunshine, "level": sun_tier}))

	var sun_parts: PackedStringArray = []
	if _has_intensity:
		sun_parts.append(TranslationServer.tr("debug.weather_light").format({
			"intensity": "%.2f" % sunshine_intensity, "level": light_tier}))
	if _has_daylight_info:
		var sr_h: int = int(sunrise)
		var sr_m: int = int((sunrise - sr_h) * 60)
		var ss_h: int = int(sunset)
		var ss_m: int = int((sunset - ss_h) * 60)
		sun_parts.append(TranslationServer.tr("debug.weather_sun_times").format({
			"sunrise": SaveInfoFormatter.hhmm_string(sr_h, sr_m),
			"sunset": SaveInfoFormatter.hhmm_string(ss_h, ss_m)}))
	if not sun_parts.is_empty():
		lines.append("  ".join(sun_parts))

	return lines
