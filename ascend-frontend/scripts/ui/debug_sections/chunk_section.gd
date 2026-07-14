"""区块统计分区 — 推送型，外部每帧设置各计数器。
"""

class_name ChunkSection
extends "res://scripts/ui/debug_section.gd"


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


func get_lines() -> PackedStringArray:
	return PackedStringArray([
		"已加载: %d  |  放置中: %d" % [loaded_count, being_placed_count],
		"缓存: %d  |  待发送: %d" % [cached_count, pending_count],
	])
