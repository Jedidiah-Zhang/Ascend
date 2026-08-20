"""世界加载流程 — 地形就绪判定/进度条补满收尾的纯逻辑计时状态机。

从 main_world.gd 拆出（出生点地形就绪判定、进度条补满收尾的计时/幂等闸）：
本类只持有加载时序状态（就绪等待计时、补满收尾兜底计时、补满闸），
输出主世界本帧应执行的收尾动作；副作用（挂载/显示玩家/相机摆位/覆盖层操作）
全部留在 main_world。依赖方向：main_world（世界编排）→ 本类。
"""

class_name WorldLoadingFlow
extends RefCounted

## 就绪判定半径：出生 chunk 周围 radius×radius 圈全部加载视为就绪
const TERRAIN_READY_RADIUS: int = 1
## 地形就绪等待超时（秒）：超时强制显示，防后端异常时玩家永久卡住
const TERRAIN_READY_TIMEOUT: float = 8.0
## 补满收尾兜底（秒）：进度条补满动画异常（completed 信号未触发）时强制收尾
const COMPLETION_FALLBACK_SEC: float = 3.0

## 进度条补满是否已开始（幂等闸）
var _loading_completed: bool = false
## 地形就绪等待计时（墙钟秒）
var _terrain_ready_timer: float = 0.0
## 补满收尾兜底计时（>0 时倒计时，归零强制收尾）
var _completion_fallback_sec: float = 0.0


## 推进加载流程计时一帧。仅当"已出生且未可见"时由主世界调用。
##
## Returns:
##     {force_ready: bool, force_finish: bool}——force_ready = 就绪等待
##     超时（主世界应强制判定就绪）；force_finish = 补满收尾超时
##     （completed 信号未触发，主世界应直接显示世界）。
func tick(delta: float) -> Dictionary:
	var act := {"force_ready": false, "force_finish": false}
	# 就绪等待计时仅在补满收尾开始前累积（开始后不再触发重复强判）
	if not _loading_completed:
		_terrain_ready_timer += delta
		if _terrain_ready_timer >= TERRAIN_READY_TIMEOUT:
			act["force_ready"] = true
	# 补满收尾兜底：completed 信号未触发时持续倒计时，归零强制收尾
	if _completion_fallback_sec > 0.0:
		_completion_fallback_sec -= delta
		if _completion_fallback_sec <= 0.0:
			act["force_finish"] = true
	return act


## 开始补满收尾（幂等）：已开始过返回 false（主世界跳过重复触发）。
func begin_completion() -> bool:
	if _loading_completed:
		return false
	_loading_completed = true
	_completion_fallback_sec = COMPLETION_FALLBACK_SEC
	return true


## 世界可见收尾：停表（补满完成/超时兜底共用，幂等）。
func finish() -> void:
	_loading_completed = true
	_completion_fallback_sec = 0.0
	_terrain_ready_timer = 0.0


## 重置（世界重建/断线后旧加载状态失效）。
func reset() -> void:
	_loading_completed = false
	_terrain_ready_timer = 0.0
	_completion_fallback_sec = 0.0


## 地形加载进度（纯计算）：已构建 chunk 数 / 就绪圈总数（供进度条推进）。
static func terrain_progress(built: int, total: int) -> float:
	if total <= 0:
		return 0.0
	return clampf(float(built) / float(total), 0.0, 1.0)