"""存档选择页 — 列出存档位、管理操作与开始游戏（Issue #14）。

功能:
  - 列出已有存档：名称、游戏内时间、运行时长、快照数、最后游玩
  - 进入（读档）、重命名、复制、删除
  - 新建游戏（默认参数直接创建并进入世界，调参流程留待 Issue #8）
  - 点击存档行展开行内「时间线分叉」：快照节点 + 当前时间点（特别标注），
    节点可直接点击——当前时间点进入世界、快照双击确认回滚；
    「最后进入」的世界带标注
  - 返回主菜单

全部内容 _draw() 绘制 + 自绘命中检测；文本输入使用 LineEdit
（仅重命名/新建时显示），风格与调试终端一致。
"""

extends Control

class_name SaveSelect

# FontUtils / SaveApi / SaveInfoFormatter 均为全局类（各自 class_name），
# 无需 preload 常量（preload 同名常量会遮蔽全局类并产生警告）

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

# ── 时间线视图常量 ─────────────────────────────────────────

const TL_INLINE_H: float = 230.0
const TL_GAP: float = 10.0
const TL_HEADER_H: float = 26.0
const TL_H_STEP: float = 110.0
const TL_V_STEP: float = 54.0
const TL_NODE_R: float = 12.0
const TL_LIVE_COLOR: Color = Color(0.85, 0.72, 0.30)
const TL_LIVE_HOVER_COLOR: Color = Color(0.98, 0.88, 0.50)
const TL_NODE_COLOR: Color = Color(0.32, 0.55, 0.85)
const TL_NODE_HOVER_COLOR: Color = Color(0.45, 0.70, 0.95)
const TL_AUTO_COLOR: Color = Color(0.38, 0.48, 0.60)
const TL_AUTO_HOVER_COLOR: Color = Color(0.52, 0.64, 0.78)
const TL_CONFIRM_COLOR: Color = Color(0.85, 0.35, 0.30)
const TL_EDGE_COLOR: Color = Color(0.55, 0.60, 0.72, 0.85)
const TL_HINT_COLOR: Color = Color(0.55, 0.62, 0.75)
const TL_BG_COLOR: Color = Color(0.07, 0.09, 0.14, 0.95)
const TL_LEGEND_W: float = 250.0
const TL_LEGEND_ROW_H: float = 18.0
const TL_ZOOM_MIN: float = 0.35
const TL_ZOOM_MAX: float = 2.5


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

## 输入模式: "" 无, "rename" 重命名（新建不再弹窗，直接默认名创建）
var _input_mode: String = ""
## 输入目标行索引
var _input_row: int = -1
## 名称输入框
var _name_input: LineEdit = null

## 全量快照列表（save_list 响应，时间线视图数据源）
var _snapshots: Array = []
## 引擎当前加载的世界（"最后进入"标注）
var _current_world_id: String = ""

# ── 时间线视图状态（行内展开） ────────────────────────────

## 展开时间线的行索引（-1 = 全部收起）
var _expanded_row: int = -1
## 时间线节点: [{id, depth, slot, is_live, suffix, ...}]
var _tl_nodes: Array = []
## 时间线边: [[parent_id, child_id], ...]
var _tl_edges: Array = []
## 悬停节点 id
var _tl_hover_id: String = ""
## 待确认回滚的快照 id（两次点击确认）
var _tl_confirm_id: String = ""
## 节点命中矩形: id → Rect2
var _tl_rects: Dictionary = {}
## 树左上角（绘制原点）
var _tl_origin: Vector2 = Vector2.ZERO
## 展开的时间线区域（命中判定）
var _tl_tree_rect: Rect2 = Rect2()
## 树视口（拖拽/缩放命中判定）
var _tl_body_rect: Rect2 = Rect2()
## 图例列区域（滚轮滚动命中判定）
var _tl_legend_rect: Rect2 = Rect2()
## 图例行命中矩形: id → Rect2
var _tl_legend_rects: Dictionary = {}
## 节点编号: id → 1..N（图例序号，按保存顺序）
var _tl_numbers: Dictionary = {}
## 保存顺序的节点 id 列表（编号与图例共用）
var _tl_sorted_ids: Array = []
## 树缩放（滚轮，展开时自动适配）
var _tl_zoom: float = 1.0
## 树平移（拖拽）
var _tl_pan: Vector2 = Vector2.ZERO
## 图例滚动偏移（行）
var _tl_legend_scroll: float = 0.0
## 拖拽平移中
var _tl_dragging: bool = false
var _tl_drag_last: Vector2 = Vector2.ZERO


# ── 生命周期 ──────────────────────────────────────────────

## 初始化界面：全屏锚点、等宽字体、名称输入框（隐藏），连接后端信号并请求存档列表。
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
	Connection.connection_lost.connect(_on_connection_lost)
	Connection.backend_failed.connect(_on_backend_failed)
	_refresh_list()


## 节点退出场景树时断开与 Connection 的信号连接，避免悬挂引用。
func _exit_tree() -> void:
	if Connection.message_received.is_connected(_on_message):
		Connection.message_received.disconnect(_on_message)
	if Connection.connection_lost.is_connected(_on_connection_lost):
		Connection.connection_lost.disconnect(_on_connection_lost)
	if Connection.backend_failed.is_connected(_on_backend_failed):
		Connection.backend_failed.disconnect(_on_backend_failed)


# ── 数据 ──────────────────────────────────────────────────

func _refresh_list() -> void:
	"""请求存档列表。"""
	_status_text = "正在读取存档..."
	_status_color = STATUS_WAIT_COLOR
	Connection.send(SaveApi.list_request())


func _apply_worlds(payload: Dictionary) -> void:
	"""应用 save_list 响应。"""
	_worlds = SaveApi.parse_worlds(payload)
	_snapshots = SaveApi.parse_snapshots(payload)
	_current_world_id = SaveApi.parse_current_world_id(payload)
	_confirm_delete_row = -1
	_scroll = 0.0
	_close_timeline()
	if _worlds.is_empty():
		_status_text = "暂无存档 — 点击「新建游戏」开始"
		_status_color = STATUS_OK_COLOR
	else:
		_status_text = "共 %d 个存档（快照 %d 个）" % [
			_worlds.size(), _snapshots.size(),
		]
		_status_color = STATUS_OK_COLOR
	queue_redraw()


## 设置底部状态文本并切换为错误样式（红色），触发重绘。
##
## Args:
##     text: 错误信息文本。
func _set_error(text: String) -> void:
	_status_text = text
	_status_color = STATUS_ERR_COLOR
	queue_redraw()


# ── 连接中断 ──────────────────────────────────────────────

func _on_connection_lost() -> void:
	"""连接断开时复位忙状态（响应可能永远不来，避免 UI 卡死）。"""
	if _busy:
		_busy = false
		_confirm_delete_row = -1
		_set_error("连接中断，操作未完成 — 请重试")
	queue_redraw()


func _on_backend_failed(reason: String) -> void:
	"""后端启动失败：同样复位忙状态。"""
	_busy = false
	_confirm_delete_row = -1
	_set_error("后端不可用：%s" % reason)
	queue_redraw()


# ── 绘制 ──────────────────────────────────────────────────

## 绘制页面全部内容：背景、标题与返回按钮、存档列表（含展开行的行内时间线）、底部状态与「新建游戏」按钮、名称输入弹层。
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
	var total_h: float = _worlds.size() * (ROW_H + ROW_GAP) \
		+ (TL_INLINE_H + TL_GAP if _expanded_row >= 0 else 0.0)
	_max_scroll = maxf(0.0, (total_h - (list_bottom - list_top)) / (ROW_H + ROW_GAP))
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
		var row_float: float = _row_display_y(i, list_top)
		if row_float + ROW_H < list_top or row_float > list_bottom:
			continue
		var row_rect := Rect2(list_rect.position.x, row_float, list_rect.size.x, ROW_H)
		_draw_row(i, row_rect)
		if i == _expanded_row:
			_draw_inline_timeline(row_rect)

	# 底部：状态 + 新建按钮
	draw_string(_font, Vector2(MARGIN, view_size.y - 22), _status_text,
		HORIZONTAL_ALIGNMENT_LEFT, -1, SMALL_FONT_SIZE, _status_color)
	var create_rect := Rect2(view_size.x - MARGIN - 130, view_size.y - FOOTER_H + 12, 130, 32)
	_draw_button(create_rect, "新建游戏", _hover_action.get("key") == "create")
	_create_rect = create_rect

	# 名称输入弹层
	if _input_mode != "":
		_draw_input_dialog()


func _row_display_y(index: int, list_top: float) -> float:
	"""行显示纵坐标：展开行下方行整体下移 TL_INLINE_H + TL_GAP。"""
	var y: float = list_top - _scroll * (ROW_H + ROW_GAP)
	for i in range(index):
		y += ROW_H + ROW_GAP
		if i == _expanded_row:
			y += TL_INLINE_H + TL_GAP
	return y


## 绘制单个存档行：名称、信息区（游戏时间/时长/快照数/最后游玩/种子）与右侧操作按钮，并把按钮矩形记入 _action_rects 供命中检测。
##
## Args:
##     index: 世界在 _worlds 中的行索引。
##     rect: 该行的绘制区域。
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


## 绘制通用自绘按钮：填充（危险色/悬停高亮）、描边、居中文字。
##
## Args:
##     rect: 按钮区域。
##     label: 按钮文字。
##     hovered: 是否悬停高亮。
##     danger: 是否危险操作（红色系），默认 false。
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


## 绘制重命名输入弹层：半透明遮罩 + 面板 + 提示文字，摆放输入框并自绘确定/取消按钮。
func _draw_input_dialog() -> void:
	var view_size: Vector2 = size
	var dlg_w: float = 420.0
	var dlg_h: float = 130.0
	var dlg_rect := Rect2((view_size.x - dlg_w) * 0.5, (view_size.y - dlg_h) * 0.5, dlg_w, dlg_h)
	draw_rect(Rect2(Vector2.ZERO, size), Color(0, 0, 0, 0.45))
	draw_rect(dlg_rect, PANEL_COLOR)
	draw_rect(dlg_rect, Color(1, 1, 1, 0.15), false, 1.0)

	var prompt: String = "重命名为"
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


## 处理全部输入：鼠标移动（拖拽平移/悬停更新）、滚轮（列表滚动或树区缩放/图例滚动）、左键点击分发、Esc 逐层关闭（时间线 → 输入框 → 返回）。
##
## Args:
##     event: 输入事件。
func _input(event: InputEvent) -> void:
	if event is InputEventMouseMotion:
		if _tl_dragging:
			_tl_pan += event.position - _tl_drag_last
			_tl_drag_last = event.position
			queue_redraw()
		_update_hover(event.position)
		queue_redraw()
		return
	if event is InputEventMouseButton:
		if not event.pressed:
			if event.button_index == MOUSE_BUTTON_LEFT:
				_tl_dragging = false
			return
		if event.button_index == MOUSE_BUTTON_WHEEL_UP:
			if _expanded_row >= 0 and _tl_tree_rect.has_point(event.position):
				_handle_tree_wheel(-1.0, event.position)
			else:
				_scroll -= 1.0
			queue_redraw()
		elif event.button_index == MOUSE_BUTTON_WHEEL_DOWN:
			if _expanded_row >= 0 and _tl_tree_rect.has_point(event.position):
				_handle_tree_wheel(1.0, event.position)
			else:
				_scroll += 1.0
			queue_redraw()
		elif event.button_index == MOUSE_BUTTON_LEFT:
			_handle_click(event.position)
			# _handle_click 可能即时切场景（进入/返回），节点出树后 viewport 为 null
			var vp := get_viewport()
			if vp:
				vp.set_input_as_handled()
	if event is InputEventKey and event.pressed and not event.echo and event.keycode == KEY_ESCAPE:
		if _expanded_row >= 0:
			_close_timeline()
		elif _input_mode != "":
			_close_input()
		else:
			_go_back()
		var vp := get_viewport()
		if vp:
			vp.set_input_as_handled()


func _handle_tree_wheel(dir: float, pos: Vector2) -> void:
	"""树区滚轮：图例列滚动列表，树区缩放（以光标为锚）。"""
	if _tl_legend_rect.has_point(pos):
		_tl_legend_scroll += dir
		return
	var factor: float = 1.15 if dir < 0.0 else 1.0 / 1.15
	var old_zoom: float = _tl_zoom
	_tl_zoom = clampf(_tl_zoom * factor, TL_ZOOM_MIN, TL_ZOOM_MAX)
	if _tl_zoom != old_zoom and _tl_body_rect.has_point(pos):
		# 保持光标下的树点不动
		var rel: Vector2 = pos - _tl_body_rect.position
		_tl_pan = rel - (rel - _tl_pan) * (_tl_zoom / old_zoom)


## 根据光标位置更新悬停状态（_hover_row / _hover_action / _tl_hover_id），按优先级：输入弹层按钮 → 返回/新建 → 时间线节点与图例行 → 行操作按钮 → 行主体。
##
## Args:
##     pos: 光标位置。
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
	# 展开时间线：节点 / 图例行悬停（互相联动高亮）
	if _expanded_row >= 0 and _tl_tree_rect.has_point(pos):
		var hid: String = _hit_timeline_node(pos)
		if hid.is_empty():
			hid = _hit_legend_row(pos)
		_tl_hover_id = hid
		return
	_tl_hover_id = ""
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
		var y: float = _row_display_y(i, list_top)
		if y <= list_bottom and y + ROW_H >= list_top \
				and pos.x >= MARGIN and pos.x <= size.x - MARGIN \
				and pos.y >= y and pos.y <= y + ROW_H:
			_hover_row = i
			return


## 左键点击分发：输入弹层按钮、返回/新建、时间线节点（激活/回滚确认）、树区空白开始拖拽、行操作按钮、行主体展开时间线；请求进行中忽略。
##
## Args:
##     pos: 点击位置。
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
		_create_new_world()
		return
	# 展开时间线：节点 / 图例行命中（优先于其它）
	if _expanded_row >= 0:
		var node_id: String = _hit_timeline_node(pos)
		if node_id.is_empty():
			node_id = _hit_legend_row(pos)
		if not node_id.is_empty():
			_activate_timeline_node(node_id)
			return
		# 树区空白按下 → 开始拖拽平移
		if _tl_body_rect.has_point(pos):
			_tl_dragging = true
			_tl_drag_last = pos
			return
	for entry in _action_rects:
		if entry["rect"].has_point(pos):
			_activate_action(entry["row"], entry["action"])
			return
	# 行主体点击 → 展开/收起该世界的时间线
	var row: int = _hit_row(pos)
	if row >= 0:
		_toggle_timeline(row)


func _hit_row(pos: Vector2) -> int:
	"""命中列表行（仅行主体，不含操作按钮与时间线区域）。"""
	var list_top: float = HEADER_H
	var list_bottom: float = size.y - FOOTER_H
	for i in _worlds.size():
		var y: float = _row_display_y(i, list_top)
		if y <= list_bottom and y + ROW_H >= list_top \
				and pos.x >= MARGIN and pos.x <= size.x - MARGIN \
				and pos.y >= y and pos.y <= y + ROW_H:
			return i
	return -1


# ── 时间线视图（行内展开，快照分叉） ───────────────────────

func _toggle_timeline(row: int) -> void:
	"""展开/收起指定行的时间线。"""
	if row < 0 or row >= _worlds.size():
		return
	if _expanded_row == row:
		_close_timeline()
		return
	_expanded_row = row
	_tl_hover_id = ""
	_tl_confirm_id = ""
	_tl_legend_scroll = 0.0
	_tl_dragging = false
	_tl_pan = Vector2.ZERO
	var w: Dictionary = _worlds[row]
	var world_id: String = str(w["world_id"])
	var snaps: Array = []
	for s in _snapshots:
		if s is Dictionary and str(s.get("world_id", "")) == world_id:
			snaps.append(s)
	var tree: Dictionary = TimelineLayout.build(
		snaps, int(w.get("game_time", 0)),
		str(w.get("live_origin", "")),
	)
	_tl_nodes = tree["nodes"]
	_tl_edges = tree["edges"]
	# 编号数据源：与暂停菜单「节点 N」共用同一保存顺序
	var numbered: Array = []
	for s in snaps:
		var suffix: String = str(s.get("suffix", ""))
		if suffix == "manual" or suffix == "auto":
			numbered.append(s)
	_tl_sorted_ids = TimelineLayout.save_order_ids(numbered)
	_fit_timeline_zoom()
	queue_redraw()


func _fit_timeline_zoom() -> void:
	"""展开时自动适配：长树缩小到视口（短树保持 1.0 上限）。"""
	_tl_zoom = 1.0
	if size.x <= 10.0:
		return
	var max_depth: float = 0.0
	var max_slot: float = 0.0
	for n in _tl_nodes:
		max_depth = maxf(max_depth, float(n["depth"]))
		max_slot = maxf(max_slot, float(n["slot"]))
	var body_w: float = size.x - MARGIN * 2 - 28 - TL_LEGEND_W
	var body_h: float = TL_INLINE_H - TL_HEADER_H - 8
	var fit_w: float = body_w / maxf(1.0, (max_depth + 1) * TL_H_STEP)
	var fit_h: float = body_h / maxf(1.0, (max_slot + 1) * TL_V_STEP)
	_tl_zoom = clampf(minf(1.0, minf(fit_w, fit_h)), TL_ZOOM_MIN, 1.0)


func _close_timeline() -> void:
	"""收起时间线。"""
	if _expanded_row < 0:
		return
	_expanded_row = -1
	_tl_nodes = []
	_tl_edges = []
	_tl_rects = {}
	_tl_tree_rect = Rect2()
	_tl_body_rect = Rect2()
	_tl_legend_rect = Rect2()
	_tl_legend_rects = {}
	_tl_numbers = {}
	_tl_sorted_ids = []
	_tl_hover_id = ""
	_tl_confirm_id = ""
	_tl_dragging = false
	_tl_pan = Vector2.ZERO
	_tl_zoom = 1.0
	_tl_legend_scroll = 0.0
	queue_redraw()


func _tl_node_pos(node: Dictionary) -> Vector2:
	"""节点屏幕位置（深度→x，槽位→y，缩放作用于间距）。"""
	return _tl_origin + Vector2(
		node["depth"] * TL_H_STEP * _tl_zoom,
		node["slot"] * TL_V_STEP * _tl_zoom,
	)


func _draw_inline_timeline(row_rect: Rect2) -> void:
	"""在展开行下方绘制时间线分叉树（编号节点 + 右侧图例，无弹窗）。

	节点圆圈只显示编号（绝不重叠），完整信息在右侧图例列表；
	树区支持拖拽平移与滚轮缩放，图例区滚轮滚动。

	面板高度 = min(TL_INLINE_H, 列表视口剩余空间)：展开行靠近底部时
	面板裁剪到 list_bottom，节点/边/图例逐元素剔除出界部分——
	内容永远不溢出列表区压到页脚（历史 bug：面板背景与内容
	延伸至窗口底边，覆盖状态栏/「新建游戏」按钮）。
	"""
	var avail_h: float = (size.y - FOOTER_H) - (row_rect.end.y + TL_GAP)
	var panel_h: float = minf(TL_INLINE_H, avail_h)
	if panel_h <= 0.0:
		return
	var tree_rect := Rect2(
		row_rect.position.x, row_rect.end.y + TL_GAP,
		row_rect.size.x, panel_h)
	_tl_tree_rect = tree_rect
	draw_rect(tree_rect, TL_BG_COLOR)
	draw_rect(tree_rect, Color(1, 1, 1, 0.10), false, 1.0)

	var w: Dictionary = _worlds[_expanded_row]
	var title: String = "时间线"
	if str(w.get("world_id", "")) == _current_world_id:
		title += "（最后进入）"
	draw_string(_font, tree_rect.position + Vector2(14, 18), title,
		HORIZONTAL_ALIGNMENT_LEFT, -1, 14, TITLE_COLOR)

	# 右侧图例列
	_tl_legend_rect = Rect2(tree_rect.end.x - TL_LEGEND_W, tree_rect.position.y,
		TL_LEGEND_W, tree_rect.size.y)
	draw_rect(_tl_legend_rect, Color(0, 0, 0, 0.25))
	draw_string(_font, _tl_legend_rect.position + Vector2(10, 18), "快照列表",
		HORIZONTAL_ALIGNMENT_LEFT, -1, 13, TITLE_COLOR)
	var legend_body := Rect2(
		_tl_legend_rect.position + Vector2(8, TL_HEADER_H),
		Vector2(TL_LEGEND_W - 16, _tl_legend_rect.size.y - TL_HEADER_H))

	# 树视口（图例左侧）
	var body := Rect2(tree_rect.position + Vector2(14, TL_HEADER_H),
		Vector2(tree_rect.size.x - 28 - TL_LEGEND_W, tree_rect.size.y - TL_HEADER_H - 8))
	_tl_body_rect = body

	# 编号：按保存顺序（saved_at 真实创建时刻，同秒游戏时间兜底）
	_tl_numbers.clear()
	for i in _tl_sorted_ids.size():
		_tl_numbers[_tl_sorted_ids[i]] = i + 1

	# 几何：缩放 + 平移（树小于视口时居中不可拖，大于时钳制可拖）
	var max_depth: float = 0.0
	var max_slot: float = 0.0
	for n in _tl_nodes:
		max_depth = maxf(max_depth, float(n["depth"]))
		max_slot = maxf(max_slot, float(n["slot"]))
	var tree_w: float = (max_depth + 1) * TL_H_STEP * _tl_zoom
	var tree_h: float = (max_slot + 1) * TL_V_STEP * _tl_zoom
	if tree_w >= body.size.x:
		_tl_pan.x = clampf(_tl_pan.x, body.size.x - tree_w, 0.0)
	else:
		_tl_pan.x = (body.size.x - tree_w) * 0.5
	if tree_h >= body.size.y:
		_tl_pan.y = clampf(_tl_pan.y, body.size.y - tree_h, 0.0)
	else:
		_tl_pan.y = (body.size.y - tree_h) * 0.5
	_tl_origin = body.position + _tl_pan

	# 节点位置表 + 边（逐元素剔除面板可见区之外的元素）
	var visible_rect: Rect2 = tree_rect.grow(TL_NODE_R)
	var pos_map: Dictionary = {}
	for n in _tl_nodes:
		pos_map[n["id"]] = _tl_node_pos(n)
	_tl_rects.clear()
	for e in _tl_edges:
		if not pos_map.has(e[0]) or not pos_map.has(e[1]):
			continue
		var a: Vector2 = pos_map[e[0]]
		var b: Vector2 = pos_map[e[1]]
		if not visible_rect.intersects(Rect2(a, b - a).abs()):
			continue
		var dir: Vector2 = (b - a).normalized()
		draw_line(a + dir * TL_NODE_R, b - dir * TL_NODE_R,
			TL_EDGE_COLOR, 2.0)

	# 节点（圆圈 + 编号/星标）
	for n in _tl_nodes:
		var pos: Vector2 = pos_map[n["id"]]
		if not visible_rect.has_point(pos):
			continue
		var hovered: bool = n["id"] == _tl_hover_id
		var confirming: bool = n["id"] == _tl_confirm_id
		var fill: Color
		if n["is_live"]:
			fill = TL_LIVE_HOVER_COLOR if hovered else TL_LIVE_COLOR
		elif str(n.get("suffix", "")) != "manual":
			fill = TL_CONFIRM_COLOR if confirming \
				else (TL_AUTO_HOVER_COLOR if hovered else TL_AUTO_COLOR)
		else:
			fill = TL_CONFIRM_COLOR if confirming \
				else (TL_NODE_HOVER_COLOR if hovered else TL_NODE_COLOR)
		draw_circle(pos, TL_NODE_R, fill)
		draw_arc(pos, TL_NODE_R, 0.0, TAU, 32, Color(1, 1, 1, 0.35), 1.0)
		var glyph: String = "★" if n["is_live"] else str(_tl_numbers.get(n["id"], ""))
		draw_string(_font, pos + Vector2(-12, 5), glyph,
			HORIZONTAL_ALIGNMENT_CENTER, 24, 11, Color(0.05, 0.06, 0.10, 0.95))
		if n["is_live"]:
			draw_string(_font, pos + Vector2(-34, TL_NODE_R + 15), "当前时间点",
				HORIZONTAL_ALIGNMENT_LEFT, -1, 13, TL_LIVE_COLOR)
			var gt: String = SaveInfoFormatter.game_time_string(
				int(w.get("game_time", 0)))
			draw_string(_font, pos + Vector2(-34, TL_NODE_R + 30), gt,
				HORIZONTAL_ALIGNMENT_LEFT, -1, 12, TL_HINT_COLOR)
		_tl_rects[n["id"]] = Rect2(pos - Vector2(30, 30), Vector2(60, 60))

	# 图例（可滚动，全部节点可达；顺序 = 保存顺序编号）
	var legend_rows: Array = [TimelineLayout.LIVE_ID]
	legend_rows.append_array(_tl_sorted_ids)
	var visible_rows: int = maxi(0, int(legend_body.size.y / TL_LEGEND_ROW_H))
	_tl_legend_scroll = clampf(_tl_legend_scroll, 0.0,
		maxf(0.0, legend_rows.size() - visible_rows))
	_tl_legend_rects.clear()
	for i in range(visible_rows):
		var row_idx: int = i + int(_tl_legend_scroll)
		if row_idx >= legend_rows.size():
			break
		var id: String = legend_rows[row_idx]
		var row_rc := Rect2(legend_body.position.x,
			legend_body.position.y + i * TL_LEGEND_ROW_H,
			legend_body.size.x, TL_LEGEND_ROW_H - 2)
		_tl_legend_rects[id] = row_rc
		if id == _tl_hover_id:
			draw_rect(row_rc, Color(1, 1, 1, 0.12))
		var num: String = "★" if id == TimelineLayout.LIVE_ID \
			else str(_tl_numbers.get(id, ""))
		var text: String = "%s  %s" % [num, _tl_legend_text(id)]
		draw_string(_font, row_rc.position + Vector2(4, 13), text,
			HORIZONTAL_ALIGNMENT_LEFT, -1, 12, DIM_TEXT_COLOR)


func _tl_legend_text(id: String) -> String:
	"""图例行文本：来源标识 + 时间标签。"""
	for n in _tl_nodes:
		if n["id"] != id:
			continue
		if n["is_live"]:
			return "当前时间点 · " + SaveInfoFormatter.game_time_string(
				int(_worlds[_expanded_row].get("game_time", 0)))
		var prefix: String = "自动 · " if str(n.get("suffix", "")) != "manual" else ""
		return prefix + _tl_node_label(n)
	return ""


func _tl_node_label(node: Dictionary) -> String:
	"""快照节点标签：优先游戏时间，缺失时用真实保存时间。"""
	var gt: int = int(node.get("time", 0))
	if gt > 0:
		return SaveInfoFormatter.game_time_string(gt)
	return SaveInfoFormatter.datetime_string(float(node.get("saved_at", 0.0)))


func _hit_timeline_node(pos: Vector2) -> String:
	"""命中树视口内的节点，返回节点 id（未命中返回空串）。"""
	if _expanded_row < 0 or not _tl_body_rect.has_point(pos):
		return ""
	for id in _tl_rects:
		if _tl_rects[id].has_point(pos):
			return id
	return ""


func _hit_legend_row(pos: Vector2) -> String:
	"""命中图例行，返回节点 id（未命中返回空串）。"""
	if _expanded_row < 0 or not _tl_legend_rect.has_point(pos):
		return ""
	for id in _tl_legend_rects:
		if _tl_legend_rects[id].has_point(pos):
			return id
	return ""


## 点击时间线节点：当前时间点（LIVE）直接进入世界；快照首次点击进入待确认态，再次点击同一节点发起回滚（load_request 带 snapshot）。
##
## Args:
##     id: 节点 id（TimelineLayout.LIVE_ID 或快照 file）。
func _activate_timeline_node(id: String) -> void:
	var world_id: String = str(_worlds[_expanded_row].get("world_id", ""))
	if id == TimelineLayout.LIVE_ID:
		# 当前时间点：直接进入世界（加载活目录）
		_load_world(world_id)
		return
	if _tl_confirm_id == id:
		# 两次点击确认 → 回滚到该快照
		_tl_confirm_id = ""
		_busy = true
		_status_text = "正在回滚到 %s ..." % id
		_status_color = STATUS_WAIT_COLOR
		Connection.send(SaveApi.load_request(world_id, id))
		queue_redraw()
		return
	_tl_confirm_id = id
	queue_redraw()


## 行操作按钮分发：进入（读档）、重命名（打开输入框）、复制（导出请求）、删除（两次点击确认后发请求）。
##
## Args:
##     row: 世界行索引。
##     action: ACTIONS 按钮索引。
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

func _create_new_world() -> void:
	"""新建游戏：默认名 = 当前日期时间，直接创建进入（无需弹窗输入）。

	想改名的玩家创建后经「重命名」修改。
	"""
	if _busy:
		return
	_busy = true
	_status_text = "正在创建世界..."
	_status_color = STATUS_WAIT_COLOR
	Connection.send(SaveApi.create_request(_default_save_name(), 0))
	queue_redraw()


static func _default_save_name() -> String:
	"""新建存档的默认名称（当前日期时间，分钟精度，格式与存档列表一致）。"""
	return SaveInfoFormatter.datetime_string(float(Time.get_unix_time_from_system()))


# ── 输入对话框（仅重命名） ────────────────────────────────

## 打开名称输入弹层：预填目标行现有名称，显示输入框并聚焦。
##
## Args:
##     mode: 输入模式（目前仅 "rename"）。
##     row: 目标行索引。
func _open_input(mode: String, row: int) -> void:
	_input_mode = mode
	_input_row = row
	if row >= 0 and row < _worlds.size():
		_name_input.text = str(_worlds[row]["name"])
	else:
		_name_input.text = ""
	_name_input.visible = true
	_name_input.grab_focus()
	queue_redraw()


## 关闭名称输入弹层：复位输入模式与目标行，清空并隐藏输入框。
func _close_input() -> void:
	_input_mode = ""
	_input_row = -1
	_name_input.text = ""
	_name_input.visible = false
	queue_redraw()


## 名称输入框提交回调：去除首尾空白后发送重命名请求；空名报错，目标行失效时直接关闭弹层。
##
## Args:
##     text: 输入框当前文本。
func _on_name_submitted(text: String) -> void:
	var save_name: String = text.strip_edges()
	if save_name.is_empty():
		_set_error("名称不能为空")
		return
	if _input_row < 0 or _input_row >= _worlds.size():
		_close_input()
		return
	var world_id: String = str(_worlds[_input_row]["world_id"])
	_busy = true
	_status_text = "正在重命名..."
	Connection.send(SaveApi.rename_request(world_id, save_name))
	_close_input()


# ── 流程 ──────────────────────────────────────────────────

## 发起读档请求（加载活目录）并置忙状态，成功后由 save_load 响应切场景进入世界。
##
## Args:
##     world_id: 目标世界 id。
func _load_world(world_id: String) -> void:
	_busy = true
	_status_text = "正在进入世界..."
	_status_color = STATUS_WAIT_COLOR
	Connection.send(SaveApi.load_request(world_id))
	queue_redraw()


func _enter_world() -> void:
	"""切换到主世界场景（save_load 已置位，后端将重建并重启服务）。"""
	get_tree().change_scene_to_file(MAIN_WORLD_SCENE)


## 返回主菜单场景。
func _go_back() -> void:
	get_tree().change_scene_to_file(MAIN_MENU_SCENE)


# ── 消息处理 ──────────────────────────────────────────────

## Connection 消息回调：response 按请求类型分发（列表应用、创建后进入世界、读档后切场景、改名/复制/删除后刷新），error 显示解析后的错误文本并复位忙状态。
##
## Args:
##     message: 后端消息字典（type / request_type / payload）。
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
