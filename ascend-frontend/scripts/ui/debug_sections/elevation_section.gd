"""地形分区 — 事件型，显示当前地块海拔与坡度。

数据通过 update_from_backend() 注入。
"""

class_name ElevationSection
extends "res://scripts/ui/debug_section.gd"


## 当前玩家所在格海拔
var elevation_value: int = 0

## 当前格坡度（度数）
var slope_value: float = 0.0

## 是否已收到后端数据
var _has_data: bool = false


func _init() -> void:
	label = "地形"


func update_from_backend(data: Dictionary) -> void:
	if data.has("elevation"):
		elevation_value = int(data["elevation"])
		_has_data = true
	if data.has("slope"):
		slope_value = float(data["slope"])
		_has_data = true


func get_lines() -> PackedStringArray:
	if not _has_data:
		return PackedStringArray(["海拔: —  |  坡度: —"])
	return PackedStringArray([
		"海拔: %d  |  坡度: %.1f°" % [elevation_value, slope_value],
	])
