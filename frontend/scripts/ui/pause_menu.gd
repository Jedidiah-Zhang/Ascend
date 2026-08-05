"""ESC 暂停菜单 — 继续游戏 / 手动存档 / 设置 / 返回主菜单 / 退出游戏。

打开时暂停游戏（get_tree().paused），关闭时恢复；返回主菜单 / 退出
游戏前先恢复（否则新场景的 _process 会被冻结）。

全部内容 _draw() 绘制 + 自绘命中检测，等宽字体，风格与主菜单 /
存档选择页一致。

设置尚未实现（占位提示，与主菜单一致）；手动存档信号由 main_world
转发（当前世界 ID 由后端 world_initialized 事件提供，结果经
show_status() 回填）。
"""

extends Control

class_name PauseMenu

signal save_requested

const MAIN_MENU_SCENE: String = "res://scenes/main_menu.tscn"

# ── 视觉常量 ──────────────────────────────────────────────

const OVERLAY_COLOR: Color = Color(0, 0, 0, 0.5)
const PANEL_COLOR: Color = Color(0.09, 0.11, 0.17, 0.97)
const TITLE_COLOR: Color = Color(0.90, 0.93, 1.0)
const SUBTITLE_COLOR: Color = Color(0.55, 0.62, 0.75)
const BUTTON_COLOR: Color = Color(0.16, 0.20, 0.30)
const BUTTON_HOVER_COLOR: Color = Color(0.24, 0.32, 0.46)
const BUTTON_DISABLED_COLOR: Color = Color(0.12, 0.13, 0.18)
const BUTTON_TEXT_COLOR: Color = Color(0.92, 0.92, 0.96)
const BUTTON_DANGER_COLOR: Color = Color(0.30, 0.16, 0.16)
const BUTTON_DANGER_HOVER_COLOR: Color = Color(0.46, 0.22, 0.22)
const STATUS_OK_COLOR: Color = Color(0.55, 0.95, 0.55)
const STATUS_ERR_COLOR: Color = Color(0.95, 0.50, 0.45)
const STATUS_WAIT_COLOR: Color = Color(0.85, 0.85, 0.55)

const PANEL_W: float = 340.0
const PANEL_TITLE_H: float = 64.0
const PANEL_STATUS_H: float = 36.0
const PANEL_PADDING: float = 40.0
const BUTTON_W: float = 260.0
const BUTTON_H: float = 42.0
const BUTTON_GAP: float = 10.0
const TITLE_FONT_SIZE: int = 24
const BUTTON_FONT_SIZE: int = 17
const NOTE_FONT_SIZE: int = 13

## 按钮定义: {key, label, danger}
const BUTTONS: Array = [
	{"key": "resume", "label": "继续游戏", "danger": false},
	{"key": "save", "label": "手动存档", "danger": false},
	{"key": "settings", "label": "设置", "danger": false},
	{"key": "menu", "label": "返回主菜单", "danger": false},
	{"key": "quit", "label": "退出游戏", "danger": true},
]

## 未实现功能的占位说明（与主菜单一致）
const SETTINGS_NOTE: String = "未实现"


# ── 属性 ──────────────────────────────────────────────────

var _font: Font = null
## 悬停按钮 key
var _hover_key: String = ""
## 按钮矩形: key → Rect2（_draw 时更新）
var _button_rects: Dictionary = {}
## 状态文本（面板底部）
var _status_text: String = ""
var _status_color: Color = STATUS_WAIT_COLOR
## 手动存档请求已发出等待响应中
var _saving: bool = false


# ── 生命周期 ──────────────────────────────────────────────

func _ready() -> void:
	anchor_left = 0.0
	anchor_top = 0.0
	anchor_right = 1.0
	anchor_bottom = 1.0
	mouse_filter = Control.MOUSE_FILTER_STOP
	# 必须 ALWAYS：WHEN_PAUSED 会同时屏蔽未暂停时的输入（输入回调
	# 受 process_mode 门控），ESC 将永远无法打开菜单
	process_mode = Node.PROCESS_MODE_ALWAYS
	_font = FontUtils.get_mono_font()
	hide()


# ── 公开接口 ──────────────────────────────────────────────

func open() -> void:
	"""打开暂停菜单并暂停游戏（幂等）。"""
	if visible:
		return
	_saving = false
	_status_text = ""
	_hover_key = ""
	show()
	queue_redraw()
	if get_tree():
		get_tree().paused = true


func close() -> void:
	"""关闭暂停菜单并恢复游戏（幂等）。"""
	if not visible:
		return
	hide()
	if get_tree():
		get_tree().paused = false


func toggle() -> void:
	"""切换暂停菜单开关（ESC 入口）。"""
	if visible:
		close()
	else:
		open()


func show_status(text: String, is_error: bool = false) -> void:
	"""显示存档结果等状态文本（main_world 回填）。"""
	_saving = false
	_status_text = text
	_status_color = STATUS_ERR_COLOR if is_error else STATUS_OK_COLOR
	queue_redraw()


# ── 输入 ──────────────────────────────────────────────────

func _input(event: InputEvent) -> void:
	if event is InputEventKey and event.pressed and not event.echo:
		if event.keycode == KEY_ESCAPE:
			toggle()
			var vp := get_viewport()
			if vp:
				vp.set_input_as_handled()
			return
	if not visible:
		return
	if event is InputEventMouseMotion:
		var hovered: String = _hit_button(event.position)
		if hovered != _hover_key:
			_hover_key = hovered
			queue_redraw()
		return
	if event is InputEventMouseButton and event.pressed \
			and event.button_index == MOUSE_BUTTON_LEFT:
		var key: String = _hit_button(event.position)
		if not key.is_empty():
			_activate(key)
		var vp := get_viewport()
		if vp:
			vp.set_input_as_handled()


# ── 绘制 ──────────────────────────────────────────────────

func _draw() -> void:
	if _font == null:
		return
	draw_rect(Rect2(Vector2.ZERO, size), OVERLAY_COLOR)

	var buttons_h: float = BUTTONS.size() * BUTTON_H + (BUTTONS.size() - 1) * BUTTON_GAP
	var panel_h: float = PANEL_TITLE_H + buttons_h + PANEL_STATUS_H + PANEL_PADDING * 2
	var panel_rect := Rect2(
		(size.x - PANEL_W) * 0.5, (size.y - panel_h) * 0.5, PANEL_W, panel_h)
	draw_rect(panel_rect, PANEL_COLOR)
	draw_rect(panel_rect, Color(1, 1, 1, 0.15), false, 1.0)

	draw_string(_font, panel_rect.position + Vector2(PANEL_PADDING, PANEL_TITLE_H * 0.5 + 9),
		"游戏暂停", HORIZONTAL_ALIGNMENT_LEFT, -1, TITLE_FONT_SIZE, TITLE_COLOR)

	var y: float = panel_rect.position.y + PANEL_TITLE_H + PANEL_PADDING * 0.5
	_button_rects.clear()
	for b in BUTTONS:
		var key: String = b["key"]
		var rect := Rect2(panel_rect.position.x + (PANEL_W - BUTTON_W) * 0.5,
			y, BUTTON_W, BUTTON_H)
		_button_rects[key] = rect
		var hovered: bool = key == _hover_key
		_draw_button(rect, b["label"], hovered, b["danger"])
		if key == "settings" and not hovered:
			draw_string(_font,
				rect.position + Vector2(BUTTON_W + 12, BUTTON_H * 0.5 + 6),
				"( %s )" % SETTINGS_NOTE, HORIZONTAL_ALIGNMENT_LEFT, -1,
				NOTE_FONT_SIZE, SUBTITLE_COLOR)
		y += BUTTON_H + BUTTON_GAP

	if not _status_text.is_empty():
		draw_string(_font, panel_rect.position + Vector2(
			PANEL_PADDING, panel_h - PANEL_PADDING + 6),
			_status_text, HORIZONTAL_ALIGNMENT_LEFT, -1,
			NOTE_FONT_SIZE, _status_color)


func _draw_button(rect: Rect2, label: String, hovered: bool, danger: bool) -> void:
	var fill: Color
	if danger:
		fill = BUTTON_DANGER_HOVER_COLOR if hovered else BUTTON_DANGER_COLOR
	else:
		fill = BUTTON_HOVER_COLOR if hovered else BUTTON_COLOR
	draw_rect(rect, fill)
	draw_rect(rect, Color(1, 1, 1, 0.12), false, 1.0)
	var label_w: float = _font.get_string_size(
		label, HORIZONTAL_ALIGNMENT_LEFT, -1, BUTTON_FONT_SIZE).x
	draw_string(_font, rect.position + Vector2(
		(rect.size.x - label_w) * 0.5, rect.size.y * 0.5 + 7),
		label, HORIZONTAL_ALIGNMENT_LEFT, -1, BUTTON_FONT_SIZE, BUTTON_TEXT_COLOR)


func _hit_button(pos: Vector2) -> String:
	for key in _button_rects:
		if _button_rects[key].has_point(pos):
			return key
	return ""


# ── 动作 ──────────────────────────────────────────────────

func _activate(key: String) -> void:
	match key:
		"resume":
			close()
		"save":
			_start_save()
		"settings":
			_saving = false
			_status_text = "设置：%s" % SETTINGS_NOTE
			_status_color = STATUS_WAIT_COLOR
			queue_redraw()
		"menu":
			close()
			get_tree().change_scene_to_file(MAIN_MENU_SCENE)
		"quit":
			close()
			get_tree().quit()


func _start_save() -> void:
	if _saving:
		return
	_saving = true
	_status_text = "正在保存..."
	_status_color = STATUS_WAIT_COLOR
	queue_redraw()
	save_requested.emit()
