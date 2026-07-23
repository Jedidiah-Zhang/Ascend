"""时间分区 — 事件型，由 minute_change 事件驱动更新。
"""

class_name TimeSection
extends "res://scripts/ui/debug_section.gd"


var day: int = 0
var hour: int = 0
var minute: int = 0
var _has_data: bool = false


func _init() -> void:
	label = "时间"


func on_world_event(event_type: String, payload: Dictionary) -> void:
	if event_type != "minute_change":
		return
	var data: Dictionary = payload.get("data", {})
	day = int(data.get("day", 0))
	hour = int(payload.get("game_hour", 0))
	minute = int(payload.get("game_minute", 0))
	_has_data = true


func get_lines() -> PackedStringArray:
	if not _has_data:
		return PackedStringArray(["—"])
	return PackedStringArray([
		"第 %d 天 %02d:%02d" % [day, hour, minute],
	])
