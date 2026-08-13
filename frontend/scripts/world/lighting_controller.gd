"""光照/阴影调制 — 太阳高度角驱动的环境光、背景、直射光与阴影参数。

从 main_world.gd 拆出（原 _configure_environment / _update_lighting 的
调制逻辑 + 光照常量）：持有 DirectionalLight3D / WorldEnvironment 引用，
状态（游戏时间/日出日落/日照强度/太阳高度角缓存）由调用方喂入。

依赖 CameraRig.shadow_coverage（阴影覆盖距离与相机几何共享）。
"""

class_name LightingController
extends RefCounted

const Config = preload("res://scripts/config.gd")


## 太阳高度角渐入上界：0→0.35 间平滑过渡，消除亮度跳变
const SUN_RAMP_CEIL: float = 0.35
## 阴影透明度渐变带宽（围绕 SHADOW_CUTOFF）
const SHADOW_FADE_BAND: float = 0.05
## 太阳高度角低于该值时关闭阴影（与 CameraRig 同源，见 Config）
const SHADOW_CUTOFF: float = Config.SHADOW_CUTOFF
## 低角度区间上限：低于该值开始放大覆盖范围、压扁 pancake
const SHADOW_LOW_ANGLE_CEIL: float = Config.SHADOW_LOW_ANGLE_CEIL
## 低角度时 pancake 尺寸（压缩阴影相机深度视锥）
const SHADOW_LOW_ANGLE_PANCAKE: float = 80.0
const SHADOW_BASE_PANCAKE: float = 20.0
const SHADOW_BIAS_BASE: float = 0.07
const SHADOW_NORMAL_BIAS: float = 0.2
## 低角度时 shadow_bias 放大保护的分母下限（防除零/过小偏置）
const SHADOW_BIAS_MIN_ALT: float = 0.1
## 直射光强度倍率（后端日照 0~1 → 场景光强）
const SUN_ENERGY_SCALE: float = 1.2
## 天气调制中环境光的基量/天气占比（env_t = sun_ramp × (BASE + WEATHER×intensity)）
const ENV_BASE_WEIGHT: float = 0.4
const ENV_WEATHER_WEIGHT: float = 0.6

## 日间环境光/背景色（configure_environment 与 update 共用）
const DAY_AMBIENT: Color = Color(0.55, 0.55, 0.6, 1.0)
const NIGHT_AMBIENT: Color = Color(0.14, 0.15, 0.32, 1.0)
const DAY_BG: Color = Color(0.15, 0.15, 0.5, 1.0)
const NIGHT_BG: Color = Color(0.02, 0.02, 0.08, 1.0)


var _sun_light: DirectionalLight3D
var _world_env: WorldEnvironment
var _rig: CameraRig
## 最近一次计算的太阳高度角（阴影覆盖计算缓存）
var _last_sun_altitude: float = 0.5


func bind(sun_light: DirectionalLight3D, world_env: WorldEnvironment,
		rig: CameraRig) -> void:
	"""绑定节点与相机几何（main_world._ready 时调用；null 时操作静默跳过）。"""
	_sun_light = sun_light
	_world_env = world_env
	_rig = rig


## 最近一次计算的太阳高度角（相机/阴影覆盖计算读取）。
func last_sun_altitude() -> float:
	return _last_sun_altitude


## 一次性环境配置（纯色背景 + 环境光 + 线性色调映射）与太阳阴影初始参数。
##
## Args:
##     camera_distance: 相机距离（初始阴影覆盖距离计算用）。
func configure_environment(camera_distance: float) -> void:
	if _world_env == null:
		return

	var env := _world_env.environment
	if env == null:
		env = Environment.new()
		_world_env.environment = env

	env.background_mode = Environment.BG_COLOR
	env.background_color = DAY_BG
	env.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	env.ambient_light_color = DAY_AMBIENT
	env.ambient_light_energy = 1.0
	env.tonemap_mode = Environment.TONE_MAPPER_LINEAR

	# ── 阴影配置 ──
	if _sun_light:
		_sun_light.shadow_enabled = true
		_sun_light.directional_shadow_mode = DirectionalLight3D.SHADOW_ORTHOGONAL
		_sun_light.shadow_bias = SHADOW_BIAS_BASE
		_sun_light.shadow_normal_bias = SHADOW_NORMAL_BIAS
		_sun_light.shadow_blur = 0.4
		_sun_light.directional_shadow_pancake_size = SHADOW_BASE_PANCAKE
		_sun_light.directional_shadow_fade_start = 0.85
		if _rig:
			_sun_light.directional_shadow_max_distance = _rig.shadow_coverage(
				camera_distance, 0.5)


## 按游戏时间与天气调制光照：太阳高度角驱动阴影开关/透明度/覆盖距离/pancake/偏置，
## 平滑 ramp 渐变环境光与背景色，直射光能量 = 后端日照 × 高度角渐入。
##
## Args:
##     game_hour/game_minute: 当前游戏时间。
##     sunrise/sunset: 日出日落时刻。
##     sun_azimuth: 太阳方位角（0-360，从后端种子派生）。
##     sunshine_intensity: 日照强度（0-1，来自后端）。
##     camera_distance: 相机距离（阴影覆盖距离计算用）。
func update(game_hour: float, game_minute: int,
		sunrise: float, sunset: float, sun_azimuth: float,
		sunshine_intensity: float, camera_distance: float) -> void:
	if _sun_light == null or _world_env == null:
		return

	var hour_float: float = game_hour + game_minute / 60.0
	var daylight: float = sunset - sunrise
	if daylight <= 0.0:
		return

	var is_day: bool = hour_float >= sunrise and hour_float < sunset
	var day_progress: float = clampf((hour_float - sunrise) / daylight, 0.0, 1.0)
	var sun_altitude: float = sin(day_progress * PI) if is_day else 0.0
	_last_sun_altitude = sun_altitude

	# 日出日落平滑 ramp：高度角 0→SUN_RAMP_CEIL 间渐入渐出，所有亮度量共用，消除跳变
	var sun_ramp: float = smoothstep(0.0, SUN_RAMP_CEIL, sun_altitude)

	# 阴影：低角度时 opacity 渐变淡出，避免阴影瞬间出现/消失
	var shadow_t: float = clampf((sun_altitude - SHADOW_CUTOFF) / SHADOW_FADE_BAND, 0.0, 1.0)
	_sun_light.shadow_opacity = shadow_t
	if sun_altitude < SHADOW_CUTOFF - SHADOW_FADE_BAND:
		_sun_light.shadow_enabled = false
	else:
		_sun_light.shadow_enabled = true
		var low_angle_t: float = clampf(
			(sun_altitude - SHADOW_CUTOFF) / (SHADOW_LOW_ANGLE_CEIL - SHADOW_CUTOFF),
			0.0, 1.0)
		_sun_light.directional_shadow_pancake_size = lerpf(
			SHADOW_LOW_ANGLE_PANCAKE, SHADOW_BASE_PANCAKE, low_angle_t)
		_sun_light.shadow_bias = SHADOW_BIAS_BASE / maxf(sun_altitude, SHADOW_BIAS_MIN_ALT)
	if _rig:
		_sun_light.directional_shadow_max_distance = _rig.shadow_coverage(
			camera_distance, sun_altitude)

	# 太阳方向：全天连续曲线，夜间延续地平线角度（此时能量为 0，方向无关）
	_sun_light.rotation_degrees.x = lerpf(0.0, -90.0, sun_altitude)
	_sun_light.rotation_degrees.y = sun_azimuth + day_progress * 180.0

	var intensity: float = sunshine_intensity
	var warmth: float = 1.0 - sun_altitude
	_sun_light.light_color = Color(1.0, 1.0 - warmth * 0.3, 1.0 - warmth * 0.7, 1.0)
	# 直射光 = 后端日照（含降雨衰减）× 高度角平滑渐入
	_sun_light.light_energy = intensity * SUN_ENERGY_SCALE * sun_ramp

	var env: Environment = _world_env.environment
	if env:
		# 环境光/背景由高度角 ramp 驱动（时间平滑），再乘天气调制（雨天天光略暗）
		var env_t: float = sun_ramp * (ENV_BASE_WEIGHT + ENV_WEATHER_WEIGHT * intensity)
		env.ambient_light_color = NIGHT_AMBIENT.lerp(DAY_AMBIENT, env_t)
		env.ambient_light_energy = lerpf(0.5, 1.0, env_t)

		env.background_color = NIGHT_BG.lerp(DAY_BG, env_t)
