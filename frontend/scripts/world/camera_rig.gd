"""正交相机摆位/缩放与几何计算。

从 main_world.gd 拆出（原 _configure_camera / _apply_camera_transform /
_process_camera / _compute_visible_radius / _compute_shadow_coverage）：
持有 Camera3D 引用做属性写入，几何计算为纯函数（visible_radius /
shadow_coverage），可独立单元测试。
"""

class_name CameraRig
extends RefCounted

const Config = preload("res://scripts/config.gd")

## 相机常量（与 main_world.gd 原声明同源）
const CAMERA_FOV: float = Config.CAMERA_3D_FOV
const CAMERA_DISTANCE_DEFAULT: float = Config.CAMERA_3D_DISTANCE_DEFAULT
const CAMERA_ZOOM_DISTANCE_STEP: float = Config.CAMERA_3D_DISTANCE_STEP
const CAMERA_DISTANCE_MIN: float = Config.CAMERA_3D_DISTANCE_MIN
const CAMERA_DISTANCE_MAX: float = Config.CAMERA_3D_DISTANCE_MAX

## 阴影覆盖范围 = 可视半径 × 该余量（max_distance 是半径语义，阴影相机覆盖可视区 + 边缘外遮挡物余量）
const SHADOW_COVERAGE_MARGIN: float = 1.35
## 太阳高度角低于该值时关闭阴影（与 LightingController 同源，见 Config）
const SHADOW_CUTOFF: float = Config.SHADOW_CUTOFF
## 低角度区间上限：低于该值开始放大覆盖范围、压扁 pancake
const SHADOW_LOW_ANGLE_CEIL: float = Config.SHADOW_LOW_ANGLE_CEIL
## 低角度时覆盖范围的最大放大倍率（低角度阴影被拉长）
const SHADOW_LOW_ANGLE_EXPAND: float = 3.0
## 相机近/远平面紧贴地形 slab 时的余量：最高物体高度 + 安全边距
## （正交投影下阴影范围 = 相机视锥，slab 越薄阴影精度越高）
const SHADOW_TALL_ALLOWANCE: float = 60.0
const SHADOW_SLAB_MARGIN: float = 20.0


var _camera: Camera3D


func bind(camera: Camera3D) -> void:
	"""绑定相机节点（main_world._ready 时调用；null 时操作静默跳过）。"""
	_camera = camera


## 一次性正交配置：size 控制可视范围，near/far 紧贴地形 slab
## （阴影范围 = 相机视锥），并应用当前焦点/距离。
func configure(focus: Vector3, distance: float) -> void:
	if _camera == null:
		return
	# 真正交投影：size 控制可视范围，near/far 紧贴地形 slab。
	# 正交相机下阴影范围 = 相机视锥 → 阴影精度全图一致，缩放自然控制精度。
	_camera.projection = Camera3D.PROJECTION_ORTHOGONAL
	apply(focus, distance, null, 0.5)


## 处理滚轮缩放输入：相机距离按步长增减并钳制在最小/最大范围内。
##
## Returns:
##     新的相机距离（未缩放时返回原值）。
func process_zoom(distance: float) -> float:
	if _camera == null:
		return distance
	var zoom_delta: float = 0.0
	if Input.is_action_just_pressed("zoom_in"):
		zoom_delta = -CAMERA_ZOOM_DISTANCE_STEP
	elif Input.is_action_just_pressed("zoom_out"):
		zoom_delta = CAMERA_ZOOM_DISTANCE_STEP
	if zoom_delta == 0.0:
		return distance
	var new_distance := clampf(
		distance + zoom_delta, CAMERA_DISTANCE_MIN, CAMERA_DISTANCE_MAX)
	return new_distance


## 按焦点与距离摆位相机（沿 (1,1,1) 方向俯视焦点）并换算正交投影参数：
## size = 距离×tan(FOV/2)，near/far 紧贴可视地形 slab；同步太阳位置与阴影覆盖距离。
##
## Args:
##     focus: 相机焦点（世界空间）。
##     distance: 相机距离。
##     sun: 方向光（阴影覆盖距离/位置同步；null 跳过）。
##     sun_altitude: 最近一次计算的太阳高度角（阴影覆盖计算用）。
func apply(focus: Vector3, distance: float,
		sun: DirectionalLight3D, sun_altitude: float) -> void:
	if _camera == null:
		return
	var dir := Vector3(1, 1, 1).normalized()
	_camera.position = focus + dir * distance
	_camera.look_at(focus, Vector3.UP)

	# 正交投影 size = 距离 × tan(FOV/2)，保持原缩放手感；
	# near/far 紧贴可视地形 slab（含最高物体余量），阴影范围随之精确覆盖屏幕。
	var half_perp: float = distance * tan(deg_to_rad(CAMERA_FOV * 0.5))
	_camera.size = half_perp
	var elevation: float = asin(1.0 / sqrt(3.0))
	var ground_half: float = half_perp / sin(elevation)
	_camera.near = maxf(
		distance - ground_half - SHADOW_TALL_ALLOWANCE - SHADOW_SLAB_MARGIN, 1.0)
	_camera.far = distance + ground_half + SHADOW_SLAB_MARGIN

	if sun:
		sun.directional_shadow_max_distance = shadow_coverage(distance, sun_altitude)
		sun.position = focus + Vector3(0, 200, 0)


## 相机在 (1,1,1) 方向、FOV 5° 下可视地面的对角线半径。
func visible_radius(distance: float) -> float:
	var half_perp: float = distance * tan(deg_to_rad(CAMERA_FOV * 0.5))
	var elevation: float = asin(1.0 / sqrt(3.0))
	var ground_depth: float = half_perp / sin(elevation)
	return sqrt(half_perp * half_perp + ground_depth * ground_depth)


## 阴影覆盖半径 = 可视半径 × 余量（含边缘遮挡物投射余量）；低角度太阳时按比例放大。
##
## 注意 directional_shadow_max_distance 是"距相机半径"语义：过大的余量会白白稀释
## 8192 texel 阴影分辨率，因此正午仅保留 1.35 倍，低角度拉长阴影由 3 倍放大兜底。
func shadow_coverage(distance: float, sun_altitude: float) -> float:
	var coverage: float = visible_radius(distance) * SHADOW_COVERAGE_MARGIN
	if sun_altitude < SHADOW_LOW_ANGLE_CEIL:
		var t: float = clampf(
			(sun_altitude - SHADOW_CUTOFF) / (SHADOW_LOW_ANGLE_CEIL - SHADOW_CUTOFF),
			0.0, 1.0)
		coverage *= lerpf(SHADOW_LOW_ANGLE_EXPAND, 1.0, t)
	return coverage
