"""玩家分区 — 每帧从世界脚本拉取世界坐标、区块与海拔。
"""

class_name PlayerSection
extends "res://scripts/ui/debug_section.gd"


var _world: Node = null

## 玩家世界坐标
var world_pos: Vector2 = Vector2.ZERO

## 玩家所在区块
var chunk: Vector2i = Vector2i.ZERO

## 玩家脚下海拔（米）
var elevation: float = 0.0


## 构造函数：设置分区标签为"玩家"。
func _init() -> void:
	label = "玩家"


## 缓存世界脚本引用，供 process_section 拉取玩家信息。
##
## Args:
##     world: 世界脚本节点（MainWorld 或 MainWorld3D）。
func setup(world: Node) -> void:
	_world = world


## 每帧从世界脚本 get_debug_player_info 拉取玩家世界坐标、
## 所在区块与脚下海拔，结果为空字典时保持上次数据不变。
##
## Args:
##     _delta: 帧间隔（秒），本分区不使用。
func process_section(_delta: float) -> void:
	if _world == null or not _world.has_method("get_debug_player_info"):
		return
	var info: Dictionary = _world.get_debug_player_info()
	if info.is_empty():
		return
	world_pos = info.get("world_pos", Vector2.ZERO)
	chunk = info.get("chunk", Vector2i.ZERO)
	elevation = info.get("elevation", 0.0)


## 生成玩家分区文本行：世界坐标与区块，以及脚下海拔（米）。
##
## Returns:
##     两行 PackedStringArray（坐标行 + 海拔行）。
func get_lines() -> PackedStringArray:
	return PackedStringArray([
		"坐标: (%d, %d)  |  区块: (%d, %d)" % [int(world_pos.x), int(world_pos.y), chunk.x, chunk.y],
		"海拔: %d m" % int(elevation),
	])
