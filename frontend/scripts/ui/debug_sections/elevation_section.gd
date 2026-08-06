"""地形分区 — 显示当前地块海拔与坡度。

仅在玩家移动到新 tile 时查询世界脚本，避免每帧字典遍历。
"""

class_name ElevationSection
extends "res://scripts/ui/debug_sections/tile_polling_section.gd"


## 当前玩家所在格海拔
var elevation_value: int = 0

## 当前格坡度（度数）
var slope_value: float = 0.0

## 是否已收到后端数据
var _has_data: bool = false


## 构造函数：设置分区标签为"地形"。
func _init() -> void:
	label = "地形"


## 查询当前 tile 的地形数据：从世界脚本 get_debug_terrain_at 拉取
## 海拔与坡度，逐字段刷新；缺任一字段则返回未就绪。
##
## Args:
##     world_pos: 玩家当前世界坐标（跨格后才调用）。
##
## Returns:
##     海拔与坡度两个字段全部收到返回 true。
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


## 生成地形分区文本行：海拔与坡度；尚未收到任何数据时显示占位行"—"。
##
## Returns:
##     单行 PackedStringArray（占位或数据行）。
func get_lines() -> PackedStringArray:
	if not _has_data:
		return PackedStringArray(["海拔: —  |  坡度: —"])
	return PackedStringArray([
		"海拔: %d  |  坡度: %.1f°" % [elevation_value, slope_value],
	])
