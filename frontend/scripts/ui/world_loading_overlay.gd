"""主世界加载覆盖层 — 全屏遮罩 + 进度条，隐藏地形加载过程。

进入存档后 main_world 在 LoadingLayer 挂载本覆盖层：不透明背景
完全盖住 3D 世界，玩家看不到地形 chunk 流式加载/网格构建的过程。
进度条与新建世界进度页（world_loading.gd）同款（共享 ProgressLerp）：
  世界生成阶段 → 按 WorldStageLabels.ORDER 推进刻度（封顶 90%）；
  出生点地形加载 → 按已构建 chunk 比例从 90% 补满到 100%。
地形就绪后先快速补满到 100%，满格停留片刻再发出 completed 信号，
main_world 收到后才隐藏本层并显示玩家——加载画面始终以满进度条收尾。
"""

class_name WorldLoadingOverlay
extends Control

## 进度条补满到 100% 后发出（供 main_world 隐藏覆盖层、显示世界与玩家）。
signal completed

## 背景色：alpha=1.0 全不透明——必须遮住下方正在构建的地形
const BG_COLOR: Color = Color(0.05, 0.05, 0.10, 1.0)
## 进度条轨道/填充色（与 world_loading 同色系）
const PROGRESS_TRACK_COLOR: Color = Color(1, 1, 1, 0.10)
const PROGRESS_FILL_COLOR: Color = Color(0.24, 0.48, 0.80, 0.9)
## 阶段文案颜色
const TEXT_COLOR: Color = Color(0.85, 0.88, 0.95)
const FONT_SIZE: int = 18

## 进度条尺寸
const BAR_W: float = 360.0
const BAR_H: float = 8.0
## 文案基线相对视口高度的位置（比例）
const TEXT_CENTER_Y_RATIO: float = 0.44
## 进度条与文案间距
const BAR_OFFSET: float = 28.0

## 阶段进度的封顶比例（剩余 10% 留给出生点地形加载）
const STAGE_PROGRESS_CAP: float = 0.9
## 补满到 100% 后的满格停留时间（秒）：让"加载完成"有可感知的收尾
const COMPLETION_HOLD_SEC: float = 0.45

## 等宽字体（懒加载）
var _font: Font = null
## 当前阶段文案（未收到阶段广播前显示生成兜底文案）
var _text: String = ""
## 已见生成阶段的最大索引（-1 = 未收到阶段）
var _stage_index: int = -1
## 平滑补间推进器（与 world_loading 同款逻辑，见 progress_lerp.gd）
var _lerp: ProgressLerp = ProgressLerp.new()
## completed 已发出（防重复）
var _completion_emitted: bool = false
## 满格停留剩余时间（<0 = 尚未到达 100%，到达后倒计时再发 completed）
var _completion_hold_left: float = -1.0


## 更新阶段文案并重绘。
func set_text(text: String) -> void:
	_text = text
	queue_redraw()


## 当前阶段文案（测试用）；未设置时返回生成兜底文案。
func get_text() -> String:
	if _text.is_empty():
		return tr("ui.loading.generating_world")
	return _text


## 世界生成阶段推进：按 WorldStageLabels.ORDER 推进进度刻度（封顶 90%），
## 阶段切换触发快速补间窗口；未知/重复阶段忽略。
func set_stage(stage: String) -> void:
	var idx: int = WorldStageLabels.index_of(stage)
	if idx < 0 or idx <= _stage_index:
		return
	_stage_index = idx
	_lerp.start_catchup()
	_lerp.target_ratio = maxf(_lerp.target_ratio, _stage_target(idx))
	queue_redraw()


## 出生点地形加载进度（0~1）：目标从 90% 基准补满到 100%（单调不减）。
## 每次更新触发快速补间——chunk 陆续到达时进度条实时跟追目标。
func set_terrain_progress(ratio: float) -> void:
	var next_target: float = (
		STAGE_PROGRESS_CAP + (1.0 - STAGE_PROGRESS_CAP) * clampf(ratio, 0.0, 1.0))
	if next_target > _lerp.target_ratio:
		_lerp.target_ratio = next_target
		_lerp.start_catchup()
	queue_redraw()


## 进度补满：目标置 100% 并触发快速补间；满格停留后发出 completed 信号
## （main_world 据此隐藏覆盖层——进度条始终以满格收尾，不会半截消失）。
func complete() -> void:
	_lerp.target_ratio = 1.0
	_lerp.start_catchup()
	queue_redraw()


## 世界重建/读档后重置进度状态（阶段刻度/目标/补满标记）。
func reset() -> void:
	_stage_index = -1
	_lerp.reset()
	_completion_emitted = false
	_completion_hold_left = -1.0
	queue_redraw()


## 阶段索引 → 目标刻度（封顶 STAGE_PROGRESS_CAP）。
func _stage_target(idx: int) -> float:
	return clampf(float(idx + 1) / float(WorldStageLabels.ORDER.size()), 0.0, STAGE_PROGRESS_CAP)


func _process(delta: float) -> void:
	if not visible:
		return
	_lerp.advance(delta)
	queue_redraw()
	# 到达 100% 后满格停留片刻再发 completed：收尾可感知（满格 → 世界出现）
	if not _completion_emitted and _lerp.display_ratio >= 1.0:
		if _completion_hold_left < 0.0:
			_completion_hold_left = COMPLETION_HOLD_SEC
		_completion_hold_left -= delta
		if _completion_hold_left <= 0.0:
			_completion_emitted = true
			completed.emit()


## 当前显示的进度比例（测试用）。
func get_progress_ratio() -> float:
	return _lerp.display_ratio


func _draw() -> void:
	if _font == null:
		_font = FontUtils.get_mono_font()
	var vsize: Vector2 = size

	# 不透明背景：遮住下方正在构建的地形（本需求核心）
	draw_rect(Rect2(Vector2.ZERO, vsize), BG_COLOR)

	# 进度条（与新建世界进度页同款）：轨道 + 填充
	var bar_pos := Vector2((vsize.x - BAR_W) * 0.5, vsize.y * TEXT_CENTER_Y_RATIO + BAR_OFFSET)
	draw_rect(Rect2(bar_pos, Vector2(BAR_W, BAR_H)), PROGRESS_TRACK_COLOR)
	draw_rect(Rect2(bar_pos, Vector2(BAR_W * _lerp.display_ratio, BAR_H)), PROGRESS_FILL_COLOR)

	# 阶段文案（进度条上方；未设置时显示生成兜底文案）
	var text: String = get_text()
	var text_w: float = _font.get_string_size(text, HORIZONTAL_ALIGNMENT_LEFT, -1, FONT_SIZE).x
	draw_string(
		_font,
		Vector2((vsize.x - text_w) * 0.5, vsize.y * TEXT_CENTER_Y_RATIO),
		text,
		HORIZONTAL_ALIGNMENT_LEFT,
		-1,
		FONT_SIZE,
		TEXT_COLOR)
