"""区块统计分区 — 每帧从世界脚本拉取各计数器。
"""

class_name ChunkSection
extends "res://scripts/ui/debug_section.gd"


var _world: Node = null

## 已加载完成且绘制到 TileMapLayer 的区块数
var loaded_count: int = 0

## 正在分批放置 tile 的区块数
var being_placed_count: int = 0

## 已收到地形数据但尚未绘制的缓存区块数
var cached_count: int = 0

## 等待后端响应的区块数
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
	being_placed_count = stats.get("placing", 0)
	cached_count = stats.get("cached", 0)
	pending_count = stats.get("pending", 0)


func get_lines() -> PackedStringArray:
	return PackedStringArray([
		"已加载: %d  |  放置中: %d" % [loaded_count, being_placed_count],
		"缓存: %d  |  待发送: %d" % [cached_count, pending_count],
	])
