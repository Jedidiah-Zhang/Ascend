"""相机分区 — 推送型，外部每帧设置相机位置与缩放。
"""

class_name CameraSection
extends "res://scripts/ui/debug_section.gd"


## 相机世界位置
var position: Vector2 = Vector2.ZERO

## 相机缩放
var zoom: Vector2 = Vector2.ONE


func _init() -> void:
	label = "相机"


func get_lines() -> PackedStringArray:
	return PackedStringArray([
		"位置: (%d, %d)" % [int(position.x), int(position.y)],
		"缩放: %.2fx" % zoom.x,
	])
