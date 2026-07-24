"""区块统计分区 — 每帧从世界脚本拉取各计数器。
"""

class_name ChunkSection
extends "res://scripts/ui/debug_section.gd"


var _world: Node = null

var loaded_count: int = 0
var cached_count: int = 0
var pending_count: int = 0


func _init() -> void:
	label = "区块"


func setup(world: Node) -> void:
	_world = world


func process_section(_delta: float) -> void:
	if _world == null or not _world.has_method("get_debug_chunk_stats"):
		return
	var stats: Dictionary = _world.get_debug_chunk_stats()
	loaded_count = stats.get("loaded", 0)
	cached_count = stats.get("cached", 0)
	pending_count = stats.get("pending", 0)


func get_lines() -> PackedStringArray:
	return PackedStringArray([
		"已加载: %d  缓存: %d" % [loaded_count, cached_count],
		"待发送: %d" % pending_count,
	])
