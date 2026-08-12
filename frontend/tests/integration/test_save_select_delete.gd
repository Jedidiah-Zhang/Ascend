extends GutTest

const SAVE_SELECT_SCRIPT: String = "res://scripts/ui/save_select.gd"
const TimelineLayout = preload("res://scripts/ui/timeline_layout.gd")


func _make_select() -> Control:
	var sel: Control = load(SAVE_SELECT_SCRIPT).new()
	sel.size = Vector2(1280, 720)
	autoqfree(sel)
	add_child(sel)
	# 测试隔离：断开真实后端消息订阅（见 test_save_select.gd 同款注释）
	if Connection.message_received.is_connected(sel._on_message):
		Connection.message_received.disconnect(sel._on_message)
	return sel


func _payload(worlds: Array, snaps: Array = []) -> Dictionary:
	return {"worlds": worlds, "snapshots": snaps, "current_world_id": ""}


## 世界：w1 有 s1（根）→ s2（子）。s1 有后代（删除分支可显示）。
func _setup() -> Control:
	var sel: Control = _make_select()
	sel._apply_worlds(_payload([
		{"world_id": "w1", "name": "世界A", "game_time": 400, "live_origin": ""},
	], [
		{"world_id": "w1", "file": "s1", "parent": "", "game_time": 100, "suffix": "manual"},
		{"world_id": "w1", "file": "s2", "parent": "s1", "game_time": 200, "suffix": "manual"},
	]))
	sel._toggle_timeline(0)
	sel.queue_redraw()
	return sel


func _click(sel: Control, pos: Vector2) -> void:
	var ev := InputEventMouseButton.new()
	ev.button_index = MOUSE_BUTTON_LEFT
	ev.pressed = true
	ev.position = pos
	sel._input(ev)


# ── 选中与面板 ────────────────────────────────────────────

func before_each() -> void:
	# 断言中文文案：固定 zh_CN，与用户设置文件 locale 解耦
	TranslationServer.set_locale("zh_CN")


func test_click_snapshot_opens_action_panel() -> void:
	"""点击快照节点应选中并弹出操作面板（进入存档点 / 删除存档点 / 删除分支）。"""
	var sel: Control = _setup()
	await wait_frames(2)
	assert_true(sel._tl_rects.has("s1"), "节点矩形应已建立")

	_click(sel, sel._tl_rects["s1"].get_center())
	assert_eq(sel._panel_node_id, "s1", "点击节点应选中")
	assert_false(sel._busy, "选中不应发请求")

	await wait_frames(2)
	assert_true(sel._panel_rects.has("enter"), "面板应有「进入存档点」")
	assert_true(sel._panel_rects.has("delete"), "面板应有「删除存档点」")
	assert_true(sel._panel_rects.has("prune"), "有后代节点应有「删除分支」")


func test_panel_actions_prune_only_with_children() -> void:
	"""删除分支仅当节点有后代时显示。"""
	var sel: Control = _setup()
	sel._panel_node_id = "s1"
	var items: Array = sel._panel_actions()
	assert_eq(items.size(), 3, "有后代的节点：进入 + 删除 + 分支")
	assert_eq(items[2]["action"], "prune")
	assert_eq(items[2]["label_key"], "ui.saves.prune_branch")

	sel._panel_node_id = "s2"
	items = sel._panel_actions()
	assert_eq(items.size(), 2, "无后代的节点：进入 + 删除")
	assert_false(items.has({"action": "prune", "label_key": "ui.saves.prune_branch", "danger": true}))


func test_click_other_node_switches_panel() -> void:
	"""点击另一节点 → 面板切换到新节点。"""
	var sel: Control = _setup()
	await wait_frames(2)
	_click(sel, sel._tl_rects["s1"].get_center())
	assert_eq(sel._panel_node_id, "s1")
	_click(sel, sel._tl_rects["s2"].get_center())
	assert_eq(sel._panel_node_id, "s2", "应切换到新节点")
	assert_true(sel._dlg_confirm.is_empty(), "切换节点应无弹窗")


func test_click_selected_node_toggles_panel() -> void:
	"""点击已选中节点 = 关闭面板（再点重新打开）。"""
	var sel: Control = _setup()
	await wait_frames(2)
	_click(sel, sel._tl_rects["s1"].get_center())
	assert_eq(sel._panel_node_id, "s1")
	_click(sel, sel._tl_rects["s1"].get_center())
	assert_eq(sel._panel_node_id, "", "再次点击应关闭面板")
	_click(sel, sel._tl_rects["s1"].get_center())
	assert_eq(sel._panel_node_id, "s1", "第三次点击重新打开")


func test_click_blank_closes_panel() -> void:
	"""树区空白点击应关闭面板（并开始拖拽）。"""
	var sel: Control = _setup()
	await wait_frames(2)
	_click(sel, sel._tl_rects["s1"].get_center())
	assert_eq(sel._panel_node_id, "s1")

	var blank: Vector2 = sel._tl_body_rect.position + Vector2(4, 4)
	_click(sel, blank)
	assert_eq(sel._panel_node_id, "", "空白点击应关闭面板")


func test_escape_closes_panel_before_timeline() -> void:
	"""Esc 优先级：面板 → 时间线 → 输入框 → 返回。"""
	var sel: Control = _setup()
	await wait_frames(2)
	_click(sel, sel._tl_rects["s2"].get_center())
	assert_eq(sel._panel_node_id, "s2")

	var esc := InputEventKey.new()
	esc.keycode = KEY_ESCAPE
	esc.pressed = true
	sel._input(esc)
	assert_eq(sel._panel_node_id, "", "Esc 应优先关闭面板")
	assert_eq(sel._expanded_row, 0, "时间线应保持展开")


# ── 进入存档点 ────────────────────────────────────────────

func test_panel_enter_rolls_back() -> void:
	"""面板「进入存档点」→ 直接回滚（世界进程带 --snapshot）。"""
	var sel: Control = _setup()
	var launched: Array = []
	sel.backend_launcher = func(args): launched.append(Array(args))
	sel._panel_node_id = "s1"
	sel._handle_panel_action("enter")
	assert_true(sel._busy)
	assert_string_contains(sel._status_text, "回滚")
	assert_eq(sel._panel_node_id, "", "执行后面板应关闭")
	assert_eq(launched, [["--world-id", "w1", "--snapshot", "s1"]])


# ── 删除存档点 / 删除分支（确认弹窗） ─────────────────────

func test_panel_delete_opens_confirm_dialog() -> void:
	"""点「删除存档点」→ 弹出确认弹窗（不发请求）。"""
	var sel: Control = _setup()
	sel._panel_node_id = "s1"

	sel._handle_panel_action("delete")
	assert_eq(sel._dlg_confirm.get("action", ""), "delete", "应打开删除确认弹窗")
	assert_eq(sel._dlg_confirm.get("node_id", ""), "s1")
	assert_false(sel._busy, "弹窗打开不应发请求")


func test_panel_prune_opens_confirm_dialog() -> void:
	"""点「删除分支」→ 弹出确认弹窗（不发请求）。"""
	var sel: Control = _setup()
	sel._panel_node_id = "s1"

	sel._handle_panel_action("prune")
	assert_eq(sel._dlg_confirm.get("action", ""), "prune")
	assert_false(sel._busy)


func test_dialog_confirm_sends_delete() -> void:
	"""弹窗「删除」→ 发送单点删除请求。"""
	var sel: Control = _setup()
	sel._panel_node_id = "s1"
	sel._handle_panel_action("delete")
	sel._dlg_rects = {"ok": Rect2(0, 0, 10, 10), "cancel": Rect2(20, 0, 10, 10)}

	sel._handle_click(Vector2(5, 5))
	assert_true(sel._busy, "确定后应发出删除请求")
	assert_string_contains(sel._status_text, "删除节点")
	assert_true(sel._dlg_confirm.is_empty(), "执行后弹窗应关闭")
	assert_eq(sel._panel_node_id, "", "执行后面板应关闭")


func test_dialog_confirm_sends_prune() -> void:
	"""弹窗「删除」→ 发送分支裁剪请求。"""
	var sel: Control = _setup()
	sel._panel_node_id = "s1"
	sel._handle_panel_action("prune")
	sel._dlg_rects = {"ok": Rect2(0, 0, 10, 10), "cancel": Rect2(20, 0, 10, 10)}

	sel._handle_click(Vector2(5, 5))
	assert_true(sel._busy)
	assert_string_contains(sel._status_text, "删除快照")


func test_dialog_cancel_closes_keeps_panel() -> void:
	"""弹窗「取消」→ 关闭弹窗，面板保持选中。"""
	var sel: Control = _setup()
	sel._panel_node_id = "s1"
	sel._handle_panel_action("delete")
	sel._dlg_rects = {"ok": Rect2(0, 0, 10, 10), "cancel": Rect2(20, 0, 10, 10)}

	sel._handle_click(Vector2(25, 5))
	assert_true(sel._dlg_confirm.is_empty(), "取消后弹窗应关闭")
	assert_eq(sel._panel_node_id, "s1", "面板应保持打开")
	assert_false(sel._busy)


func test_dialog_click_outside_cancels() -> void:
	"""点弹窗外任意处 = 取消关闭弹窗。"""
	var sel: Control = _setup()
	sel._panel_node_id = "s1"
	sel._handle_panel_action("delete")
	sel._dlg_rect = Rect2(100, 100, 200, 100)
	sel._dlg_rects = {"ok": Rect2(0, 0, 10, 10), "cancel": Rect2(20, 0, 10, 10)}

	sel._handle_click(Vector2(50, 300))
	assert_true(sel._dlg_confirm.is_empty(), "点弹窗外应关闭弹窗")


func test_dialog_escape_closes() -> void:
	"""弹窗打开时 ESC → 先关弹窗。"""
	var sel: Control = _setup()
	sel._panel_node_id = "s1"
	sel._handle_panel_action("delete")

	var esc := InputEventKey.new()
	esc.keycode = KEY_ESCAPE
	esc.pressed = true
	sel._input(esc)
	assert_true(sel._dlg_confirm.is_empty(), "ESC 应先关弹窗")
	assert_eq(sel._panel_node_id, "s1", "面板保持打开")


func test_delete_on_leaf_node_single_point() -> void:
	"""叶子节点只有「删除存档点」（无删除分支），弹窗为单点删除。"""
	var sel: Control = _setup()
	sel._panel_node_id = "s2"
	assert_eq(sel._panel_actions().size(), 2)
	sel._handle_panel_action("delete")
	assert_eq(sel._dlg_confirm.get("action", ""), "delete")
	sel._dlg_rects = {"ok": Rect2(0, 0, 10, 10), "cancel": Rect2(20, 0, 10, 10)}
	sel._handle_click(Vector2(5, 5))
	assert_true(sel._busy)
	assert_string_contains(sel._status_text, "删除节点")


func test_panel_click_via_real_click_flow() -> void:
	"""真实合成点击全流程：节点 → 面板删除按钮 → 弹窗确定 → 删除请求（busy）。"""
	var sel: Control = _setup()
	await wait_frames(2)
	_click(sel, sel._tl_rects["s1"].get_center())
	await wait_frames(2)
	assert_true(sel._panel_rects.has("delete"))

	_click(sel, sel._panel_rects["delete"].get_center())
	assert_false(sel._dlg_confirm.is_empty(), "面板删除项应打开确认弹窗")
	await wait_frames(2)
	assert_true(sel._dlg_rects.has("ok"), "弹窗按钮矩形应已建立")

	_click(sel, sel._dlg_rects["ok"].get_center())
	assert_true(sel._busy, "弹窗确定后删除请求应已发出")


# ── 刷新保持时间线展开 ────────────────────────────────────

func test_refresh_keeps_timeline_expanded() -> void:
	"""删除节点后 save_list 刷新不应收起时间线（重建展开视图）。"""
	var sel: Control = _make_select()
	sel._apply_worlds(_payload([
		{"world_id": "w1", "name": "世界A", "game_time": 400, "live_origin": ""},
	], [
		{"world_id": "w1", "file": "s1", "parent": "", "game_time": 100, "suffix": "manual"},
		{"world_id": "w1", "file": "s2", "parent": "s1", "game_time": 200, "suffix": "manual"},
	]))
	sel._toggle_timeline(0)
	assert_eq(sel._expanded_row, 0)
	assert_eq(sel._tl_nodes.size(), 3)

	# 模拟删除 s2 后的 save_list 响应（数据已更新）
	sel._apply_worlds(_payload([
		{"world_id": "w1", "name": "世界A", "game_time": 400, "live_origin": ""},
	], [
		{"world_id": "w1", "file": "s1", "parent": "", "game_time": 100, "suffix": "manual"},
	]))
	assert_eq(sel._expanded_row, 0, "刷新后时间线应保持展开")
	assert_eq(sel._tl_nodes.size(), 2, "树数据应重建为剩余快照")
	var ids: Array = []
	for n in sel._tl_nodes:
		ids.append(n["id"])
	assert_false(ids.has("s2"), "已删除节点不应在树中")


func test_refresh_collapses_when_world_deleted() -> void:
	"""世界被删除时刷新应收起时间线（展开行已不存在）。"""
	var sel: Control = _make_select()
	sel._apply_worlds(_payload([
		{"world_id": "w1", "name": "世界A", "game_time": 400, "live_origin": ""},
	], [
		{"world_id": "w1", "file": "s1", "parent": "", "game_time": 100, "suffix": "manual"},
	]))
	sel._toggle_timeline(0)
	assert_eq(sel._expanded_row, 0)

	sel._apply_worlds(_payload([]))
	assert_eq(sel._expanded_row, -1, "展开行不存在应收起")


# ── 存档（世界）删除弹窗 ──────────────────────────────────

func test_world_delete_opens_confirm_dialog() -> void:
	"""行「删除」按钮 → 打开存档删除确认弹窗（不发请求）。"""
	var sel: Control = _setup()
	sel._activate_action(0, 3)
	assert_eq(sel._dlg_confirm.get("action", ""), "delete_world", "应打开存档删除弹窗")
	assert_eq(sel._dlg_confirm.get("world_name", ""), "世界A")
	assert_false(sel._busy, "弹窗打开不应发请求")


func test_world_delete_dialog_confirm_sends_request() -> void:
	"""弹窗「删除」→ 发送世界删除请求。"""
	var sel: Control = _setup()
	sel._activate_action(0, 3)
	sel._dlg_rects = {"ok": Rect2(0, 0, 10, 10), "cancel": Rect2(20, 0, 10, 10)}
	sel._handle_click(Vector2(5, 5))
	assert_true(sel._busy, "确定后应发出删除请求")
	assert_string_contains(sel._status_text, "删除")
	assert_true(sel._dlg_confirm.is_empty(), "执行后弹窗应关闭")


func test_world_delete_dialog_cancel() -> void:
	"""存档删除弹窗「取消」→ 关闭，不发请求。"""
	var sel: Control = _setup()
	sel._activate_action(0, 3)
	sel._dlg_rects = {"ok": Rect2(0, 0, 10, 10), "cancel": Rect2(20, 0, 10, 10)}
	sel._handle_click(Vector2(25, 5))
	assert_true(sel._dlg_confirm.is_empty(), "取消后弹窗应关闭")
	assert_false(sel._busy, "不应发请求")


func test_world_delete_dialog_outside_click_cancels() -> void:
	"""存档删除弹窗点外部 = 取消。"""
	var sel: Control = _setup()
	sel._activate_action(0, 3)
	sel._dlg_rect = Rect2(100, 100, 200, 100)
	sel._dlg_rects = {"ok": Rect2(0, 0, 10, 10), "cancel": Rect2(20, 0, 10, 10)}
	sel._handle_click(Vector2(50, 300))
	assert_true(sel._dlg_confirm.is_empty(), "点弹窗外应关闭")
