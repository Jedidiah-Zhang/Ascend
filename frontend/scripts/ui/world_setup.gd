"""创建世界流程容器（Issue #8）— 多步调参 + 创建 + 进入世界。

流程（步骤可插拔，顺序由 SetupFlow.build_steps 固定）:
  1. 逐步骤调参：每步一个页面（SetupStep 契约，见 setup_step.gd）
  2. 最后一步「创建世界」→ save_create（seed + gen_params 随档定案）
  3. 停菜单进程 → 以 --world-id 拉起世界进程（进程模型）
  4. 切 world_loading 进度页 → 后端启动/生成完成 → 主世界场景

进入方式（存档分流）:
  - 主菜单「开始游戏」无存档 → 直达本页
  - 存档选择页「新建游戏」→ 本页

全部内容 _draw() 绘制 + 自绘命中检测；种子输入复用 LineEdit
（仅种子编辑时显示），风格与主菜单/存档页一致。
"""

extends Control

class_name WorldSetup

const SAVE_SELECT_SCENE: String = "res://scenes/save_select.tscn"
const WORLD_LOADING_SCENE: String = "res://scenes/world_loading.tscn"

# ── 视觉常量 ──────────────────────────────────────────────

const BG_COLOR: Color = Color(0.05, 0.05, 0.10, 0.92)
const TITLE_COLOR: Color = Color(0.90, 0.93, 1.0)
const SUBTITLE_COLOR: Color = Color(0.55, 0.62, 0.75)
const BUTTON_COLOR: Color = Color(0.18, 0.22, 0.32)
const BUTTON_HOVER_COLOR: Color = Color(0.26, 0.34, 0.48)
const TEXT_COLOR: Color = Color(0.90, 0.90, 0.95)
const STATUS_OK_COLOR: Color = Color(0.55, 0.95, 0.55)
const STATUS_ERR_COLOR: Color = Color(0.95, 0.50, 0.45)
const STATUS_WAIT_COLOR: Color = Color(0.85, 0.85, 0.55)
const PANEL_COLOR: Color = Color(0.09, 0.11, 0.17, 0.97)

const TITLE_FONT_SIZE: int = 28
const STEP_FONT_SIZE: int = 16
const BUTTON_FONT_SIZE: int = 16
const STATUS_FONT_SIZE: int = 13

const MARGIN: float = 24.0
const HEADER_H: float = 64.0
const FOOTER_H: float = 56.0
const BUTTON_W: float = 120.0
const BUTTON_H: float = 32.0
const BUTTON_GAP: float = 10.0


# ── 属性 ──────────────────────────────────────────────────

var _steps: Array = []
var _current: int = 0
## 汇总的创建参数（跨步骤传递 + save_create 载荷）
var _params: Dictionary = {"seed": 0, "gen_params": {}}
var _font: Font = null

## 种子输入框（步骤注入）
var _seed_input: LineEdit = null

## 悬停导航键: "" / "cancel" / "prev" / "next"
var _hover_nav: String = ""
var _nav_rects: Dictionary = {}

## 状态文本（底部）
var _status_text: String = ""
var _status_color: Color = STATUS_WAIT_COLOR

## 创建流程中（save_create 已响应，等待世界进程）
var _entering_world: bool = false
var _busy: bool = false

## 后端进程启动器（测试可注入；默认走 Connection 进程切换）。
var backend_launcher: Callable = _launch_backend_default

## 创建成功后路由到加载进度页（测试可注入，避免测试中切换真实场景）。
var scene_router: Callable = _route_to_loading_default

## 消息发送器（测试可注入；默认走 Connection）。
var sender: Callable = _send_default


func _launch_backend_default(args: PackedStringArray) -> void:
	Connection.restart_backend(args)


func _route_to_loading_default() -> void:
	get_tree().change_scene_to_file(WORLD_LOADING_SCENE)


func _send_default(message: Dictionary) -> void:
	Connection.send(message)


# ── 生命周期 ──────────────────────────────────────────────

func _ready() -> void:
	anchor_left = 0.0
	anchor_top = 0.0
	anchor_right = 1.0
	anchor_bottom = 1.0
	_font = FontUtils.get_mono_font()

	_steps = SetupFlow.build_steps()

	_seed_input = LineEdit.new()
	_seed_input.visible = false
	_seed_input.max_length = 10
	_seed_input.text_submitted.connect(_on_seed_submitted)
	add_child(_seed_input)
	_current = 0
	_steps[0].setup(_params)
	_status_text = tr("ui.setup.step_progress").format({"current": 1, "total": _steps.size()})
	_status_color = SUBTITLE_COLOR

	Connection.message_received.connect(_on_message)
	Connection.connection_lost.connect(_on_connection_lost)
	Connection.backend_failed.connect(_on_backend_failed)
	Connection.connection_established.connect(_on_connected)
	if not Settings.locale_changed.is_connected(_on_locale_changed):
		Settings.locale_changed.connect(_on_locale_changed)


func _exit_tree() -> void:
	if Connection.message_received.is_connected(_on_message):
		Connection.message_received.disconnect(_on_message)
	if Connection.connection_lost.is_connected(_on_connection_lost):
		Connection.connection_lost.disconnect(_on_connection_lost)
	if Connection.backend_failed.is_connected(_on_backend_failed):
		Connection.backend_failed.disconnect(_on_backend_failed)
	if Connection.connection_established.is_connected(_on_connected):
		Connection.connection_established.disconnect(_on_connected)
	if Settings.locale_changed.is_connected(_on_locale_changed):
		Settings.locale_changed.disconnect(_on_locale_changed)


# ── 绘制 ──────────────────────────────────────────────────

func _draw() -> void:
	if _font == null:
		return
	var view_size: Vector2 = size

	draw_rect(Rect2(Vector2.ZERO, size), BG_COLOR)
	draw_string(_font, Vector2(MARGIN, HEADER_H * 0.5 + 6), tr("ui.setup.title"),
		HORIZONTAL_ALIGNMENT_LEFT, -1, TITLE_FONT_SIZE, TITLE_COLOR)

	# 返回按钮
	var back_rect := Rect2(view_size.x - MARGIN - 90, HEADER_H * 0.5 - 15, 90, 30)
	_draw_nav_button(back_rect, tr("ui.back"), _hover_nav == "back")
	_nav_rects["back"] = back_rect

	# 步骤标题
	var step: SetupStep = _steps[_current]
	var header: String = tr("ui.setup.step_header").format({
		"current": _current + 1, "total": _steps.size(), "title": step.title(),
	})
	draw_string(_font, Vector2(MARGIN, HEADER_H + 26), header,
		HORIZONTAL_ALIGNMENT_LEFT, -1, STEP_FONT_SIZE, SUBTITLE_COLOR)

	# 步骤内容区
	var content_rect := Rect2(MARGIN, HEADER_H + 40.0,
		view_size.x - MARGIN * 2, view_size.y - HEADER_H - 40.0 - FOOTER_H)
	step.draw_page(self, content_rect, _font)

	# 底部：状态 + 导航
	draw_string(_font, Vector2(MARGIN, view_size.y - 22), _status_text,
		HORIZONTAL_ALIGNMENT_LEFT, -1, STATUS_FONT_SIZE, _status_color)

	var is_last: bool = _current == _steps.size() - 1
	var next_label: String = tr("ui.setup.title") if is_last else tr("ui.setup.next")
	var prev_label: String = tr("ui.setup.prev") if not is_last else ""
	var next_rect := Rect2(
		view_size.x - MARGIN - BUTTON_W, view_size.y - FOOTER_H + 12,
		BUTTON_W, BUTTON_H)
	_draw_nav_button(next_rect, next_label, _hover_nav == "next")
	_nav_rects["next"] = next_rect
	if prev_label != "":
		var prev_rect := Rect2(
			next_rect.position.x - BUTTON_GAP - BUTTON_W,
			next_rect.position.y, BUTTON_W, BUTTON_H)
		_draw_nav_button(prev_rect, prev_label, _hover_nav == "prev")
		_nav_rects["prev"] = prev_rect


func _draw_nav_button(rect: Rect2, label: String, hovered: bool) -> void:
	draw_rect(rect, BUTTON_HOVER_COLOR if hovered else BUTTON_COLOR)
	draw_rect(rect, Color(1, 1, 1, 0.12), false, 1.0)
	var w: float = _font.get_string_size(label, HORIZONTAL_ALIGNMENT_LEFT, -1,
		BUTTON_FONT_SIZE).x
	draw_string(_font, rect.position + Vector2((rect.size.x - w) * 0.5, rect.size.y * 0.5 + 6),
		label, HORIZONTAL_ALIGNMENT_LEFT, -1, BUTTON_FONT_SIZE, TEXT_COLOR)


# ── 输入 ──────────────────────────────────────────────────

func _input(event: InputEvent) -> void:
	if _busy:
		# 创建流程中仅允许查看
		return
	if _seed_input.visible:
		if event is InputEventKey and event.pressed \
				and not event.echo and event.keycode == KEY_ESCAPE:
			_current_step().on_escape()
			queue_redraw()
			get_viewport().set_input_as_handled()
		return
	if event is InputEventMouseMotion:
		_update_nav_hover(event.position)
		if _current_step().handle_input(event, _content_rect()):
			queue_redraw()
		return
	if event is InputEventMouseButton:
		if not event.pressed and event.button_index == MOUSE_BUTTON_LEFT:
			if _current_step().handle_release(event):
				queue_redraw()
			return
		if event.pressed and event.button_index == MOUSE_BUTTON_LEFT:
			_handle_click(event, event.position)
			# _handle_click 可能即时切场景，节点出树后 viewport 为 null
			var vp := get_viewport()
			if vp:
				vp.set_input_as_handled()
	if event is InputEventKey and event.pressed \
			and not event.echo and event.keycode == KEY_ESCAPE:
		if _current_step().on_escape():
			queue_redraw()
			get_viewport().set_input_as_handled()


func _content_rect() -> Rect2:
	return Rect2(MARGIN, HEADER_H + 40.0,
		size.x - MARGIN * 2, size.y - HEADER_H - 40.0 - FOOTER_H)


func _update_nav_hover(pos: Vector2) -> void:
	var hit: String = ""
	for key in _nav_rects:
		if _nav_rects[key].has_point(pos):
			hit = key
			break
	if hit != _hover_nav:
		_hover_nav = hit
		queue_redraw()


func _handle_click(event: InputEvent, pos: Vector2) -> void:
	if _nav_rects.get("back", Rect2()).has_point(pos):
		_go_back()
		return
	if _nav_rects.get("next", Rect2()).has_point(pos):
		_next_step()
		return
	if _nav_rects.get("prev", Rect2()).has_point(pos):
		_prev_step()
		return
	if _current_step().handle_input(event, _content_rect()):
		queue_redraw()


# ── 步骤流转 ──────────────────────────────────────────────

func _current_step() -> SetupStep:
	return _steps[_current]


func _next_step() -> void:
	var step: SetupStep = _current_step()
	var err: String = step.validate()
	if err != "":
		_set_status(err, STATUS_ERR_COLOR)
		return
	# 合并产出 → 创建载荷（最后一步）
	for key in step.get_params():
		_params[key] = step.get_params()[key]
	if _current >= _steps.size() - 1:
		_start_create()
		return
	_current += 1
	_current_step().setup(_params)
	_status_text = tr("ui.setup.step_progress").format({
		"current": _current + 1, "total": _steps.size(),
	})
	_status_color = SUBTITLE_COLOR
	queue_redraw()


func _prev_step() -> void:
	if _current <= 0:
		return
	_current -= 1
	_current_step().setup(_params)
	_status_text = tr("ui.setup.step_progress").format({
		"current": _current + 1, "total": _steps.size(),
	})
	_status_color = SUBTITLE_COLOR
	queue_redraw()


# ── 创建与进入世界 ────────────────────────────────────────

func _start_create() -> void:
	_busy = true
	_set_status(tr("ui.setup.creating_world"), STATUS_WAIT_COLOR)
	var gen_params: Dictionary = _params.get("gen_params", {})
	sender.call(SaveApi.create_request(
		_default_save_name(), int(_params.get("seed", 0)), gen_params))


static func _default_save_name() -> String:
	"""新建存档的默认名称（当前日期时间，与存档页一致）。"""
	return SaveInfoFormatter.datetime_string(float(Time.get_unix_time_from_system()))


func _on_message(message: Dictionary) -> void:
	var msg_type: String = message.get("type", "")
	var request_type: String = message.get("request_type", "")
	if msg_type == "response":
		match request_type:
			SaveApi.CREATE:
				_busy = false
				var world_id: String = str(message.get("payload", {}).get("world_id", ""))
				if world_id.is_empty():
					_set_status(tr("ui.saves.create_failed"), STATUS_ERR_COLOR)
					return
				_entering_world = true
				_set_status(tr("ui.common.entering_world"), STATUS_WAIT_COLOR)
				backend_launcher.call(PackedStringArray(["--world-id", world_id]))
				# 不等连接就绪：切进度页，由其等待后端启动与生成
				scene_router.call()
			SaveApi.MAP_PREVIEW:
				_current_step().on_preview_response(message.get("payload", {}))
				queue_redraw()
	elif msg_type == "error":
		if request_type == SaveApi.MAP_PREVIEW:
			_current_step().on_preview_failed()
			queue_redraw()
			return
		_busy = false
		_set_status(tr("ui.common.request_failed").format({
			"reason": SaveApi.parse_error(message),
		}), STATUS_ERR_COLOR)


## 创建成功后已切 world_loading 进度页，本页不再处理连接就绪；
## 仅保留 _entering_world 兜底标记（连接失败走 _on_connection_lost）。
func _on_connected(_host: String, _port: int) -> void:
	pass


func _on_connection_lost() -> void:
	if _busy or _entering_world:
		_busy = false
		_entering_world = false
		_set_status(tr("ui.common.connection_lost_retry"), STATUS_ERR_COLOR)
		queue_redraw()


func _on_backend_failed(reason: String) -> void:
	_busy = false
	_entering_world = false
	_set_status(tr("ui.common.backend_unavailable").format({"reason": reason}), STATUS_ERR_COLOR)
	queue_redraw()


func _set_status(text: String, color: Color) -> void:
	_status_text = text
	_status_color = color
	queue_redraw()


## 语言切换回调（设置界面改动后重绘文案）。
func _on_locale_changed(_locale: String) -> void:
	queue_redraw()


# ── 种子输入 ──────────────────────────────────────────────

func _on_seed_submitted(text: String) -> void:
	_current_step().on_seed_submitted(text)
	queue_redraw()


# ── 返回 ──────────────────────────────────────────────────

func _go_back() -> void:
	get_tree().change_scene_to_file(SAVE_SELECT_SCENE)
