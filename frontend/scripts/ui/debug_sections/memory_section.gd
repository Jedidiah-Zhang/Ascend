"""内存与节点统计分区 — 自取型，数据来自 Performance 单例。
"""

class_name MemorySection
extends "res://scripts/ui/debug_section.gd"


## 构造函数：设置分区标签为"内存"。
func _init() -> void:
	label = "内存"


## 生成内存分区文本行：从 Performance 单例读取静态内存/视频内存
## （换算为 MB）与场景树节点数，自取型不依赖世界脚本。
##
## Returns:
##     两行 PackedStringArray（内存行 + 节点数行）。
func get_lines() -> PackedStringArray:
	var static_mem := Performance.get_monitor(Performance.MEMORY_STATIC) / 1048576.0
	var video_mem := Performance.get_monitor(Performance.RENDER_VIDEO_MEM_USED) / 1048576.0
	var node_count := Performance.get_monitor(Performance.OBJECT_NODE_COUNT)
	return PackedStringArray([
		"静态: %.1f MB  |  视频: %.1f MB" % [static_mem, video_mem],
		"节点数: %d" % node_count,
	])
