"""2D 正俯视相机 — Camera2D 摆位/缩放与可视范围计算。

从 3D 等轴版迁移（原 camera_rig.gd）：正交摆位/阴影覆盖几何删除，
改为 Camera2D 跟随 + 滚轮缩放（zoom 倍率钳制）。几何计算仅保留
visible_radius（2D 屏幕对角线 → 世界 tile 半径，流式半径用）。
"""

class_name CameraRig
extends RefCounted


## 相机常量（与 main_world.gd 原声明同源，见 Config）
const CAMERA_ZOOM_DEFAULT: float = Config.CAMERA_ZOOM_DEFAULT
const CAMERA_ZOOM_STEP: float = Config.CAMERA_ZOOM_STEP
const CAMERA_ZOOM_MIN: float = Config.CAMERA_ZOOM_MIN
const CAMERA_ZOOM_MAX: float = Config.CAMERA_ZOOM_MAX
const TILE_PIXEL_SIZE: float = Config.TILE_PIXEL_SIZE

## 无绑定相机时 visible_radius 的兜底视口尺寸（单元测试用）
const FALLBACK_VIEWPORT: Vector2 = Vector2(1280, 720)


var _camera: Camera2D


func bind(camera: Camera2D) -> void:
	"""绑定相机节点（main_world._ready 时调用；null 时操作静默跳过）。"""
	_camera = camera


## 一次性配置：默认缩放并应用当前焦点/缩放。
func configure(focus: Vector2, zoom: Vector2) -> void:
	if _camera == null:
		return
	apply(focus, zoom)


## 处理滚轮缩放输入：zoom 按步长倍乘并钳制在最小/最大范围内。
##
## Returns:
##     新的缩放（未缩放时返回原值）。
func process_zoom(zoom: Vector2) -> Vector2:
	if _camera == null:
		return zoom
	var step: float = 0.0
	if Input.is_action_just_pressed("zoom_in"):
		step = 1.0
	elif Input.is_action_just_pressed("zoom_out"):
		step = -1.0
	if step == 0.0:
		return zoom
	var new_zoom: float = clampf(
		zoom.x * pow(CAMERA_ZOOM_STEP, step), CAMERA_ZOOM_MIN, CAMERA_ZOOM_MAX)
	return Vector2(new_zoom, new_zoom)


## 按焦点与缩放摆位相机（Camera2D 锚定屏幕中心，zoom 向量等倍缩放）。
func apply(focus: Vector2, zoom: Vector2) -> void:
	if _camera == null:
		return
	_camera.position = focus
	_camera.zoom = zoom


## 相机在指定缩放下可视世界的半对角线半径（世界 tile 单位）：
## 屏幕对角线/2 换算为世界像素，再除 TILE_PIXEL_SIZE 与缩放。
## 纯函数：未绑定相机时用兜底视口尺寸。
func visible_radius(zoom: float, viewport_size: Vector2 = Vector2.ZERO) -> float:
	var view: Vector2 = viewport_size
	if view == Vector2.ZERO:
		view = FALLBACK_VIEWPORT
	return (view * 0.5).length() / (TILE_PIXEL_SIZE * zoom)