"""玩家分区 — 推送型，外部每帧设置世界坐标与海拔。
"""

class_name PlayerSection
extends "res://scripts/ui/debug_section.gd"


## 玩家世界坐标
var world_pos: Vector2 = Vector2.ZERO

## 玩家海拔层级
var elevation: int = 0


func _init() -> void:
	label = "玩家"


func get_lines() -> PackedStringArray:
	return PackedStringArray([
		"坐标: (%d, %d)" % [int(world_pos.x), int(world_pos.y)],
		"海拔: %d" % elevation,
	])
