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
var _place_us: int = 0
var _erase_us: int = 0
var _queue_us: int = 0
var _conn_us: int = 0

## TPS 计算所需的上一帧状态
var _prev_game_time: int = -1
var _prev_real_msec: int = 0

var _world: Node = null


func _init() -> void:
	label = "性能"


func setup(world: Node) -> void:
	_world = world


func process_section(_delta: float) -> void:
	update_msp_t()
	if _world and _world.has_method("get_debug_timing"):
		var timing: Dictionary = _world.get_debug_timing()
		_stream_us = timing.get("stream", 0)
		_place_us = timing.get("place", 0)
		_erase_us = timing.get("erase", 0)
		_queue_us = timing.get("queue", 0)
		_conn_us = timing.get("conn", 0)


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


func set_timing(stream_us: int, place_us: int, erase_us: int, queue_us: int, conn_us: int) -> void:
	_stream_us = stream_us
	_place_us = place_us
	_erase_us = erase_us
	_queue_us = queue_us
	_conn_us = conn_us


func update_msp_t() -> void:
	var raw_ms := Performance.get_monitor(Performance.TIME_PROCESS) * 1000.0
	_mspt_ema = _MS_ALPHA * raw_ms + (1.0 - _MS_ALPHA) * _mspt_ema


func get_lines() -> PackedStringArray:
	var fps := Engine.get_frames_per_second()
	return PackedStringArray([
		"FPS: %d  TPS: %.1f" % [fps, tps],
		"MSPT: %.2f ms  网络: %dμs" % [_mspt_ema, _conn_us],
		"流式: %dμs  放置: %dμs  擦除: %dμs" % [_stream_us, _place_us, _erase_us],
	])
