"""进度条平滑推进的共享纯逻辑 — 目标刻度 → 单方向补间的显示比例。

world_loading（新建世界进度页）与 world_loading_overlay（主世界加载
覆盖层）共用：目标更新时触发快速补间窗口（高速逼近新刻度），平时缓慢
爬升（视觉活性）；单方向推进、不越格、目标邻近时精确落点（浮点累计
误差不再让补间停在 0.9999999）。
"""

class_name ProgressLerp
extends RefCounted

## 快速补间窗口（秒）：窗口内进度快速增长到新刻度
const CATCHUP_SEC: float = 0.6
## 快速补间速率（每秒比例）：视觉"快速增长"
const CATCHUP_PER_SEC: float = 0.25
## 缓慢爬升速率（每秒比例）：视觉活性，不匀速、不越格
const DRIFT_PER_SEC: float = 0.01

## 当前显示的进度比例（0~1，平滑补间值）
var display_ratio: float = 0.0
## 进度目标刻度（单调不减语义由调用方保证）
var target_ratio: float = 0.0
## 快速补间剩余时间（秒）；目标更新时重置
var catchup_left: float = 0.0


## 推进一帧并返回新的显示比例（调用方据此重绘）。
func advance(delta: float) -> float:
	var fast: bool = catchup_left > 0.0
	catchup_left = maxf(0.0, catchup_left - delta)
	var rate: float = (CATCHUP_PER_SEC if fast else DRIFT_PER_SEC) * delta
	var next: float = clampf(display_ratio + rate, 0.0, target_ratio)
	# 目标邻近时精确落点：浮点累计误差会让补间停在 0.9999999 而永不
	# 触发完成判定，这里直接吸附到目标刻度
	if next >= target_ratio - 1e-9:
		next = target_ratio
	display_ratio = next
	return display_ratio


## 触发快速补间（目标更新时调用：阶段切换/进度推进）。
func start_catchup() -> void:
	catchup_left = CATCHUP_SEC


## 归零（重试/重建世界时）。
func reset() -> void:
	display_ratio = 0.0
	target_ratio = 0.0
	catchup_left = 0.0
