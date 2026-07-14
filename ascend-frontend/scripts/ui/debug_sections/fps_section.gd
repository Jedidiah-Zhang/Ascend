"""性能分区 — FPS / TPS / MSPT。

FPS 和逻辑耗时从引擎获取；TPS 由 main_world 根据 minute_change 间隔推算。
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


func _init() -> void:
	label = "性能"


func set_timing(stream_us: int, place_us: int, erase_us: int, queue_us: int, conn_us: int) -> void:
	_stream_us = stream_us
	_place_us = place_us
	_erase_us = erase_us
	_queue_us = queue_us
	_conn_us = conn_us


func get_lines() -> PackedStringArray:
	var fps := Engine.get_frames_per_second()
	return PackedStringArray([
		"FPS: %d  TPS: %.1f" % [fps, tps],
		"MSPT: %.2f ms  网络: %dμs" % [_mspt_ema, _conn_us],
		"流式: %dμs  放置: %dμs  擦除: %dμs" % [_stream_us, _place_us, _erase_us],
	])


func update_msp_t() -> void:
	var raw_ms := Performance.get_monitor(Performance.TIME_PROCESS) * 1000.0
	_mspt_ema = _MS_ALPHA * raw_ms + (1.0 - _MS_ALPHA) * _mspt_ema
