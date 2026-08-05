"""地形分区 — 显示当前地块海拔与坡度。

仅在玩家移动到新 tile 时查询世界脚本，避免每帧字典遍历。
"""

class_name ElevationSection
extends "res://scripts/ui/debug_sections/tile_polling_section.gd"


## 当前玩家所在格海拔
var elevation_value: int = 0

## 当前格坡度（度数）
var slope_value: float = 0.0


func _init() -> void:
	label = "地形"


func _poll(world_pos: Vector2) -> bool:
	if not _world.has_method("get_debug_terrain_at"):
		return false
	var all_received: bool = true
	var terrain_data: Dictionary = _world.get_debug_terrain_at(world_pos)
	if terrain_data.has("elevation"):
		elevation_value = terrain_data["elevation"]
		_has_data = true
	else:
		all_received = false
	if terrain_data.has("slope"):
		slope_value = terrain_data["slope"]
		_has_data = true
	else:
		all_received = false
	return all_received


func get_lines() -> PackedStringArray:
	if not _has_data:
		return PackedStringArray(["海拔: —  |  坡度: —"])
	return PackedStringArray([
		"海拔: %d  |  坡度: %.1f°" % [elevation_value, slope_value],
	])
