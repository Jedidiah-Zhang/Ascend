extends GutTest

const SAVE_SELECT_SCRIPT: String = "res://scripts/ui/save_select.gd"
const TimelineLayout = preload("res://scripts/ui/timeline_layout.gd")
const Fakes = preload("res://tests/fakes/connection_layers.gd")


func _make_select() -> Control:
	var sel: Control = load(SAVE_SELECT_SCRIPT).new()
	autoqfree(sel)
	add_child(sel)
	return sel


func _payload(worlds: Array, snaps: Array = []) -> Dictionary:
	return {"worlds": worlds, "snapshots": snaps, "current_world_id": ""}


func _setup_timeline() -> Control:
	var sel: Control = _make_select()
	sel._apply_worlds(_payload([
		{"world_id": "w1", "name": "世界A", "game_time": 300, "live_origin": ""},
	], [
		{"world_id": "w1", "file": "snap-a", "parent": "", "game_time": 100, "suffix": "manual"},
		{"world_id": "w1", "file": "snap-b", "parent": "", "game_time": 200, "suffix": "manual"},
	]))
	sel._toggle_timeline(0)
	return sel


func _click(sel: Control, pos: Vector2) -> void:
	var ev := InputEventMouseButton.new()
	ev.button_index = MOUSE_BUTTON_LEFT
	ev.pressed = true
	ev.position = pos
	sel._input(ev)


func _move(sel: Control, pos: Vector2) -> void:
	var ev := InputEventMouseMotion.new()
	ev.position = pos
	sel._input(ev)


func before_each() -> void:
	# 断言中文文案：固定 zh_CN，与用户设置文件 locale 解耦
	TranslationServer.set_locale("zh_CN")
	# 隔离真实网络层：save_select._ready 仅在 CONNECTED 时发真实请求，
	# fake 层 + DISCONNECTED 状态使其不发起（测试数据自给自足）。
	Connection._set_layers(
		Fakes.FakeProcess.new(), Fakes.FakeTransport.new(),
		Fakes.FakeHandshake.new(), Fakes.FakeWorker.new())
	Connection.status = Connection.Status.DISCONNECTED


func test_real_click_flow_sends_rollback_request() -> void:
	"""真实合成点击：点节点选中 → 点面板「进入存档点」→ 以快照参数发起世界进程切换。"""
	var sel: Control = _setup_timeline()
	var launched: Array = []
	sel.backend_launcher = func(args): launched.append(Array(args))
	# 先真实绘制一帧，拿到节点矩形
	sel.queue_redraw()
	await wait_frames(2)
	assert_true(sel._tl_rects.has("snap-a"), "节点矩形应已建立")

	var node_pos: Vector2 = sel._tl_rects["snap-a"].get_center()
	_move(sel, node_pos)
	_click(sel, node_pos)
	assert_eq(sel._panel_node_id, "snap-a", "点击节点应选中弹出面板")
	assert_false(sel._busy, "选中不应发请求")

	# 绘制一帧拿到面板按钮矩形
	sel.queue_redraw()
	await wait_frames(2)
	assert_true(sel._panel_rects.has("enter"), "面板按钮矩形应已建立")
	_click(sel, sel._panel_rects["enter"].get_center())
	assert_true(sel._busy, "「进入存档点」已发出")
	assert_eq(launched, [["--world-id", "w1", "--snapshot", "snap-a"]],
		"回滚 = 以 world_id+snapshot 拉起世界进程")


func test_click_live_node_loads_world() -> void:
	"""真实点击当前时间点 → 以 --world-id 进入世界（无 snapshot）。"""
	var sel: Control = _setup_timeline()
	var launched: Array = []
	sel.backend_launcher = func(args): launched.append(Array(args))
	sel.queue_redraw()
	await wait_frames(2)

	var live_pos: Vector2 = sel._tl_rects[TimelineLayout.LIVE_ID].get_center()
	_move(sel, live_pos)
	_click(sel, live_pos)

	assert_true(sel._busy)
	assert_eq(launched, [["--world-id", "w1"]], "活目录加载不应带 snapshot")
