"""tile 轮询分区基类 — 玩家移动到新 tile 时拉取一次数据的分区模板。

地形/气候分区共用同一"tile 变化检测 + 字段完整性 + all_received 提交"
模板：仅在玩家跨格时查询世界脚本，避免每帧字典遍历。
"""

class_name TilePollingSection
extends "res://scripts/ui/debug_section.gd"


## 玩家所在格是否已收到全部字段数据
var _has_data: bool = false

var _world: Node = null
var _last_tile_pos: Vector2i = Vector2i(-999999, -999999)


func setup(world: Node) -> void:
	_world = world


## 子类实现：查询世界脚本并逐字段刷新，返回是否全部字段就绪。
func _poll(world_pos: Vector2) -> bool:
	return false


func process_section(_delta: float) -> void:
	if _world == null or not _world.has_method("get_debug_player_info"):
		return
	var player_info: Dictionary = _world.get_debug_player_info()
	var world_pos: Vector2 = player_info.get("world_pos", Vector2.ZERO)
	var tile_pos := Vector2i(int(world_pos.x), int(world_pos.y))
	if tile_pos == _last_tile_pos:
		return

	if _poll(world_pos):
		_last_tile_pos = tile_pos
