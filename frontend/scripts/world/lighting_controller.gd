"""2D 光照调制 — CanvasModulate 全局昼夜色调 + 昼夜判据。

平地形无遮挡、无阴影，整类问题消失（视觉风格设计文档）。持有
CanvasModulate 引用，状态（游戏时间/日出日落/日照强度）由调用方喂入。
局部光源开关（火把/营火）由 is_night 纯判据驱动。
"""

class_name LightingController
extends RefCounted

const Config = preload("res://scripts/config.gd")


## 太阳高度角渐入上界：0→0.35 间平滑过渡，消除亮度跳变
const SUN_RAMP_CEIL: float = 0.35
## 白天/夜晚 CanvasModulate 色调（夜晚蓝紫暗色，白天中性）
const DAY_COLOR: Color = Color(1, 1, 1, 1)
const NIGHT_COLOR: Color = Color(0.35, 0.4, 0.7, 1)
## 雨天/阴天调暗上限（日照强度 0 → 0.85 亮度；1 → 1.0）
const WEATHER_DIM_MIN: float = 0.85


var _modulate: CanvasModulate
## 最近一次计算的太阳高度角（保留给未来相机/局部光使用）
var _last_sun_altitude: float = 0.5


func bind(modulate: CanvasModulate) -> void:
	"""绑定 CanvasModulate（main_world._ready 时调用；null 时操作静默跳过）。"""
	_modulate = modulate


## 最近一次计算的太阳高度角。
func last_sun_altitude() -> float:
	return _last_sun_altitude


## 是否为夜晚（游戏时间在日出前/日落后）：局部光源（火把/营火）的开关判据。
## 昼夜参数异常（daylight<=0）时保守视为白天（不点灯）。
func is_night(game_hour: float, game_minute: int,
		sunrise: float, sunset: float) -> bool:
	var hour_float: float = game_hour + game_minute / 60.0
	if sunset - sunrise <= 0.0:
		return false
	return hour_float < sunrise or hour_float >= sunset


## 按游戏时间与天气调制全局色调：太阳高度角驱动昼夜插值，
## 雨天（日照强度低）整体略暗；日出日落平滑 ramp 无跳变。
##
## Args:
##     game_hour/game_minute: 当前游戏时间。
##     sunrise/sunset: 日出日落时刻。
##     sunshine_intensity: 日照强度（0-1，来自后端）。
func update(game_hour: float, game_minute: int,
		sunrise: float, sunset: float, sunshine_intensity: float) -> void:
	if _modulate == null:
		return

	var hour_float: float = game_hour + game_minute / 60.0
	var daylight: float = sunset - sunrise
	if daylight <= 0.0:
		return

	var is_day: bool = hour_float >= sunrise and hour_float < sunset
	var day_progress: float = clampf((hour_float - sunrise) / daylight, 0.0, 1.0)
	var sun_altitude: float = sin(day_progress * PI) if is_day else 0.0
	_last_sun_altitude = sun_altitude

	# 日出日落平滑 ramp：高度角 0→SUN_RAMP_CEIL 间渐入渐出，无跳变
	var sun_ramp: float = smoothstep(0.0, SUN_RAMP_CEIL, sun_altitude)
	# 天气调制：日照强度低（雨/阴）时整体略暗
	var dim: float = WEATHER_DIM_MIN + (1.0 - WEATHER_DIM_MIN) * sunshine_intensity

	_modulate.color = NIGHT_COLOR.lerp(DAY_COLOR, sun_ramp * dim)