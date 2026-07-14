"""气候分区 — 事件型，数据通过 update_from_backend() 独立注入温度和湿度。

温度来自 temperature_change 事件，湿度来自 humidity_change 事件。
两者独立追踪，未收到的显示 "—"。
"""

class_name ClimateSection
extends DebugSection


## 温度（摄氏度），来自 weather 事件
var temperature: float = 0.0

## 是否已收到温度数据
var _has_temp: bool = false

## 湿度（0-1），来自 weather 事件
var humidity: float = 0.0

## 是否已收到湿度数据
var _has_humidity: bool = false


func _init() -> void:
	label = "气候"


func update_from_backend(data: Dictionary) -> void:
	if data.has("temperature"):
		temperature = float(data["temperature"])
		_has_temp = true
	if data.has("humidity"):
		humidity = float(data["humidity"])
		_has_humidity = true


func get_lines() -> PackedStringArray:
	var temp_str := "%.1f°C" % temperature if _has_temp else "—"
	var humid_str := "%.0f%%" % humidity if _has_humidity else "—"
	return PackedStringArray([
		"温度: %s  |  湿度: %s" % [temp_str, humid_str],
	])
