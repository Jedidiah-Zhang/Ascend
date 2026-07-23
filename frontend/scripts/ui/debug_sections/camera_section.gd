"""相机分区 — 每帧从世界脚本拉取相机位置与视野参数。
"""

class_name CameraSection
extends "res://scripts/ui/debug_section.gd"


var _world: Node = null

## 相机世界位置
var position: Vector2 = Vector2.ZERO

## 相机视野参数描述（格式由世界脚本决定，如 "缩放: 1.50x" 或 "距离: 400m"）
var _camera_display: String = ""


func _init() -> void:
	label = "相机"


func setup(world: Node) -> void:
	_world = world


func process_section(_delta: float) -> void:
	if _world == null or not _world.has_method("get_debug_camera_info"):
		return
	var info: Dictionary = _world.get_debug_camera_info()
	if info.is_empty():
		return
	position = info.get("position", Vector2.ZERO)
	_camera_display = info.get("camera_display", "")


func get_lines() -> PackedStringArray:
	return PackedStringArray([
		"位置: (%d, %d)" % [int(position.x), int(position.y)],
		_camera_display if not _camera_display.is_empty() else "—",
	])
