"""游戏时间分区 — 事件型，显示当前游戏时间（日/时/分）。

数据来自 Calendar 发布的 minute_change 事件。
"""

class_name TimeSection
extends DebugSection


var day: int = 0
var hour: int = 0
var minute: int = 0
var _has_data: bool = false


func _init() -> void:
	label = "时间"


func update_from_backend(data: Dictionary) -> void:
	if data.has("day"):
		day = int(data["day"])
	if data.has("hour"):
		hour = int(data["hour"])
	if data.has("minute"):
		minute = int(data["minute"])
	_has_data = true


func get_lines() -> PackedStringArray:
	if not _has_data:
		return PackedStringArray(["时间: —"])
	return PackedStringArray([
		"第 %d 天  %02d:%02d" % [day, hour, minute],
	])
