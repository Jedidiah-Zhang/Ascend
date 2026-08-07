"""世界加载进度页 — 创建/进入世界时展示后端启动与生成进度（Issue #8）。

流程: 创建请求成功后 → 重启后端(世界模式) → 本页显示
「启动世界进程 → 生成地形(stage 逐阶段) → 进入世界」。

world_initialized 事件由 main_world 消费（出生点/初始状态请求），
本页在切场景后下一帧转发该事件（Relay 持有强引用，跨场景存活），
保证 main_world 订阅就位时事件不丢失。

失败兜底: 后端启动失败 / 连接中断 / 超时 → 错误态（返回主菜单 / 重试）。

全部内容 _draw() 绘制，等宽字体，与主菜单风格一致。
"""

class_name WorldLoading
extends Control

const MAIN_MENU_SCENE: String = "res://scenes/main_menu.tscn"
const MAIN_WORLD_SCENE: String = "res://scenes/main.tscn"

## 世界生成/加载超时（秒）：超时未收到 world_initialized → 错误态。
const LOAD_TIMEOUT_SEC: float = 120.0

enum State { LAUNCHING, LOADING, ERROR }

# ── 视觉常量 ──────────────────────────────────────────────

const BG_COLOR: Color = Color(0.05, 0.05, 0.10, 0.92)
const PANEL_COLOR: Color = Color(0.08, 0.09, 0.14, 0.85)
const TITLE_COLOR: Color = Color(0.90, 0.93, 1.0)
const SUBTITLE_COLOR: Color = Color(0.55, 0.62, 0.75)
const STAGE_COLOR: Color = Color(0.85, 0.88, 0.95)
const BUTTON_COLOR: Color = Color(0.16, 0.20, 0.30)
const BUTTON_HOVER_COLOR: Color = Color(0.24, 0.32, 0.46)
const BUTTON_TEXT_COLOR: Color = Color(0.92, 0.92, 0.96)
const ERROR_COLOR: Color = Color(0.95, 0.50, 0.45)
const PROGRESS_FILL_COLOR: Color = Color(0.24, 0.48, 0.80, 0.9)
const PROGRESS_TRACK_COLOR: Color = Color(1, 1, 1, 0.10)

## 阶段切换后的快速补间窗口（秒）：该窗口内进度快速增长到新刻度
const PROGRESS_CATCHUP_SEC: float = 0.6
## 快速补间速率（每秒比例）：一格约 0.57s 到位，视觉"快速增长"
const PROGRESS_CATCHUP_PER_SEC: float = 0.25
## 阶段内缓慢爬升速率（每秒比例）：视觉活性，不匀速、不越格
const PROGRESS_DRIFT_PER_SEC: float = 0.01

const TITLE_FONT_SIZE: int = 40
const STAGE_FONT_SIZE: int = 20
const BUTTON_FONT_SIZE: int = 18
const HINT_FONT_SIZE: int = 13
const BUTTON_W: float = 200.0
const BUTTON_H: float = 44.0
const BUTTON_GAP: float = 12.0


# ── 转发 Relay（跨场景存活） ──────────────────────────────

## 切场景后一帧转发 world_initialized：SceneTree 的 process_frame
## ONE_SHOT 连接持有本对象；自持引用保证局部变量失效后（本帧末
## 场景切换）仍存活；_fire 触发后解除自持，随 ONE_SHOT 断开释放。
## 注：CONNECT_REFERENCE_COUNTED|ONE_SHOT 组合在当前引擎下不会
## 触发，故用自持引用替代。
class Relay:
	extends RefCounted

	var message: Dictionary = {}
	var _self: Relay = null

	func _init() -> void:
		_self = self

	func _fire() -> void:
		_self = null
		Connection.message_received.emit(message)


# ── 属性 ──────────────────────────────────────────────────

var _font: Font = null
var _state: State = State.LAUNCHING
var _stage_text: String = "正在启动世界进程..."
var _error_reason: String = ""
var _elapsed: float = 0.0
## 已见生成阶段的最大索引（-1 = 未收到阶段）；进度目标刻度按此推进
var _stage_index: int = -1
## 当前显示的进度比例（平滑补间后的值，见 _advance_display_progress）
var _display_ratio: float = 0.0
## 快速补间剩余时间（秒）；阶段切换时重置
var _catchup_left: float = 0.0
var _handed_off: bool = false

## 重试按钮矩形（_draw 时更新，_input 时命中）
var _retry_rect: Rect2 = Rect2()
var _menu_rect: Rect2 = Rect2()
var _retry_hover: bool = false
var _menu_hover: bool = false

## 重试时的后端启动器（测试可注入；默认走 Connection 进程切换）。
var backend_launcher: Callable = _launch_backend_default

## 就绪后切入主世界场景的路由器（测试可注入，避免测试中切换真实场景）。
var scene_router: Callable = _route_to_main_default


func _ready() -> void:
	anchor_left = 0.0
	anchor_top = 0.0
	anchor_right = 1.0
	anchor_bottom = 1.0
	_font = FontUtils.get_mono_font()
	if not Connection.message_received.is_connected(_on_message):
		Connection.message_received.connect(_on_message)
	if not Connection.connection_lost.is_connected(_on_connection_lost):
		Connection.connection_lost.connect(_on_connection_lost)
	if not Connection.backend_failed.is_connected(_on_backend_failed):
		Connection.backend_failed.connect(_on_backend_failed)
	if Connection.status == Connection.Status.CONNECTED:
		_enter_loading("正在加载世界...")


func _exit_tree() -> void:
	if Connection.message_received.is_connected(_on_message):
		Connection.message_received.disconnect(_on_message)
	if Connection.connection_lost.is_connected(_on_connection_lost):
		Connection.connection_lost.disconnect(_on_connection_lost)
	if Connection.backend_failed.is_connected(_on_backend_failed):
		Connection.backend_failed.disconnect(_on_backend_failed)


# ── 状态流转 ──────────────────────────────────────────────

func _enter_loading(text: String) -> void:
	_state = State.LOADING
	_stage_text = text
	queue_redraw()


func _enter_error(reason: String) -> void:
	_state = State.ERROR
	_error_reason = reason
	queue_redraw()


func _retry() -> void:
	_state = State.LAUNCHING
	_stage_text = "正在启动世界进程..."
	_elapsed = 0.0
	_stage_index = -1
	_display_ratio = 0.0
	_catchup_left = 0.0
	queue_redraw()
	backend_launcher.call(Connection.backend_args)


func _launch_backend_default(args: PackedStringArray) -> void:
	Connection.restart_backend(args)


# ── 消息 ──────────────────────────────────────────────────

func _on_message(message: Dictionary) -> void:
	if _handed_off or _state == State.ERROR:
		return
	var msg_type: String = message.get("type", "")
	var event_type: String = message.get("event_type", "")
	if msg_type != "event":
		return
	match event_type:
		"world_progress":
			if _state == State.LAUNCHING:
				_enter_loading("正在加载世界...")
			# 协议：payload.data.stage（与 main_world.gd 解析一致）
			var payload: Dictionary = message.get("payload", {})
			var stage: String = str(payload.get("data", {}).get("stage", ""))
			_stage_text = WorldStageLabels.label_for(stage)
			var idx: int = WorldStageLabels.index_of(stage)
			if idx > _stage_index:
				_stage_index = idx
				_catchup_left = PROGRESS_CATCHUP_SEC
			queue_redraw()
		"world_initialized":
			_handed_off = true
			_stage_text = "正在进入世界..."
			queue_redraw()
			_handoff(message)


## 转发 world_initialized 给即将就位的 main_world：
## 先切换场景（本帧末生效），下一帧 process_frame 时 main_world
## 已完成订阅，Relay 再广播该事件。
func _handoff(message: Dictionary) -> void:
	var relay := Relay.new()
	relay.message = message
	get_tree().process_frame.connect(relay._fire, CONNECT_ONE_SHOT)
	scene_router.call()


func _route_to_main_default() -> void:
	get_tree().change_scene_to_file(MAIN_WORLD_SCENE)


func _on_connection_lost() -> void:
	if not _handed_off:
		_enter_error("连接中断，世界加载失败")


func _on_backend_failed(reason: String) -> void:
	if not _handed_off:
		_enter_error("后端启动失败：%s" % reason)


# ── 每帧超时 ──────────────────────────────────────────────

func _process(delta: float) -> void:
	if _state != State.LAUNCHING and _state != State.LOADING:
		return
	_elapsed += delta
	if _elapsed > LOAD_TIMEOUT_SEC:
		_enter_error("加载超时（%.0f 秒内未完成），请重试" % LOAD_TIMEOUT_SEC)
		return
	_advance_display_progress(delta)


## 进度目标刻度：阶段刻度（每收到一个生成阶段推进一格）与超时兜底
## （阶段事件缺失时按已等待时间缓慢填充）取较大者，封顶 90%。
## 生成完成即交接切主世界，条无需走满。
func _progress_target() -> float:
	var stage_ratio: float = (
		float(_stage_index + 1) / WorldStageLabels.ORDER.size()
		if _stage_index >= 0 else 0.0)
	return clampf(maxf(stage_ratio, _elapsed / LOAD_TIMEOUT_SEC), 0.0, 0.9)


## 当前显示的进度比例（平滑补间值，非目标刻度）。
func _progress_ratio() -> float:
	return _display_ratio


## 显示进度推进：阶段切换后的快速窗口内以高速补间增长（视觉"快速
## 增长"），平时缓慢爬升（视觉活性）；单方向逼近目标刻度，不越格。
func _advance_display_progress(delta: float) -> void:
	var fast: bool = _catchup_left > 0.0
	_catchup_left = maxf(0.0, _catchup_left - delta)
	var rate: float = (
		PROGRESS_CATCHUP_PER_SEC if fast else PROGRESS_DRIFT_PER_SEC) * delta
	var next: float = clampf(_display_ratio + rate, 0.0, _progress_target())
	if not is_equal_approx(next, _display_ratio):
		_display_ratio = next
		queue_redraw()


# ── 绘制 ──────────────────────────────────────────────────

func _draw() -> void:
	if _font == null:
		return
	var view_size: Vector2 = size

	# 背景
	draw_rect(Rect2(Vector2.ZERO, size), BG_COLOR)

	# 标题
	var title: String = "世界加载"
	var title_w: float = _font.get_string_size(title, HORIZONTAL_ALIGNMENT_LEFT, -1, TITLE_FONT_SIZE).x
	draw_string(_font, Vector2((view_size.x - title_w) * 0.5, view_size.y * 0.32), title,
		HORIZONTAL_ALIGNMENT_LEFT, -1, TITLE_FONT_SIZE, TITLE_COLOR)

	var center_y: float = view_size.y * 0.44
	match _state:
		State.ERROR:
			_draw_error(center_y)
		_:
			_draw_stage(center_y)


func _draw_stage(center_y: float) -> void:
	# 阶段文案
	var w: float = _font.get_string_size(_stage_text, HORIZONTAL_ALIGNMENT_LEFT, -1, STAGE_FONT_SIZE).x
	draw_string(_font, Vector2((size.x - w) * 0.5, center_y), _stage_text,
		HORIZONTAL_ALIGNMENT_LEFT, -1, STAGE_FONT_SIZE, STAGE_COLOR)

	# 进度条（阶段刻度与超时兜底取大，见 _progress_ratio）
	var bar_w: float = 360.0
	var bar_h: float = 8.0
	var bar_pos: Vector2 = Vector2((size.x - bar_w) * 0.5, center_y + 28)
	var ratio: float = _progress_ratio()
	draw_rect(Rect2(bar_pos, Vector2(bar_w, bar_h)), PROGRESS_TRACK_COLOR)
	draw_rect(Rect2(bar_pos, Vector2(bar_w * ratio, bar_h)), PROGRESS_FILL_COLOR)

	# 世界 ID 提示
	var world_id: String = Connection.backend_world_id()
	var hint: String = "世界 ID: %s" % (world_id if not world_id.is_empty() else "-")
	var hint_w: float = _font.get_string_size(hint, HORIZONTAL_ALIGNMENT_LEFT, -1, HINT_FONT_SIZE).x
	draw_string(_font, Vector2((size.x - hint_w) * 0.5, center_y + 64), hint,
		HORIZONTAL_ALIGNMENT_LEFT, -1, HINT_FONT_SIZE, SUBTITLE_COLOR)


func _draw_error(center_y: float) -> void:
	var w: float = _font.get_string_size(_error_reason, HORIZONTAL_ALIGNMENT_LEFT, -1, 16).x
	draw_string(_font, Vector2((size.x - w) * 0.5, center_y), _error_reason,
		HORIZONTAL_ALIGNMENT_LEFT, -1, 16, ERROR_COLOR)

	# 按钮：返回主菜单 / 重试
	var total_w: float = BUTTON_W * 2 + BUTTON_GAP
	var start_x: float = (size.x - total_w) * 0.5
	var y: float = center_y + 36
	_retry_rect = Rect2(start_x, y, BUTTON_W, BUTTON_H)
	_menu_rect = Rect2(start_x + BUTTON_W + BUTTON_GAP, y, BUTTON_W, BUTTON_H)
	_draw_button(_retry_rect, "重试", _retry_hover)
	_draw_button(_menu_rect, "返回主菜单", _menu_hover)


func _draw_button(rect: Rect2, label: String, hovered: bool) -> void:
	var fill: Color = BUTTON_HOVER_COLOR if hovered else BUTTON_COLOR
	draw_rect(rect, fill)
	draw_rect(rect, Color(1, 1, 1, 0.12), false, 1.0)
	var label_w: float = _font.get_string_size(label, HORIZONTAL_ALIGNMENT_LEFT, -1, BUTTON_FONT_SIZE).x
	draw_string(_font, rect.position + Vector2((BUTTON_W - label_w) * 0.5, BUTTON_H * 0.5 + 6),
		label, HORIZONTAL_ALIGNMENT_LEFT, -1, BUTTON_FONT_SIZE, BUTTON_TEXT_COLOR)


# ── 输入 ──────────────────────────────────────────────────

func _input(event: InputEvent) -> void:
	if event is InputEventMouseMotion:
		var retry_h: bool = _retry_rect.has_point(event.position)
		var menu_h: bool = _menu_rect.has_point(event.position)
		if retry_h != _retry_hover or menu_h != _menu_hover:
			_retry_hover = retry_h
			_menu_hover = menu_h
			queue_redraw()
		return
	if event is InputEventMouseButton and event.pressed and event.button_index == MOUSE_BUTTON_LEFT:
		if _state != State.ERROR:
			return
		if _retry_rect.has_point(event.position):
			_retry()
			get_viewport().set_input_as_handled()
		elif _menu_rect.has_point(event.position):
			get_tree().change_scene_to_file(MAIN_MENU_SCENE)
			get_viewport().set_input_as_handled()
