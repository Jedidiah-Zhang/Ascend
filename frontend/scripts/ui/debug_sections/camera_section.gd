"""相机分区 — 每帧从世界脚本拉取相机位置与视野参数。
"""

class_name CameraSection
extends "res://scripts/ui/debug_section.gd"


var _world: Node = null

## 相机世界位置
var position: Vector2 = Vector2.ZERO

## 相机视野参数描述（格式由世界脚本决定，如 "缩放: 1.50x" 或 "距离: 400m"）
var _camera_display: String = ""


## 构造函数：设置分区标签翻译键。
func _init() -> void:
	label_key = "debug.section.camera"


## 缓存世界脚本引用，供 process_section 拉取相机数据。
##
## Args:
##     world: 世界脚本节点（MainWorld 或 MainWorld3D）。
func setup(world: Node) -> void:
	_world = world


## 每帧从世界脚本 get_debug_camera_info 拉取相机世界位置与视野描述，
## 结果为空字典时保持上次数据不变。
##
## Args:
##     _delta: 帧间隔（秒），本分区不使用。
func process_section(_delta: float) -> void:
	if _world == null or not _world.has_method("get_debug_camera_info"):
		return
	var info: Dictionary = _world.get_debug_camera_info()
	if info.is_empty():
		return
	position = info.get("position", Vector2.ZERO)
	_camera_display = info.get("camera_display", "")


## 生成相机分区文本行：世界坐标与视野参数描述（未获取到时显示"—"）。
##
## Returns:
##     两行 PackedStringArray（位置行 + 视野行）。
func get_lines() -> PackedStringArray:
	return PackedStringArray([
		TranslationServer.tr("debug.camera_position").format({
			"x": int(position.x), "y": int(position.y)}),
		_camera_display if not _camera_display.is_empty() else "—",
	])
