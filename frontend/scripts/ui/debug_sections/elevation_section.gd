"""地形分区 — 显示当前地块海拔与坡度。

仅在玩家移动到新 tile 时查询世界脚本，避免每帧字典遍历。
"""

class_name ElevationSection
extends "res://scripts/ui/debug_section.gd"


## 当前玩家所在格海拔
var elevation_value: int = 0

## 当前格坡度（度数）
var slope_value: float = 0.0

## 是否已收到后端数据
var _has_data: bool = false

var _world: Node = null
var _last_tile_pos: Vector2i = Vector2i(-999999, -999999)


func _init() -> void:
	label = "地形"


func setup(world: Node) -> void:
	_world = world


func process_section(_delta: float) -> void:
	if _world == null or not _world.has_method("get_debug_terrain_at"):
		return
	var player_info: Dictionary = _world.get_debug_player_info()
	var world_pos: Vector2 = player_info.get("world_pos", Vector2.ZERO)
	var tile_pos := Vector2i(int(world_pos.x), int(world_pos.y))
	if tile_pos == _last_tile_pos:
		return

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

	if all_received:
		_last_tile_pos = tile_pos


func get_lines() -> PackedStringArray:
	if not _has_data:
		return PackedStringArray(["海拔: —  |  坡度: —"])
	return PackedStringArray([
		"海拔: %d  |  坡度: %.1f°" % [elevation_value, slope_value],
	])
