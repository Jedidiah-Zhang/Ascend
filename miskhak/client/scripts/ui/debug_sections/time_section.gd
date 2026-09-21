"""时间分区 — 事件型，由 minute_change 事件驱动更新。
"""

class_name TimeSection
extends "res://scripts/ui/debug_section.gd"


var day: int = 0
var hour: int = 0
var minute: int = 0
var _has_data: bool = false


## 构造函数：设置分区标签翻译键。
func _init() -> void:
	label_key = "debug.section.time"


## 响应 minute_change 事件刷新时间显示：天数取自 data.day，
## 时/分直接取自 payload 的 game_hour/game_minute，并标记已收到数据。
##
## Args:
##     event_type: 事件类型，仅处理 "minute_change"。
##     payload: 事件载荷（含 data.day 与 game_hour/game_minute）。
func on_world_event(event_type: String, payload: Dictionary) -> void:
	if event_type != "minute_change":
		return
	var data: Dictionary = payload.get("data", {})
	day = int(data.get("day", 0))
	hour = int(payload.get("game_hour", 0))
	minute = int(payload.get("game_minute", 0))
	_has_data = true


## 生成时间分区文本行："第 X 天 HH:MM"；尚未收到任何事件时显示"—"。
##
## Returns:
##     单行 PackedStringArray。
func get_lines() -> PackedStringArray:
	if not _has_data:
		return PackedStringArray(["—"])
	return PackedStringArray([
		TranslationServer.tr("ui.format.game_time").format({
			"day": day, "clock": "%02d:%02d" % [hour, minute]}),
	])
