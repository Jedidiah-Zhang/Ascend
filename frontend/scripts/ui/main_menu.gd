"""主菜单 — 标题 + 开始游戏 / 设置 / 模组。

Issue #14/#7 的最小主界面：存档选择页的入口。
设置与模组为占位（未实现，点击提示）。
全部内容 _draw() 绘制，等宽字体，与调试终端风格一致。

流程（Issue #8 存档分流）: 开始游戏 → 查询存档列表
  - 有存档 → save_select.tscn（存档选择页）
  - 无存档 → world_setup.tscn（创建世界调参流程，直达）

进程模型：进入主菜单 = 菜单模式（世界进程已由暂停菜单切换回菜单，
此处仅兜底——Connection.restart_backend 幂等跳过已一致的模式）。
"""

extends Control

class_name MainMenu

# FontUtils 为全局类（font_utils.gd class_name），无需 preload 常量

const SAVE_SELECT_SCENE: String = "res://scenes/save_select.tscn"
const WORLD_SETUP_SCENE: String = "res://scenes/world_setup.tscn"

# ── 视觉常量 ──────────────────────────────────────────────

const BG_COLOR: Color = Color(0.05, 0.05, 0.10, 0.92)
const PANEL_COLOR: Color = Color(0.08, 0.09, 0.14, 0.85)
const TITLE_COLOR: Color = Color(0.90, 0.93, 1.0)
const SUBTITLE_COLOR: Color = Color(0.55, 0.62, 0.75)
const BUTTON_COLOR: Color = Color(0.16, 0.20, 0.30)
const BUTTON_HOVER_COLOR: Color = Color(0.24, 0.32, 0.46)
const BUTTON_DISABLED_COLOR: Color = Color(0.12, 0.13, 0.18)
const BUTTON_TEXT_COLOR: Color = Color(0.92, 0.92, 0.96)
const STATUS_CONNECTED_COLOR: Color = Color(0.55, 0.95, 0.55)
const STATUS_WAITING_COLOR: Color = Color(0.85, 0.85, 0.55)
const STATUS_ERROR_COLOR: Color = Color(0.95, 0.50, 0.45)

const TITLE_FONT_SIZE: int = 56
const SUBTITLE_FONT_SIZE: int = 16
const BUTTON_FONT_SIZE: int = 20
const STATUS_FONT_SIZE: int = 13
const BUTTON_W: float = 260.0
const BUTTON_H: float = 48.0
const BUTTON_GAP: float = 14.0


# ── 属性 ──────────────────────────────────────────────────

## 按钮定义: {label, enabled, note}
var _buttons: Array = [
	{"label": "开始游戏", "enabled": true, "note": ""},
	{"label": "设置", "enabled": false, "note": "未实现"},
	{"label": "模组", "enabled": false, "note": "未实现"},
]
## 各按钮矩形（_draw 时更新，_input 时命中）
var _button_rects: Array = []
var _hover_index: int = -1
var _font: Font = null
var _status_text: String = "正在启动后端..."
var _status_color: Color = STATUS_WAITING_COLOR
## 开始游戏已请求存档列表、等待分流响应中
var _checking_saves: bool = false


## 初始化主菜单：全屏锚点、等宽字体，并订阅 Connection 的连接状态
## 信号（连接成功 / 断开 / 后端失败）。
func _ready() -> void:
	anchor_left = 0.0
	anchor_top = 0.0
	anchor_right = 1.0
	anchor_bottom = 1.0
	_font = FontUtils.get_mono_font()
	_update_status()
	# 兜底：若后端仍处世界进程模式（异常路径未走暂停菜单），切回菜单模式
	if Connection.backend_args.size() > 0:
		Connection.restart_backend(PackedStringArray())
	if not Connection.connection_established.is_connected(_on_connected):
		Connection.connection_established.connect(_on_connected)
	if not Connection.connection_lost.is_connected(_on_disconnected):
		Connection.connection_lost.connect(_on_disconnected)
	if not Connection.backend_failed.is_connected(_on_backend_failed):
		Connection.backend_failed.connect(_on_backend_failed)
	if not Connection.message_received.is_connected(_on_message):
		Connection.message_received.connect(_on_message)


## 离开场景树时断开 Connection 信号订阅，避免悬挂引用。
func _exit_tree() -> void:
	if Connection.connection_established.is_connected(_on_connected):
		Connection.connection_established.disconnect(_on_connected)
	if Connection.connection_lost.is_connected(_on_disconnected):
		Connection.connection_lost.disconnect(_on_disconnected)
	if Connection.backend_failed.is_connected(_on_backend_failed):
		Connection.backend_failed.disconnect(_on_backend_failed)
	if Connection.message_received.is_connected(_on_message):
		Connection.message_received.disconnect(_on_message)


# ── 绘制 ──────────────────────────────────────────────────

## 绘制主菜单：背景、标题与副标题、按钮列表（禁用态置灰、悬停高亮、
## 未实现占位说明）、底部后端连接状态与版本号。
func _draw() -> void:
	if _font == null:
		return
	var view_size: Vector2 = size

	# 背景
	draw_rect(Rect2(Vector2.ZERO, size), BG_COLOR)

	# 标题
	var title: String = "ASCEND"
	var title_w: float = _font.get_string_size(title, HORIZONTAL_ALIGNMENT_LEFT, -1, TITLE_FONT_SIZE).x
	draw_string(_font, Vector2((view_size.x - title_w) * 0.5, view_size.y * 0.30), title,
		HORIZONTAL_ALIGNMENT_LEFT, -1, TITLE_FONT_SIZE, TITLE_COLOR)

	var sub: String = "基因改造驱动的群体演化"
	var sub_w: float = _font.get_string_size(sub, HORIZONTAL_ALIGNMENT_LEFT, -1, SUBTITLE_FONT_SIZE).x
	draw_string(_font, Vector2((view_size.x - sub_w) * 0.5, view_size.y * 0.30 + 34), sub,
		HORIZONTAL_ALIGNMENT_LEFT, -1, SUBTITLE_FONT_SIZE, SUBTITLE_COLOR)

	# 按钮
	var start_y: float = view_size.y * 0.46
	_button_rects.clear()
	for i in _buttons.size():
		var x: float = (view_size.x - BUTTON_W) * 0.5
		var y: float = start_y + i * (BUTTON_H + BUTTON_GAP)
		var rect := Rect2(x, y, BUTTON_W, BUTTON_H)
		_button_rects.append(rect)
		var b: Dictionary = _buttons[i]
		var fill: Color = BUTTON_DISABLED_COLOR if not b["enabled"] \
			else (BUTTON_HOVER_COLOR if i == _hover_index else BUTTON_COLOR)
		draw_rect(rect, fill)
		draw_rect(rect, Color(1, 1, 1, 0.12), false, 1.0)
		var label: String = b["label"]
		var label_w: float = _font.get_string_size(label, HORIZONTAL_ALIGNMENT_LEFT, -1, BUTTON_FONT_SIZE).x
		draw_string(_font, rect.position + Vector2((BUTTON_W - label_w) * 0.5, BUTTON_H * 0.5 + 7),
			label, HORIZONTAL_ALIGNMENT_LEFT, -1, BUTTON_FONT_SIZE,
			BUTTON_TEXT_COLOR if b["enabled"] else Color(0.55, 0.55, 0.60))
		if b["note"] != "":
			draw_string(_font, rect.position + Vector2(BUTTON_W + 12, BUTTON_H * 0.5 + 6),
				"( %s )" % b["note"], HORIZONTAL_ALIGNMENT_LEFT, -1, 13, SUBTITLE_COLOR)

	# 状态
	draw_string(_font, Vector2(16, view_size.y - 20), _status_text,
		HORIZONTAL_ALIGNMENT_LEFT, -1, STATUS_FONT_SIZE, _status_color)
	draw_string(_font, Vector2(16, view_size.y - 20 - 16), "v%s" % ProjectSettings.get_setting("application/config/version", "0.1"),
		HORIZONTAL_ALIGNMENT_LEFT, -1, STATUS_FONT_SIZE, SUBTITLE_COLOR)


# ── 输入 ──────────────────────────────────────────────────

## 处理输入：鼠标移动更新悬停按钮，左键点击命中时执行对应动作。
##
## Args:
##     event: 待处理的输入事件。
func _input(event: InputEvent) -> void:
	if event is InputEventMouseMotion:
		var index: int = _hit_button(event.position)
		if index != _hover_index:
			_hover_index = index
			queue_redraw()
		return
	if event is InputEventMouseButton and event.pressed and event.button_index == MOUSE_BUTTON_LEFT:
		var index: int = _hit_button(event.position)
		if index >= 0:
			_activate(index)
			# change_scene_to_file 即时切场景，节点出树后 viewport 为 null
			var vp := get_viewport()
			if vp:
				vp.set_input_as_handled()


## 命中检测：返回包含 pos 的按钮索引。
##
## Args:
##     pos: 鼠标位置。
##
## Returns:
##     命中的按钮索引；未命中返回 -1。
func _hit_button(pos: Vector2) -> int:
	for i in _button_rects.size():
		if _button_rects[i].has_point(pos):
			return i
	return -1


## 执行按钮动作：未启用按钮显示占位提示；「开始游戏」在后端连接失败
## 时重试连接，否则切换至存档选择页。
##
## Args:
##     index: 按钮索引（对应 _buttons 数组）。
func _activate(index: int) -> void:
	if index >= _buttons.size():
		return
	var b: Dictionary = _buttons[index]
	if not b["enabled"]:
		_status_text = "%s：%s" % [b["label"], b["note"]]
		_status_color = STATUS_WAITING_COLOR
		return
	match b["label"]:
		"开始游戏":
			# FAILED 终态可经 connect_to_server 重置（如后端启动超时）
			if Connection.status == Connection.Status.FAILED:
				Connection.connect_to_server()
				_status_text = "正在重试连接后端..."
				_status_color = STATUS_WAITING_COLOR
				return
			if Connection.status != Connection.Status.CONNECTED:
				_status_text = "正在连接后端..."
				_status_color = STATUS_WAITING_COLOR
				return
			if _checking_saves:
				return
			_checking_saves = true
			_status_text = "正在检查存档..."
			_status_color = STATUS_WAITING_COLOR
			Connection.send(SaveApi.list_request())


# ── 消息 ──────────────────────────────────────────────────

## save_list 响应：无存档 → 直达创建世界流程（Issue #8）；
## 有存档 → 存档选择页。
func _on_message(message: Dictionary) -> void:
	if not _checking_saves:
		return
	if message.get("type", "") != "response" \
			or message.get("request_type", "") != SaveApi.LIST:
		return
	_checking_saves = false
	var worlds: Array = SaveApi.parse_worlds(message.get("payload", {}))
	if worlds.is_empty():
		_status_text = "暂无存档 — 正在进入创建世界..."
		get_tree().change_scene_to_file(WORLD_SETUP_SCENE)
	else:
		_status_text = "正在进入存档选择..."
		get_tree().change_scene_to_file(SAVE_SELECT_SCENE)


# ── 连接状态 ──────────────────────────────────────────────

## 根据 Connection 状态刷新底部状态文本与颜色
## （已连接 / 连接中 / 断开重连 / 连接失败）。
## 事件驱动重绘：状态变化即 queue_redraw，无需每帧轮询。
func _update_status() -> void:
	match Connection.status:
		Connection.Status.CONNECTED:
			_status_text = "后端已连接"
			_status_color = STATUS_CONNECTED_COLOR
		Connection.Status.CONNECTING:
			_status_text = "正在连接后端..."
			_status_color = STATUS_WAITING_COLOR
		Connection.Status.DISCONNECTED:
			_status_text = "正在重连后端..."
			_status_color = STATUS_WAITING_COLOR
		Connection.Status.FAILED:
			_status_text = "后端连接失败（详见输出）"
			_status_color = STATUS_ERROR_COLOR
	queue_redraw()


## 后端连接成功回调：刷新状态文本。
func _on_connected(_host: String, _port: int) -> void:
	_update_status()


## 后端连接断开回调：刷新状态文本并复位存档检查（响应可能不再回来）。
func _on_disconnected() -> void:
	_checking_saves = false
	_update_status()


## 后端启动失败回调：显示失败原因并置错误色。
func _on_backend_failed(reason: String) -> void:
	_status_text = "后端启动失败：%s" % reason
	_status_color = STATUS_ERROR_COLOR
