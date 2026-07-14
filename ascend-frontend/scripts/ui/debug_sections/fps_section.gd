"""FPS 与帧耗时分区 — 自取型，数据来自 Engine/Performance 单例。
"""

class_name FPSSection
extends "res://scripts/ui/debug_section.gd"


func _init() -> void:
	label = "性能"


func get_lines() -> PackedStringArray:
	var fps := Engine.get_frames_per_second()
	var frame_ms: float = 1000.0 / maxf(fps, 1.0)
	var process_ms := Performance.get_monitor(Performance.TIME_PROCESS) * 1000.0
	return PackedStringArray([
		"FPS: %d  (%.1f ms)" % [fps, frame_ms],
		"进程耗时: %.2f ms" % process_ms,
	])
