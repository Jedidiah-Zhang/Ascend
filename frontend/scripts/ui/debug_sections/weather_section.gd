"""天气分区 — 每 0.5 秒通过 Connection 轮询 get_weather，自行管理计时与响应处理。

数据通过 on_world_response("get_weather") 接收，与具体世界脚本解耦。
"""

class_name WeatherSection
extends "res://scripts/ui/debug_section.gd"


## 当前天气描述（降水）
var current_weather: String = "晴"

var temperature: float = 0.0
var temp_perception: String = ""
var _has_temp: bool = false

var humidity: float = 0.0
var hum_perception: String = ""
var _has_hum: bool = false

var wind_speed: float = 0.0
var wind_perception: String = ""
var _has_wind: bool = false

var sunshine: float = 0.0
var sun_perception: String = ""
var _has_sun: bool = false

var sunrise: float = 0.0
var sunset: float = 0.0
var _has_daylight_info: bool = false

var sunshine_intensity: float = 0.0
var light_perception: String = ""
var _has_intensity: bool = false

## 轮询累积计时器（秒）
var _query_accum: float = 0.0

var _world: Node = null


func _init() -> void:
	label = "天气"


func setup(world: Node) -> void:
	_world = world


func process_section(delta: float) -> void:
	_query_accum += delta
	if _query_accum < 0.5:
		return
	_query_accum = 0.0

	if Connection.status != Connection.Status.CONNECTED:
		return

	var chunk: Vector2i = Vector2i.ZERO
	if _world and _world.has_method("get_debug_player_info"):
		var info: Dictionary = _world.get_debug_player_info()
		chunk = info.get("chunk", Vector2i.ZERO)

	Connection.send({
		"type": "request",
		"request_type": "get_weather",
		"payload": {"chunks": [[chunk.x, chunk.y]]},
	})


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
		temp_perception = str(data.get("temp_perception", ""))
		_has_temp = true
	if data.has("humidity"):
		humidity = float(data["humidity"])
		hum_perception = str(data.get("hum_perception", ""))
		_has_hum = true
	if data.has("wind_speed"):
		wind_speed = float(data["wind_speed"])
		wind_perception = str(data.get("wind_perception", ""))
		_has_wind = true
	if data.has("sunshine"):
		sunshine = float(data["sunshine"])
		sun_perception = str(data.get("sun_perception", ""))
		_has_sun = true
	if data.has("sunrise"):
		sunrise = float(data["sunrise"])
		sunset = float(data.get("sunset", 0.0))
		_has_daylight_info = true
	if data.has("sunshine_intensity"):
		sunshine_intensity = float(data["sunshine_intensity"])
		light_perception = str(data.get("light_perception", ""))
		_has_intensity = true


func get_lines() -> PackedStringArray:
	var lines: PackedStringArray = []
	lines.append("天气: %s" % current_weather)

	var meteo: PackedStringArray = []
	if _has_temp:
		meteo.append("%.1f°C(%s)" % [temperature, temp_perception])
	if _has_hum:
		meteo.append("%.0f%%(%s)" % [humidity, hum_perception])
	if _has_wind:
		meteo.append("%.1f m/s(%s)" % [wind_speed, wind_perception])
	if not meteo.is_empty():
		lines.append("  ".join(meteo))

	if _has_sun:
		lines.append("日照 %.1fh(%s)" % [sunshine, sun_perception])

	var sun_parts: PackedStringArray = []
	if _has_intensity:
		sun_parts.append("光照 %.2f(%s)" % [sunshine_intensity, light_perception])
	if _has_daylight_info:
		var sr_h: int = int(sunrise)
		var sr_m: int = int((sunrise - sr_h) * 60)
		var ss_h: int = int(sunset)
		var ss_m: int = int((sunset - ss_h) * 60)
		sun_parts.append("日出 %02d:%02d → 日落 %02d:%02d" % [sr_h, sr_m, ss_h, ss_m])
	if not sun_parts.is_empty():
		lines.append("  ".join(sun_parts))

	return lines
