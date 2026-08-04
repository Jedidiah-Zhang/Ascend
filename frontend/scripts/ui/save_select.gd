"""存档选择页 — 列出存档位、管理操作与开始游戏（Issue #14）。

功能:
  - 列出已有存档：名称、游戏内时间、运行时长、快照数、最后游玩
  - 进入（读档）、重命名、复制、删除
  - 新建游戏（默认参数直接创建并进入世界，调参流程留待 Issue #8）
  - 返回主菜单

全部内容 _draw() 绘制 + 自绘命中检测；文本输入使用 LineEdit
（仅重命名/新建时显示），风格与调试终端一致。
"""

extends Control

class_name SaveSelect

const FontUtils = preload("res://scripts/utils/font_utils.gd")
const SaveApi = preload("res://scripts/ui/save_api.gd")
const SaveInfoFormatter = preload("res://scripts/ui/save_info_formatter.gd")

const MAIN_MENU_SCENE: String = "res://scenes/main_menu.tscn"
const MAIN_WORLD_SCENE: String = "res://scenes/main.tscn"

# ── 视觉常量 ──────────────────────────────────────────────

const BG_COLOR: Color = Color(0.05, 0.05, 0.10, 0.92)
const TITLE_COLOR: Color = Color(0.90, 0.93, 1.0)
const SUBTITLE_COLOR: Color = Color(0.55, 0.62, 0.75)
const ROW_COLOR: Color = Color(0.10, 0.12, 0.18, 0.85)
const ROW_HOVER_COLOR: Color = Color(0.14, 0.18, 0.26, 0.9)
const TEXT_COLOR: Color = Color(0.90, 0.90, 0.95)
const DIM_TEXT_COLOR: Color = Color(0.62, 0.65, 0.72)
const BUTTON_COLOR: Color = Color(0.18, 0.22, 0.32)
const BUTTON_HOVER_COLOR: Color = Color(0.26, 0.34, 0.48)
const BUTTON_DANGER_COLOR: Color = Color(0.30, 0.16, 0.16)
const BUTTON_DANGER_HOVER_COLOR: Color = Color(0.46, 0.22, 0.22)
const STATUS_OK_COLOR: Color = Color(0.55, 0.95, 0.55)
const STATUS_ERR_COLOR: Color = Color(0.95, 0.50, 0.45)
const STATUS_WAIT_COLOR: Color = Color(0.85, 0.85, 0.55)
const PANEL_COLOR: Color = Color(0.09, 0.11, 0.17, 0.97)

const TITLE_FONT_SIZE: int = 28
const ROW_TITLE_FONT_SIZE: int = 18
const ROW_INFO_FONT_SIZE: int = 13
const SMALL_FONT_SIZE: int = 13

const MARGIN: float = 24.0
const ROW_H: float = 76.0
const ROW_GAP: float = 8.0
const HEADER_H: float = 64.0
const FOOTER_H: float = 56.0
const ACTION_W: float = 72.0
const ACTION_H: float = 26.0
const ACTION_GAP: float = 6.0

## 操作按钮定义: {key, label, danger}
const ACTIONS: Array = [
	{"key": "play", "label": "进入", "danger": false},
	{"key": "rename", "label": "重命名", "danger": false},
	{"key": "export", "label": "复制", "danger": false},
	{"key": "delete", "label": "删除", "danger": true},
]


# ── 属性 ──────────────────────────────────────────────────

## 解析后的世界摘要列表
var _worlds: Array = []
## 状态文本（底部）
var _status_text: String = "正在读取存档..."
var _status_color: Color = STATUS_WAIT_COLOR
var _font: Font = null

## 列表滚动偏移（行单位，0=顶部）
var _scroll: float = 0.0
## 悬停行索引
var _hover_row: int = -1
## 悬停操作按钮 {row, action_index}
var _hover_action: Dictionary = {}
## 待确认删除的行（两次点击确认）
var _confirm_delete_row: int = -1
## 请求已发出等待响应中
var _busy: bool = false

## 重命名/新建输入模式: "" 无, "rename" 重命名, "create" 新建
var _input_mode: String = ""
## 输入目标行索引
var _input_row: int = -1
## 名称输入框
var _name_input: LineEdit = null


# ── 生命周期 ──────────────────────────────────────────────

func _ready() -> void:
	anchor_left = 0.0
	anchor_top = 0.0
	anchor_right = 1.0
	anchor_bottom = 1.0
	_font = FontUtils.get_mono_font()

	_name_input = LineEdit.new()
	_name_input.visible = false
	_name_input.placeholder_text = "存档名称"
	_name_input.max_length = 40
	_name_input.text_submitted.connect(_on_name_submitted)
	add_child(_name_input)

	Connection.message_received.connect(_on_message)
	_refresh_list()


func _exit_tree() -> void:
	if Connection.message_received.is_connected(_on_message):
		Connection.message_received.disconnect(_on_message)


# ── 数据 ──────────────────────────────────────────────────

func _refresh_list() -> void:
	"""请求存档列表。"""
	_status_text = "正在读取存档..."
	_status_color = STATUS_WAIT_COLOR
	Connection.send(SaveApi.list_request())


func _apply_worlds(payload: Dictionary) -> void:
	"""应用 save_list 响应。"""
	_worlds = SaveApi.parse_worlds(payload)
	_confirm_delete_row = -1
	_scroll = 0.0
	if _worlds.is_empty():
		_status_text = "暂无存档 — 点击「新建游戏」开始"
		_status_color = STATUS_OK_COLOR
	else:
		_status_text = "共 %d 个存档（快照 %d 个）" % [
			_worlds.size(), SaveApi.parse_snapshots(payload).size(),
		]
		_status_color = STATUS_OK_COLOR
	queue_redraw()


func _set_error(text: String) -> void:
	_status_text = text
	_status_color = STATUS_ERR_COLOR
	queue_redraw()


# ── 绘制 ──────────────────────────────────────────────────

func _draw() -> void:
	if _font == null:
		return
	var view_size: Vector2 = size

	# 背景
	draw_rect(Rect2(Vector2.ZERO, size), BG_COLOR)

	# 标题
	draw_string(_font, Vector2(MARGIN, HEADER_H * 0.5 + 6), "存档选择",
		HORIZONTAL_ALIGNMENT_LEFT, -1, TITLE_FONT_SIZE, TITLE_COLOR)

	# 返回按钮
	var back_rect := Rect2(view_size.x - MARGIN - 90, HEADER_H * 0.5 - 15, 90, 30)
	_draw_button(back_rect, "返回", _hover_action.get("key") == "back")
	_back_rect = back_rect

	# 列表区
	var list_top: float = HEADER_H
	var list_bottom: float = view_size.y - FOOTER_H
	var list_rect := Rect2(MARGIN, list_top, view_size.x - MARGIN * 2, list_bottom - list_top)
	var visible_rows: int = maxi(0, int((list_rect.size.y + ROW_GAP) / (ROW_H + ROW_GAP)))
	_max_scroll = maxf(0.0, _worlds.size() - visible_rows)
	_scroll = clampf(_scroll, 0.0, _max_scroll)

	_action_rects.clear()
	if _worlds.is_empty():
		var empty: String = "暂无存档"
		var w: float = _font.get_string_size(empty, HORIZONTAL_ALIGNMENT_LEFT, -1, 20).x
		draw_string(_font, list_rect.position + Vector2((list_rect.size.x - w) * 0.5, list_rect.size.y * 0.4),
			empty, HORIZONTAL_ALIGNMENT_LEFT, -1, 20, DIM_TEXT_COLOR)

	_hover_row = -1
	_hover_action = {}
	for i in _worlds.size():
		var row_float: float = list_rect.position.y + i * (ROW_H + ROW_GAP) - _scroll * (ROW_H + ROW_GAP)
		if row_float + ROW_H < list_top or row_float > list_bottom:
			continue
		var row_rect := Rect2(list_rect.position.x, row_float, list_rect.size.x, ROW_H)
		_draw_row(i, row_rect)

	# 底部：状态 + 新建按钮
	draw_string(_font, Vector2(MARGIN, view_size.y - 22), _status_text,
		HORIZONTAL_ALIGNMENT_LEFT, -1, SMALL_FONT_SIZE, _status_color)
	var create_rect := Rect2(view_size.x - MARGIN - 130, view_size.y - FOOTER_H + 12, 130, 32)
	_draw_button(create_rect, "新建游戏", _hover_action.get("key") == "create")
	_create_rect = create_rect

	# 名称输入弹层
	if _input_mode != "":
		_draw_input_dialog()


func _draw_row(index: int, rect: Rect2) -> void:
	var w: Dictionary = _worlds[index]
	var hovered: bool = _hover_row == index
	draw_rect(rect, ROW_HOVER_COLOR if hovered else ROW_COLOR)
	draw_rect(rect, Color(1, 1, 1, 0.08), false, 1.0)

	# 信息区
	draw_string(_font, rect.position + Vector2(14, 24), str(w["name"]),
		HORIZONTAL_ALIGNMENT_LEFT, -1, ROW_TITLE_FONT_SIZE, TEXT_COLOR)
	var info: String = "%s   时长 %s    快照 %d   最后游玩 %s    种子 %s" % [
		SaveInfoFormatter.game_time_string(w["game_time"]),
		SaveInfoFormatter.duration_string(w["play_duration_sec"]),
		w["snapshot_count"],
		SaveInfoFormatter.datetime_string(w["last_played_at"]),
		SaveInfoFormatter.seed_string(w["seed"]),
	]
	draw_string(_font, rect.position + Vector2(14, 48), info,
		HORIZONTAL_ALIGNMENT_LEFT, -1, ROW_INFO_FONT_SIZE, DIM_TEXT_COLOR)

	# 操作按钮区
	var actions_total: float = ACTIONS.size() * ACTION_W + (ACTIONS.size() - 1) * ACTION_GAP
	var ax: float = rect.end.x - 14 - actions_total
	for a in ACTIONS.size():
		var btn_rect := Rect2(ax + a * (ACTION_W + ACTION_GAP),
			rect.position.y + (ROW_H - ACTION_H) * 0.5, ACTION_W, ACTION_H)
		var action: Dictionary = ACTIONS[a]
		var hover: bool = _hover_row == index and _hover_action.get("action") == a
		var danger: bool = action["danger"]
		var label: String = action["label"]
		if danger and _confirm_delete_row == index:
			label = "确认?"
		_draw_button(btn_rect, label, hover, danger)
		# 记录按钮矩形供命中检测
		_action_rects.append({"rect": btn_rect, "row": index, "action": a})


func _draw_button(rect: Rect2, label: String, hovered: bool, danger: bool = false) -> void:
	var fill: Color
	if danger:
		fill = BUTTON_DANGER_HOVER_COLOR if hovered else BUTTON_DANGER_COLOR
	else:
		fill = BUTTON_HOVER_COLOR if hovered else BUTTON_COLOR
	draw_rect(rect, fill)
	draw_rect(rect, Color(1, 1, 1, 0.12), false, 1.0)
	var label_w: float = _font.get_string_size(label, HORIZONTAL_ALIGNMENT_LEFT, -1, 13).x
	draw_string(_font, rect.position + Vector2((rect.size.x - label_w) * 0.5, rect.size.y * 0.5 + 5),
		label, HORIZONTAL_ALIGNMENT_LEFT, -1, 13, TEXT_COLOR)


func _draw_input_dialog() -> void:
	var view_size: Vector2 = size
	var dlg_w: float = 420.0
	var dlg_h: float = 130.0
	var dlg_rect := Rect2((view_size.x - dlg_w) * 0.5, (view_size.y - dlg_h) * 0.5, dlg_w, dlg_h)
	draw_rect(Rect2(Vector2.ZERO, size), Color(0, 0, 0, 0.45))
	draw_rect(dlg_rect, PANEL_COLOR)
	draw_rect(dlg_rect, Color(1, 1, 1, 0.15), false, 1.0)

	var prompt: String = "新存档名称" if _input_mode == "create" else "重命名为"
	draw_string(_font, dlg_rect.position + Vector2(16, 26), prompt,
		HORIZONTAL_ALIGNMENT_LEFT, -1, 14, TITLE_COLOR)

	# 输入框位置（visible/focus 由 _open_input / _close_input 管理）
	_name_input.position = dlg_rect.position + Vector2(16, 38)
	_name_input.size = Vector2(dlg_w - 32, 30)

	# 确认/取消按钮（自绘命中）
	var ok_rect := Rect2(dlg_rect.position.x + dlg_w - 170, dlg_rect.position.y + 86, 70, 28)
	var cancel_rect := Rect2(dlg_rect.position.x + dlg_w - 90, dlg_rect.position.y + 86, 70, 28)
	_draw_button(ok_rect, "确定", _hover_action.get("key") == "ok")
	_draw_button(cancel_rect, "取消", _hover_action.get("key") == "cancel")
	_ok_rect = ok_rect
	_cancel_rect = cancel_rect


# ── 输入 ──────────────────────────────────────────────────

var _back_rect: Rect2 = Rect2()
var _create_rect: Rect2 = Rect2()
var _ok_rect: Rect2 = Rect2()
var _cancel_rect: Rect2 = Rect2()
## 本帧操作按钮矩形集合
var _action_rects: Array = []
var _max_scroll: float = 0.0


func _input(event: InputEvent) -> void:
	if event is InputEventMouseMotion:
		_update_hover(event.position)
		queue_redraw()
		return
	if event is InputEventMouseButton:
		if event.pressed and event.button_index == MOUSE_BUTTON_WHEEL_UP:
			_scroll -= 1.0
			queue_redraw()
		elif event.pressed and event.button_index == MOUSE_BUTTON_WHEEL_DOWN:
			_scroll += 1.0
			queue_redraw()
		elif event.pressed and event.button_index == MOUSE_BUTTON_LEFT:
			_handle_click(event.position)
			get_viewport().set_input_as_handled()
	if event is InputEventKey and event.pressed and event.keycode == KEY_ESCAPE:
		if _input_mode != "":
			_close_input()
		else:
			_go_back()
		get_viewport().set_input_as_handled()


func _update_hover(pos: Vector2) -> void:
	_hover_row = -1
	_hover_action = {}
	if _input_mode != "":
		if _ok_rect.has_point(pos):
			_hover_action = {"key": "ok"}
		elif _cancel_rect.has_point(pos):
			_hover_action = {"key": "cancel"}
		return
	if _back_rect.has_point(pos):
		_hover_action = {"key": "back"}
		return
	if _create_rect.has_point(pos):
		_hover_action = {"key": "create"}
		return
	# 操作按钮命中（优先于行 hover）
	for entry in _action_rects:
		if entry["rect"].has_point(pos):
			_hover_row = entry["row"]
			_hover_action = {"action": entry["action"]}
			return
	# 行 hover
	var list_top: float = HEADER_H
	var list_bottom: float = size.y - FOOTER_H
	for i in _worlds.size():
		var y: float = list_top + i * (ROW_H + ROW_GAP) - _scroll * (ROW_H + ROW_GAP)
		if y <= list_bottom and y + ROW_H >= list_top \
				and pos.x >= MARGIN and pos.x <= size.x - MARGIN \
				and pos.y >= y and pos.y <= y + ROW_H:
			_hover_row = i
			return


func _handle_click(pos: Vector2) -> void:
	if _busy:
		return
	if _input_mode != "":
		if _ok_rect.has_point(pos):
			_on_name_submitted(_name_input.text)
		elif _cancel_rect.has_point(pos):
			_close_input()
		return
	if _back_rect.has_point(pos):
		_go_back()
		return
	if _create_rect.has_point(pos):
		_open_input("create", -1)
		return
	for entry in _action_rects:
		if entry["rect"].has_point(pos):
			_activate_action(entry["row"], entry["action"])
			return


func _activate_action(row: int, action: int) -> void:
	if row < 0 or row >= _worlds.size():
		return
	var w: Dictionary = _worlds[row]
	var world_id: String = str(w["world_id"])
	var key: String = ACTIONS[action]["key"]
	match key:
		"play":
			_load_world(world_id)
		"rename":
			_open_input("rename", row)
		"export":
			_busy = true
			_status_text = "正在复制存档..."
			Connection.send(SaveApi.export_request(world_id))
		"delete":
			if _confirm_delete_row == row:
				_confirm_delete_row = -1
				_busy = true
				_status_text = "正在删除..."
				Connection.send(SaveApi.delete_request(world_id))
			else:
				_confirm_delete_row = row
				queue_redraw()


# ── 输入对话框 ────────────────────────────────────────────

func _open_input(mode: String, row: int) -> void:
	_input_mode = mode
	_input_row = row
	if mode == "rename" and row >= 0 and row < _worlds.size():
		_name_input.text = str(_worlds[row]["name"])
	else:
		_name_input.text = ""
	_name_input.visible = true
	_name_input.grab_focus()
	queue_redraw()


func _close_input() -> void:
	_input_mode = ""
	_input_row = -1
	_name_input.text = ""
	_name_input.visible = false
	queue_redraw()


func _on_name_submitted(text: String) -> void:
	var name: String = text.strip_edges()
	if name.is_empty():
		_set_error("名称不能为空")
		return
	if _input_mode == "create":
		_busy = true
		_status_text = "正在创建世界..."
		Connection.send(SaveApi.create_request(name, 0))
	elif _input_mode == "rename":
		if _input_row < 0 or _input_row >= _worlds.size():
			_close_input()
			return
		var world_id: String = str(_worlds[_input_row]["world_id"])
		_busy = true
		_status_text = "正在重命名..."
		Connection.send(SaveApi.rename_request(world_id, name))
	_close_input()


# ── 流程 ──────────────────────────────────────────────────

func _load_world(world_id: String) -> void:
	_busy = true
	_status_text = "正在进入世界..."
	_status_color = STATUS_WAIT_COLOR
	Connection.send(SaveApi.load_request(world_id))
	queue_redraw()


func _enter_world() -> void:
	"""切换到主世界场景（save_load 已置位，后端将重建并重启服务）。"""
	get_tree().change_scene_to_file(MAIN_WORLD_SCENE)


func _go_back() -> void:
	get_tree().change_scene_to_file(MAIN_MENU_SCENE)


# ── 消息处理 ──────────────────────────────────────────────

func _on_message(message: Dictionary) -> void:
	var msg_type: String = message.get("type", "")
	var request_type: String = message.get("request_type", "")

	if msg_type == "response":
		_busy = false
		match request_type:
			SaveApi.LIST:
				_apply_worlds(message.get("payload", {}))
			SaveApi.CREATE:
				var world_id: String = str(message.get("payload", {}).get("world_id", ""))
				if world_id.is_empty():
					_set_error("创建存档失败")
				else:
					_load_world(world_id)
			SaveApi.LOAD:
				_enter_world()
			SaveApi.RENAME, SaveApi.EXPORT:
				_refresh_list()
			SaveApi.DELETE:
				_refresh_list()
	elif msg_type == "error":
		_busy = false
		_confirm_delete_row = -1
		_set_error("请求失败：%s" % SaveApi.parse_error(message))
		queue_redraw()
