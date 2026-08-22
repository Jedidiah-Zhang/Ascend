"""存档选择页 — 列出存档位、管理操作与开始游戏（Issue #14）。

功能:
  - 列出已有存档：名称、游戏内时间、运行时长、快照数、最后游玩
  - 进入（读档）、重命名、复制、删除
  - 新建游戏（默认参数直接创建并进入世界，调参流程留待 Issue #8）
   - 点击存档行展开行内「时间线分叉」：快照节点 + 当前时间点（特别标注），
     节点可直接点击——当前时间点进入世界、快照选中后弹出操作面板
     （进入存档点 / 删除存档点 / 删除分支）；「最后进入」的世界带标注
  - 返回主菜单

进程模型（一进程一模式）：进入世界 / 回滚 = 停菜单进程 → 以
--world-id/--snapshot 拉起世界进程（Connection.restart_backend），
连接就绪（connection_established）后切入世界场景。

全部内容 _draw() 绘制 + 自绘命中检测；文本输入使用 LineEdit
（仅重命名/新建时显示），风格与调试终端一致。
"""

extends Control

class_name SaveSelect

# FontUtils / SaveApi / SaveInfoFormatter 均为全局类（各自 class_name），
# 无需 preload 常量（preload 同名常量会遮蔽全局类并产生警告）

const MAIN_MENU_SCENE: String = "res://scenes/main_menu.tscn"
const MAIN_WORLD_SCENE: String = "res://scenes/main.tscn"
const WORLD_SETUP_SCENE: String = "res://scenes/world_setup.tscn"

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

## 操作按钮定义: {key, label_key, danger}
const ACTIONS: Array = [
	{"key": "play", "label_key": "ui.saves.enter", "danger": false},
	{"key": "rename", "label_key": "ui.saves.rename", "danger": false},
	{"key": "export", "label_key": "ui.saves.copy", "danger": false},
	{"key": "delete", "label_key": "ui.saves.delete", "danger": true},
]

# ── 时间线视图常量 ─────────────────────────────────────────



# ── 属性 ──────────────────────────────────────────────────

## 解析后的世界摘要列表
var _worlds: Array = []
## 状态文本（底部）
var _status_text: String = ""
var _status_color: Color = STATUS_WAIT_COLOR
var _font: Font = null

## 列表滚动偏移（行单位，0=顶部）
var _scroll: float = 0.0
## 悬停行索引
var _hover_row: int = -1
## 悬停操作按钮 {row, action_index}
var _hover_action: Dictionary = {}
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
## 进入世界流程中（restart_backend 已发起，连接就绪后切场景）
var _entering_world: bool = false

## 后端进程启动器（测试可注入；默认走 Connection 进程切换）。
## 参数 = 世界进程 CLI 参数（["--world-id", id] 或 + ["--snapshot", file]）。
var backend_launcher: Callable = _launch_backend_default


func _launch_backend_default(args: PackedStringArray) -> void:
	"""默认进程切换：Connection.restart_backend（停菜单进程 → 拉起世界进程）。"""
	Connection.restart_backend(args)

# ── 时间线视图状态（行内展开） ────────────────────────────

## 时间线视图绘制器（纯绘制/几何/命中；交互决策保留在本文件）
var _tl_painter: SnapshotTimelinePainter = SnapshotTimelinePainter.new()
## 展开时间线的行索引（-1 = 全部收起）
var _expanded_row: int = -1
## 时间线节点: [{id, depth, slot, is_live, suffix, ...}]
var _tl_nodes: Array = []
## 时间线边: [[parent_id, child_id], ...]
var _tl_edges: Array = []
## 悬停节点 id
var _tl_hover_id: String = ""
## 选中节点 id（点击节点后出现操作面板）
var _panel_node_id: String = ""
## 操作面板按钮命中矩形: action → Rect2
var _panel_rects: Dictionary = {}
## 面板按钮悬停 action
var _panel_hover: String = ""
## 删除确认弹窗: {} 关闭; {node_id, action} 打开（action: "delete"/"prune"）
var _dlg_confirm: Dictionary = {}
## 弹窗按钮命中矩形: {"ok": Rect2, "cancel": Rect2}
var _dlg_rects: Dictionary = {}
## 弹窗面板矩形（点外部关闭判定）
var _dlg_rect: Rect2 = Rect2()
## 弹窗按钮悬停 key
var _dlg_hover: String = ""
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
	_name_input.placeholder_text = tr("ui.saves.name_placeholder")
	_name_input.max_length = 40
	_name_input.text_submitted.connect(_on_name_submitted)
	add_child(_name_input)

	Connection.backend_failed.connect(_on_backend_failed)
	Connection.connection_established.connect(_on_connected)
	if not Settings.locale_changed.is_connected(_on_locale_changed):
		Settings.locale_changed.connect(_on_locale_changed)
	# 握手完成前 send() 会被丢弃：已连接才立即请求，否则等 connection_established
	if Connection.status == Connection.Status.CONNECTED:
		_refresh_list()


## 节点退出场景树时断开与 Connection 的信号连接，避免悬挂引用。
func _exit_tree() -> void:
	if Connection.backend_failed.is_connected(_on_backend_failed):
		Connection.backend_failed.disconnect(_on_backend_failed)
	if Connection.connection_established.is_connected(_on_connected):
		Connection.connection_established.disconnect(_on_connected)
	if Settings.locale_changed.is_connected(_on_locale_changed):
		Settings.locale_changed.disconnect(_on_locale_changed)


## 连接就绪回调（握手完成）：进入世界流程中则切场景，否则拉取存档列表。
## 进入场景时若连接尚在握手窗口，_ready 的首次请求会被 send() 丢弃——
## 统一由本信号驱动，保证每次连接就绪都刷新。
func _on_connected(_host: String, _port: int) -> void:
	if _entering_world:
		# 世界进程已就绪（握手完成）：切换主世界场景
		_entering_world = false
		_enter_world()
		return
	_refresh_list()


# ── 数据 ──────────────────────────────────────────────────

func _refresh_list() -> void:
	"""请求存档列表（响应回调应用列表；错误/超时/断线经回调复位忙状态）。"""
	_status_text = tr("ui.saves.loading")
	_status_color = STATUS_WAIT_COLOR
	Connection.send(SaveApi.list_request(), _on_list_response)


## save_list 响应回调：应用列表或显示错误（列表刷新不锁忙状态）。
func _on_list_response(message: Dictionary) -> void:
	_busy = false
	if message.get("type", "") == "error":
		_on_request_error(message)
		return
	_apply_worlds(message.get("payload", {}))
	queue_redraw()


## 变更类请求（改名/复制/删除）的响应回调：成功后刷新列表。
func _on_simple_refresh(message: Dictionary) -> void:
	_busy = false
	if message.get("type", "") == "error":
		_on_request_error(message)
		return
	_refresh_list()


## 请求失败统一处理：关闭确认弹窗、显示解析后的错误文本。
func _on_request_error(message: Dictionary) -> void:
	_close_confirm_dialog()
	_set_error(tr("ui.common.request_failed").format({
		"reason": SaveApi.parse_error(message),
	}))
	queue_redraw()


func _apply_worlds(payload: Dictionary) -> void:
	"""应用 save_list 响应。

	若此前有展开的时间线，数据刷新后重建保持展开（删除节点后
	不收起时间轴）；展开的世界行若已不存在（世界被删除）则收起。
	"""
	_worlds = SaveApi.parse_worlds(payload)
	_snapshots = SaveApi.parse_snapshots(payload)
	_current_world_id = SaveApi.parse_current_world_id(payload)
	_scroll = 0.0
	var keep_expanded: int = _expanded_row
	_close_timeline()
	if keep_expanded >= 0 and keep_expanded < _worlds.size():
		_toggle_timeline(keep_expanded)
	if _worlds.is_empty():
		_status_text = tr("ui.saves.empty_hint")
		_status_color = STATUS_OK_COLOR
	else:
		_status_text = tr("ui.saves.count").format({
			"worlds": _worlds.size(), "snapshots": _snapshots.size(),
		})
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


# ── 连接失效 ──────────────────────────────────────────────

## 连接失效：挂起请求由 Connection 统一作废（回调收到本地 error，
## 忙状态随回调复位），此处兜底复位全局状态。
func _on_backend_failed(reason: String) -> void:
	"""后端启动失败：同样复位忙状态。"""
	_busy = false
	_close_confirm_dialog()
	_entering_world = false
	_set_error(tr("ui.common.backend_unavailable").format({"reason": reason}))
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
	draw_string(_font, Vector2(MARGIN, HEADER_H * 0.5 + 6), tr("ui.saves.title"),
		HORIZONTAL_ALIGNMENT_LEFT, -1, TITLE_FONT_SIZE, TITLE_COLOR)

	# 返回按钮
	var back_rect := Rect2(view_size.x - MARGIN - 90, HEADER_H * 0.5 - 15, 90, 30)
	_draw_button(back_rect, tr("ui.back"), _hover_action.get("key") == "back")
	_back_rect = back_rect

	# 列表区
	var list_top: float = HEADER_H
	var list_bottom: float = view_size.y - FOOTER_H
	var list_rect := Rect2(MARGIN, list_top, view_size.x - MARGIN * 2, list_bottom - list_top)
	var total_h: float = _worlds.size() * (ROW_H + ROW_GAP) \
		+ (SnapshotTimelinePainter.INLINE_H + SnapshotTimelinePainter.GAP if _expanded_row >= 0 else 0.0)
	_max_scroll = maxf(0.0, (total_h - (list_bottom - list_top)) / (ROW_H + ROW_GAP))
	_scroll = clampf(_scroll, 0.0, _max_scroll)

	_action_rects.clear()
	if _worlds.is_empty():
		var empty: String = tr("ui.saves.empty")
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
	_draw_button(create_rect, tr("ui.saves.new_game"), _hover_action.get("key") == "create")
	_create_rect = create_rect

	# 名称输入弹层
	if _input_mode != "":
		_draw_input_dialog()

	# 节点操作面板（最上层）
	if _panel_node_id != "":
		_draw_action_panel()

	# 删除确认弹窗（模态，最最上层）
	if not _dlg_confirm.is_empty():
		_draw_confirm_dialog()


func _row_display_y(index: int, list_top: float) -> float:
	"""行显示纵坐标：展开行下方行整体下移时间线高度（裁到列表底部）。"""
	var y: float = list_top - _scroll * (ROW_H + ROW_GAP)
	for i in range(index):
		y += ROW_H + ROW_GAP
		if i == _expanded_row:
			y += SnapshotTimelinePainter.INLINE_H + SnapshotTimelinePainter.GAP
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
	var info: String = tr("ui.saves.info").format({
		"name": SaveInfoFormatter.game_time_string(w["game_time"]),
		"duration": SaveInfoFormatter.duration_string(w["play_duration_sec"]),
		"snapshots": w["snapshot_count"],
		"last_played": SaveInfoFormatter.datetime_string(w["last_played_at"]),
		"seed": SaveInfoFormatter.seed_string(w["seed"]),
	})
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
		var label: String = tr(action["label_key"])
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

	var prompt: String = tr("ui.saves.rename_prompt")
	draw_string(_font, dlg_rect.position + Vector2(16, 26), prompt,
		HORIZONTAL_ALIGNMENT_LEFT, -1, 14, TITLE_COLOR)

	# 输入框位置（visible/focus 由 _open_input / _close_input 管理）
	_name_input.position = dlg_rect.position + Vector2(16, 38)
	_name_input.size = Vector2(dlg_w - 32, 30)

	# 确认/取消按钮（自绘命中）
	var ok_rect := Rect2(dlg_rect.position.x + dlg_w - 170, dlg_rect.position.y + 86, 70, 28)
	var cancel_rect := Rect2(dlg_rect.position.x + dlg_w - 90, dlg_rect.position.y + 86, 70, 28)
	_draw_button(ok_rect, tr("ui.confirm"), _hover_action.get("key") == "ok")
	_draw_button(cancel_rect, tr("ui.cancel"), _hover_action.get("key") == "cancel")
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


## 处理全部输入：鼠标移动（拖拽平移/悬停更新）、滚轮（列表滚动或树区缩放/图例滚动）、左键点击分发、Esc 逐层关闭（操作面板 → 时间线 → 输入框 → 返回）。
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
		if not _dlg_confirm.is_empty():
			_close_confirm_dialog()
		elif _panel_node_id != "":
			_close_action_panel()
		elif _expanded_row >= 0:
			_close_timeline()
		elif _input_mode != "":
			_close_input()
		else:
			_go_back()
		var vp := get_viewport()
		if vp:
			vp.set_input_as_handled()


func _handle_tree_wheel(dir: float, pos: Vector2) -> void:
	"""树区滚轮：图例列滚动列表，树区缩放（以光标为锚）——委托绘制器。"""
	_tl_painter.wheel(dir, pos)
	_tl_zoom = _tl_painter.zoom
	_tl_pan = _tl_painter.pan
	_tl_legend_scroll = _tl_painter.legend_scroll


## 根据光标位置更新悬停状态（_hover_row / _hover_action / _tl_hover_id），按优先级：输入弹层按钮 → 操作面板按钮 → 返回/新建 → 时间线节点与图例行 → 行操作按钮 → 行主体。
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
	# 删除确认弹窗优先：弹窗按钮悬停（模态，其余元素不参与）
	if not _dlg_confirm.is_empty():
		_dlg_hover = ""
		for key in _dlg_rects:
			if _dlg_rects[key].has_point(pos):
				_dlg_hover = key
				return
		_panel_hover = ""
		_tl_hover_id = ""
		return
	# 操作面板优先：面板按钮悬停（节点悬停联动保留给面板外的树区）
	if _panel_node_id != "":
		_panel_hover = ""
		for action in _panel_rects:
			if _panel_rects[action].has_point(pos):
				_panel_hover = action
				_tl_hover_id = ""
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


## 左键点击分发：输入弹层按钮、操作面板按钮、返回/新建、时间线节点（选中/切换面板）、树区空白开始拖拽、行操作按钮、行主体展开时间线；请求进行中忽略。
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
	# 删除确认弹窗优先：确定 → 执行；取消或点弹窗外 → 关闭
	if not _dlg_confirm.is_empty():
		if _dlg_rects.get("ok", Rect2()).has_point(pos):
			if str(_dlg_confirm.get("action", "")) == "delete_world":
				_confirm_delete_world()
			else:
				var node_id: String = str(_dlg_confirm.get("node_id", ""))
				var world_id: String = str(_worlds[_expanded_row].get("world_id", ""))
				_confirm_snapshot_delete(world_id, node_id)
		elif _dlg_rects.get("cancel", Rect2()).has_point(pos) \
				or not _dlg_rect.has_point(pos):
			_close_confirm_dialog()
		return
	# 操作面板按钮优先（点在按钮上执行，点面板外不拦截其余判定）
	if _panel_node_id != "":
		for action in _panel_rects:
			if _panel_rects[action].has_point(pos):
				_handle_panel_action(action)
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
		# 树区空白按下 → 关闭面板并开始拖拽平移
		if _tl_body_rect.has_point(pos):
			_panel_node_id = ""
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
	_panel_node_id = ""
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
	_tl_painter.set_tree(_tl_nodes, _tl_edges, _tl_sorted_ids)
	_tl_painter.setup(self, _font, w, _current_world_id,
		_tl_zoom, _tl_pan, _tl_legend_scroll, _tl_hover_id, _panel_node_id)
	_tl_painter.fit_zoom()
	_tl_zoom = _tl_painter.zoom
	queue_redraw()


func _close_timeline() -> void:
	"""收起时间线。"""
	if _expanded_row < 0:
		return
	_tl_painter.reset()
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
	_panel_node_id = ""
	_panel_rects = {}
	_tl_dragging = false
	_tl_pan = Vector2.ZERO
	_tl_zoom = 1.0
	_tl_legend_scroll = 0.0
	queue_redraw()


func _draw_inline_timeline(row_rect: Rect2) -> void:
	"""在展开行下方绘制时间线分叉树（委托 SnapshotTimelinePainter 绘制器）。

	绘制与几何/命中全部在绘制器内完成（纯 RefCounted，可单测）；
	本函数只负责注入视图数据与交互状态，并把命中几何镜像回
	_tl_* 成员（_click/_hover 命中判定与测试断言使用）。
	"""
	_tl_painter.setup(self, _font, _worlds[_expanded_row], _current_world_id,
		_tl_zoom, _tl_pan, _tl_legend_scroll, _tl_hover_id, _panel_node_id)
	_tl_painter.layout(row_rect)
	_tl_painter.paint()
	_tl_tree_rect = _tl_painter.tree_rect
	_tl_body_rect = _tl_painter.body_rect
	_tl_legend_rect = _tl_painter.legend_rect
	_tl_origin = _tl_painter.origin
	_tl_rects = _tl_painter.node_rects
	_tl_legend_rects = _tl_painter.legend_rects
	_tl_numbers = _tl_painter.numbers
	_tl_zoom = _tl_painter.zoom
	_tl_pan = _tl_painter.pan
	_tl_legend_scroll = _tl_painter.legend_scroll


func _hit_timeline_node(pos: Vector2) -> String:
	"""命中树视口内的节点，返回节点 id（未命中返回空串）。"""
	return _tl_painter.hit_node(pos)


func _hit_legend_row(pos: Vector2) -> String:
	"""命中图例行，返回节点 id（未命中返回空串）。"""
	return _tl_painter.hit_legend(pos)


func _tl_node_label(node: Dictionary) -> String:
	"""快照节点标签（委托绘制器；面板标题/确认弹窗复用）。"""
	return _tl_painter.node_label(node)


## 点击时间线节点：当前时间点（LIVE 或当前 auto 记录）直接进入世界；
## 快照节点选中并弹出操作面板（进入存档点 / 删除存档点 / 删除分支）；
## 再次点击同一节点切换面板开闭。
##
## Args:
##     id: 节点 id（TimelineLayout.LIVE_ID 或快照 file）。
func _activate_timeline_node(id: String) -> void:
	var world_id: String = str(_worlds[_expanded_row].get("world_id", ""))
	if id == TimelineLayout.LIVE_ID:
		# 当前时间点：直接进入世界（加载活目录）
		_load_world(world_id)
		return
	# 当前位置 = 当前 auto 记录（is_live）：直接进入世界
	for n in _tl_nodes:
		if n["id"] == id and n["is_live"]:
			_load_world(world_id)
			return
	if _panel_node_id == id:
		# 点击已选中节点 = 切换面板开闭
		_close_action_panel()
		return
	_panel_node_id = id
	queue_redraw()


func _confirm_rollback(world_id: String, id: String) -> void:
	"""面板「进入存档点」：世界进程带 --snapshot 重启。"""
	_close_action_panel()
	_busy = true
	_status_text = tr("ui.saves.rolling_back").format({"id": id})
	_status_color = STATUS_WAIT_COLOR
	_entering_world = true
	backend_launcher.call(PackedStringArray(["--world-id", world_id, "--snapshot", id]))
	queue_redraw()


func _confirm_snapshot_delete(world_id: String, id: String) -> void:
	"""删除确认弹窗确定后：发送删除请求，范围由弹窗动作决定。

	delete = 单点删除（后代重接）；prune = 分支裁剪（节点 + 后代）。
	"""
	var recursive: bool = str(_dlg_confirm.get("action", "")) == "prune"
	_close_confirm_dialog()
	_close_action_panel()
	_busy = true
	_status_text = tr("ui.saves.deleting_snapshot") if recursive else tr("ui.saves.deleting_node")
	_status_color = STATUS_WAIT_COLOR
	Connection.send(SaveApi.snapshot_delete_request(world_id, id, recursive), _on_simple_refresh)
	queue_redraw()


func _confirm_delete_world() -> void:
	"""存档删除弹窗确定后：发送世界删除请求（连带快照）。"""
	var row: int = int(_dlg_confirm.get("row", -1))
	_close_confirm_dialog()
	if row < 0 or row >= _worlds.size():
		return
	var world_id: String = str(_worlds[row].get("world_id", ""))
	_busy = true
	_status_text = tr("ui.saves.deleting")
	_status_color = STATUS_WAIT_COLOR
	Connection.send(SaveApi.delete_request(world_id), _on_simple_refresh)
	queue_redraw()


# ── 节点操作面板（进入存档点 / 删除存档点 / 删除分支） ──────

## 面板动作列表：进入存档点恒有；删除分支仅当节点有后代时显示。
func _panel_actions() -> Array:
	var items: Array = [
		{"action": "enter", "label_key": "ui.saves.enter_snapshot", "danger": false},
		{"action": "delete", "label_key": "ui.saves.delete_snapshot", "danger": true},
	]
	var node_id: String = _panel_node_id
	for n in _tl_nodes:
		if n["id"] == node_id and not (n["children"] as Array).is_empty():
			items.append({"action": "prune", "label_key": "ui.saves.prune_branch", "danger": true})
			break
	return items


func _draw_action_panel() -> void:
	"""自绘节点操作面板：标题（节点标签）+ 动作按钮（命中矩形记入 _panel_rects）。

	面板贴在选中节点右上方，钳制在时间线面板可见区内。
	"""
	var node_pos: Vector2 = _tl_rects.get(_panel_node_id, Vector2.ZERO).get_center()
	var node_label: String = ""
	for n in _tl_nodes:
		if n["id"] == _panel_node_id:
			node_label = _tl_node_label(n)
			break
	var btn_w: float = 160.0
	var btn_h: float = 26.0
	var pad: float = 8.0
	var title_h: float = 20.0
	var items: Array = _panel_actions()
	var panel_rect := Rect2(
		node_pos + Vector2(26, -title_h - btn_h - pad * 2),
		Vector2(btn_w + pad * 2, title_h + items.size() * btn_h + pad * 3),
	)
	if panel_rect.end.x > _tl_tree_rect.end.x - 4:
		panel_rect.position.x = _tl_tree_rect.end.x - 4 - panel_rect.size.x
	if panel_rect.end.y > _tl_tree_rect.end.y - 4:
		panel_rect.position.y = _tl_tree_rect.end.y - 4 - panel_rect.size.y
	draw_rect(panel_rect, PANEL_COLOR)
	draw_rect(panel_rect, Color(1, 1, 1, 0.15), false, 1.0)
	draw_string(_font, panel_rect.position + Vector2(pad, pad + 13), node_label,
		HORIZONTAL_ALIGNMENT_LEFT, -1, 12, SnapshotTimelinePainter.HINT_COLOR)
	_panel_rects.clear()
	for i in items.size():
		var item: Dictionary = items[i]
		var btn_rect := Rect2(
			panel_rect.position + Vector2(pad, pad + title_h + i * btn_h),
			Vector2(btn_w, btn_h),
		)
		_panel_rects[item["action"]] = btn_rect
		_draw_button(btn_rect, tr(str(item["label_key"])),
			_panel_hover == item["action"], item["danger"])


func _handle_panel_action(action: String) -> void:
	"""面板按钮分发：进入存档点直接执行；删除类打开确认弹窗。"""
	var node_id: String = _panel_node_id
	var world_id: String = str(_worlds[_expanded_row].get("world_id", ""))
	if action == "enter":
		_confirm_rollback(world_id, node_id)
		return
	if action == "delete" or action == "prune":
		_open_confirm_dialog(node_id, action)


# ── 删除确认弹窗 ──────────────────────────────────────────

## 打开删除确认弹窗（模态）：确定后发送删除请求，取消/点外部/Esc 关闭。
##
## Args:
##     node_id: 目标快照节点 id。
##     action: "delete" 单点删除 / "prune" 分支裁剪。
func _open_confirm_dialog(node_id: String, action: String) -> void:
	var label: String = ""
	for n in _tl_nodes:
		if n["id"] == node_id:
			label = _tl_node_label(n)
			break
	_dlg_confirm = {"node_id": node_id, "action": action, "node_label": label}
	_dlg_hover = ""
	queue_redraw()


## 打开存档（世界）删除确认弹窗。
##
## Args:
##     row: 目标世界行索引。
func _open_delete_world_dialog(row: int) -> void:
	if row < 0 or row >= _worlds.size():
		return
	_dlg_confirm = {"action": "delete_world", "row": row,
		"world_name": str(_worlds[row].get("name", ""))}
	_dlg_hover = ""
	queue_redraw()


func _close_confirm_dialog() -> void:
	"""关闭删除确认弹窗（面板保持）。"""
	_dlg_confirm = {}
	_dlg_rects = {}
	_dlg_rect = Rect2()
	_dlg_hover = ""
	queue_redraw()


func _draw_confirm_dialog() -> void:
	"""自绘删除确认弹窗：遮罩 + 面板 + 标题/说明 + 删除（危险）/取消按钮。"""
	var view_size: Vector2 = size
	var dlg_w: float = 440.0
	var dlg_h: float = 150.0
	var dlg_rect := Rect2((view_size.x - dlg_w) * 0.5, (view_size.y - dlg_h) * 0.5,
		dlg_w, dlg_h)
	_dlg_rect = dlg_rect
	draw_rect(Rect2(Vector2.ZERO, size), Color(0, 0, 0, 0.45))
	draw_rect(dlg_rect, PANEL_COLOR)
	draw_rect(dlg_rect, Color(1, 1, 1, 0.15), false, 1.0)

	var is_prune: bool = str(_dlg_confirm.get("action", "")) == "prune"
	var is_world: bool = str(_dlg_confirm.get("action", "")) == "delete_world"
	var title: String
	var desc: String
	if is_world:
		title = tr("ui.saves.dlg_delete_world_title")
		desc = tr("ui.saves.dlg_delete_world_desc").format({
			"name": str(_dlg_confirm.get("world_name", "")),
		})
	elif is_prune:
		title = tr("ui.saves.dlg_delete_branch_title")
		desc = tr("ui.saves.dlg_delete_branch_desc").format({
			"name": str(_dlg_confirm.get("node_label", "")),
		})
	else:
		title = tr("ui.saves.dlg_delete_node_title")
		desc = tr("ui.saves.dlg_delete_node_desc").format({
			"name": str(_dlg_confirm.get("node_label", "")),
		})
	draw_string(_font, dlg_rect.position + Vector2(16, 26), title,
		HORIZONTAL_ALIGNMENT_LEFT, -1, 16, TITLE_COLOR)
	draw_string(_font, dlg_rect.position + Vector2(16, 56), desc,
		HORIZONTAL_ALIGNMENT_LEFT, -1, 13, DIM_TEXT_COLOR)

	var ok_rect := Rect2(dlg_rect.position.x + dlg_w - 170, dlg_rect.position.y + dlg_h - 42, 70, 28)
	var cancel_rect := Rect2(dlg_rect.position.x + dlg_w - 90, dlg_rect.position.y + dlg_h - 42, 70, 28)
	_draw_button(ok_rect, tr("ui.saves.delete"), _dlg_hover == "ok", true)
	_draw_button(cancel_rect, tr("ui.cancel"), _dlg_hover == "cancel")
	_dlg_rects = {"ok": ok_rect, "cancel": cancel_rect}


func _close_action_panel() -> void:
	"""关闭节点操作面板。"""
	_panel_node_id = ""
	_panel_rects = {}
	_panel_hover = ""
	queue_redraw()


## 行操作按钮分发：进入（读档）、重命名（打开输入框）、复制（导出请求）、删除（打开确认弹窗后发请求）。
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
			_status_text = tr("ui.saves.copying")
			Connection.send(SaveApi.export_request(world_id), _on_simple_refresh)
		"delete":
			_open_delete_world_dialog(row)


# ── 输入对话框 ────────────────────────────────────────────

func _create_new_world() -> void:
	"""新建游戏：进入创建世界调参流程（Issue #8）。

	多步调参（地图生成等）完成后由 world_setup 以 save_create
	创建并进入世界；不再直接默认参数创建。
	"""
	if _busy:
		return
	get_tree().change_scene_to_file(WORLD_SETUP_SCENE)


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
		_set_error(tr("ui.saves.name_empty"))
		return
	if _input_row < 0 or _input_row >= _worlds.size():
		_close_input()
		return
	var world_id: String = str(_worlds[_input_row]["world_id"])
	_busy = true
	_status_text = tr("ui.saves.renaming")
	Connection.send(SaveApi.rename_request(world_id, save_name), _on_simple_refresh)
	_close_input()


# ── 流程 ──────────────────────────────────────────────────

## 发起进入世界（进程切换）：停菜单进程 → 以 --world-id 拉起世界进程，
## 连接就绪后由 _on_connected 切场景进入世界。
##
## Args:
##     world_id: 目标世界 id。
func _load_world(world_id: String) -> void:
	_busy = true
	_status_text = tr("ui.common.entering_world")
	_status_color = STATUS_WAIT_COLOR
	_entering_world = true
	backend_launcher.call(PackedStringArray(["--world-id", world_id]))
	queue_redraw()


func _enter_world() -> void:
	"""切换到主世界场景（世界进程已就绪，world_initialized 事件为就绪信号）。"""
	get_tree().change_scene_to_file(MAIN_WORLD_SCENE)


## 返回主菜单场景。
func _go_back() -> void:
	get_tree().change_scene_to_file(MAIN_MENU_SCENE)


## 语言切换回调（设置界面改动后重绘文案）。
func _on_locale_changed(_locale: String) -> void:
	queue_redraw()
