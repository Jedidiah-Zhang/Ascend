"""内存与节点统计分区 — 自取型，数据来自 Performance 单例。
"""

class_name MemorySection
extends "res://scripts/ui/debug_section.gd"


func _init() -> void:
	label = "内存"


func get_lines() -> PackedStringArray:
	var static_mem := Performance.get_monitor(Performance.MEMORY_STATIC) / 1048576.0
	var video_mem := Performance.get_monitor(Performance.RENDER_VIDEO_MEM_USED) / 1048576.0
	var node_count := Performance.get_monitor(Performance.OBJECT_NODE_COUNT)
	return PackedStringArray([
		"静态: %.1f MB  |  视频: %.1f MB" % [static_mem, video_mem],
		"节点数: %d" % node_count,
	])
