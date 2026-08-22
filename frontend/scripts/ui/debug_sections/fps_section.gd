"""性能分区 — FPS / TPS / MSPT / 各环节耗时。

FPS 和 MSPT 从引擎 Performance 单例获取；TPS 由 minute_change 事件间隔推算；
各环节耗时从世界脚本 get_debug_timing() 拉取。
"""

class_name FPSSection
extends "res://scripts/ui/debug_section.gd"


## 实测 TPS（tick per second），由 minute_change 事件间隔推算
var tps: float = 24.0

## MSPT 指数移动平均，平滑帧间抖动（alpha=0.3，~2s 收敛）
var _mspt_ema: float = 0.0
const _MS_ALPHA: float = 0.3

## 上帧操作耗时（微秒），用于定位瓶颈
var _stream_us: int = 0
var _conn_us: int = 0

## TPS 计算所需的上一帧状态
var _prev_game_time: int = -1
var _prev_real_msec: int = 0

var _world: Node = null


## 构造函数：设置分区标签翻译键。
func _init() -> void:
	label_key = "debug.section.performance"


## 缓存世界脚本引用，供 process_section 拉取各环节耗时。
##
## Args:
##     world: 世界脚本节点（MainWorld 或 MainWorld3D）。
func setup(world: Node) -> void:
	_world = world


## 每帧更新 MSPT（EMA 平滑），并从世界脚本 get_debug_timing 拉取
## 网络流式/连接耗时（微秒），用于定位性能瓶颈。
##
## Args:
##     _delta: 帧间隔（秒），本分区不使用。
func process_section(_delta: float) -> void:
	update_msp_t()
	if _world and _world.has_method("get_debug_timing"):
		var timing: Dictionary = _world.get_debug_timing()
		_stream_us = timing.get("stream", 0)
		_conn_us = timing.get("conn", 0)


## 响应 minute_change 事件：用游戏时间增量与两次事件间的真实流逝时间
## 推算实测 TPS；首帧（无上一状态）或游戏时间回退时仅记录不计算。
##
## Args:
##     event_type: 事件类型，仅处理 "minute_change"。
##     payload: 事件载荷，读取 data.game_time 作为游戏时间戳。
func on_world_event(event_type: String, payload: Dictionary) -> void:
	if event_type != "minute_change":
		return
	var data: Dictionary = payload.get("data", {})
	var gt: int = int(data.get("game_time", 0))
	var now_msec: int = Time.get_ticks_msec()
	if _prev_game_time >= 0 and gt > _prev_game_time:
		var tick_delta: int = gt - _prev_game_time
		var real_delta: float = (now_msec - _prev_real_msec) / 1000.0
		if real_delta > 0.0:
			tps = tick_delta / real_delta
	_prev_game_time = gt
	_prev_real_msec = now_msec


## 读取引擎 TIME_PROCESS 监视器（帧耗时，秒）换算为毫秒，
## 以指数移动平均（alpha=0.3，数十帧内收敛）平滑帧间抖动。
func update_msp_t() -> void:
	var raw_ms := Performance.get_monitor(Performance.TIME_PROCESS) * 1000.0
	_mspt_ema = _MS_ALPHA * raw_ms + (1.0 - _MS_ALPHA) * _mspt_ema


## 生成性能分区文本行：FPS/TPS、MSPT/网络连接耗时与流式耗时。
##
## Returns:
##     三行 PackedStringArray（FPS/TPS 行、MSPT/网络行、流式行）。
func get_lines() -> PackedStringArray:
	var fps: int = Engine.get_frames_per_second()
	return PackedStringArray([
		TranslationServer.tr("debug.fps_line").format({
			"fps": fps, "tps": "%.1f" % tps}),
		TranslationServer.tr("debug.mspt_line").format({
			"mspt": "%.2f" % _mspt_ema, "conn": _conn_us}),
		TranslationServer.tr("debug.stream_line").format({"value": _stream_us}),
	])
