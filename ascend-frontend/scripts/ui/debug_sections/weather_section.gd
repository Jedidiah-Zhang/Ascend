"""天气分区 — 由 get_weather 轮询响应更新（唯一数据源）。

数据通过 update_from_backend() 注入（weather_handler 响应）：
  - {"weather": "晴", "temperature": 24.5, "temp_perception": "温暖",
     "humidity": 68.0, "hum_perception": "舒适",
     "wind_speed": 3.2, "wind_perception": "微风",
     "sunshine": 10.7, "sun_perception": "适中",
     "sunrise": 6.2, "sunset": 18.8,
     "sunshine_intensity": 0.85, "light_perception": "明亮"}

感知标签由后端按显示值（四舍五入后）分类并翻译，前端只做展示。
"""

class_name WeatherSection
extends DebugSection


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


func _init() -> void:
	label = "天气"


func update_from_backend(data: Dictionary) -> void:
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

	# 温度 / 湿度 / 风速
	var meteo: PackedStringArray = []
	if _has_temp:
		meteo.append("%.1f°C(%s)" % [temperature, temp_perception])
	if _has_hum:
		meteo.append("%.0f%%(%s)" % [humidity, hum_perception])
	if _has_wind:
		meteo.append("%.1f m/s(%s)" % [wind_speed, wind_perception])
	if not meteo.is_empty():
		lines.append("  ".join(meteo))

	# 日照时长行
	if _has_sun:
		lines.append("日照 %.1fh(%s)" % [sunshine, sun_perception])

	# 光照强度 + 日出→日落
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
