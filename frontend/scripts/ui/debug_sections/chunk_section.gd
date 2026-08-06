"""区块统计分区 — 每帧从世界脚本拉取各计数器。
"""

class_name ChunkSection
extends "res://scripts/ui/debug_section.gd"


var _world: Node = null

var loaded_count: int = 0
var cached_count: int = 0
var pending_count: int = 0


## 构造函数：设置分区标签为"区块"。
func _init() -> void:
	label = "区块"


## 缓存世界脚本引用，供 process_section 拉取区块统计。
##
## Args:
##     world: 世界脚本节点（MainWorld 或 MainWorld3D）。
func setup(world: Node) -> void:
	_world = world


## 每帧从世界脚本 get_debug_chunk_stats 拉取计数器，
## 刷新已加载/缓存/待发送区块数。
##
## Args:
##     _delta: 帧间隔（秒），本分区不使用。
func process_section(_delta: float) -> void:
	if _world == null or not _world.has_method("get_debug_chunk_stats"):
		return
	var stats: Dictionary = _world.get_debug_chunk_stats()
	loaded_count = stats.get("loaded", 0)
	cached_count = stats.get("cached", 0)
	pending_count = stats.get("pending", 0)


## 生成区块统计文本行：已加载/缓存数与待发送数。
##
## Returns:
##     两行 PackedStringArray（加载缓存行 + 待发送行）。
func get_lines() -> PackedStringArray:
	return PackedStringArray([
		"已加载: %d  缓存: %d" % [loaded_count, cached_count],
		"待发送: %d" % pending_count,
	])
