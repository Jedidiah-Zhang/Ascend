"""气候分区 — 显示年均基线（温度/湿度）与气候带，均来自 chunk 数据。

实时温度/湿度看天气分区（get_weather 轮询）。三者独立追踪，未收到的显示 "—"。
"""

class_name ClimateSection
extends DebugSection


const CLIMATE_LABELS: Array[String] = [
	"热带雨林", "热带草原", "沙漠", "草原",
	"温带森林", "亚寒带针叶林", "极地苔原", "高山",
]


## 温度（摄氏度）
var temperature: float = 0.0

## 是否已收到温度数据
var _has_temp: bool = false

## 湿度（%）
var humidity: float = 0.0

## 是否已收到湿度数据
var _has_humidity: bool = false

## 气候带编码，-1 表示未知
var climate_zone: int = -1


func _init() -> void:
	label = "气候"


func update_from_backend(data: Dictionary) -> void:
	if data.has("temperature"):
		temperature = float(data["temperature"])
		_has_temp = true
	if data.has("humidity"):
		humidity = float(data["humidity"])
		_has_humidity = true
	if data.has("climate_zone"):
		climate_zone = int(data["climate_zone"])


func get_lines() -> PackedStringArray:
	var temp_str := "%.1f°C" % temperature if _has_temp else "—"
	var humid_str := "%.0f%%" % humidity if _has_humidity else "—"
	var zone_str := CLIMATE_LABELS[climate_zone] if climate_zone >= 0 and climate_zone < CLIMATE_LABELS.size() else "—"
	return PackedStringArray([
		"年均温: %s  |  年均湿度: %s" % [temp_str, humid_str],
		"气候: %s" % zone_str,
	])
