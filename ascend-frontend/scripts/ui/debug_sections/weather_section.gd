"""天气分区 — 事件型，显示当前降水状态。

数据通过 update_from_backend() 注入：
  - precipitation_start: {"weather": "雨 (2.3 mm/h)"}
  - precipitation_stop:  {"weather": "晴"}
  - sunshine_change:    暂不显示，仅保留接口
"""

class_name WeatherSection
extends DebugSection


## 当前天气描述
var current_weather: String = "晴"

## 是否已收到天气数据
var _has_data: bool = true


func _init() -> void:
	label = "天气"


func update_from_backend(data: Dictionary) -> void:
	if data.has("weather"):
		current_weather = str(data["weather"])
		_has_data = true


func get_lines() -> PackedStringArray:
	if not _has_data:
		return PackedStringArray(["天气: —"])
	return PackedStringArray(["天气: %s" % current_weather])
