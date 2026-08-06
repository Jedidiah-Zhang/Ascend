extends GutTest

const SAVE_SELECT_SCRIPT: String = "res://scripts/ui/save_select.gd"
const TimelineLayout = preload("res://scripts/ui/timeline_layout.gd")
const Config = preload("res://scripts/config.gd")


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


func test_real_click_flow_sends_rollback_request() -> void:
	"""真实合成点击：点节点两次 → 应发出带 world_id+snapshot 的 save_load。"""
	Connection._hello_acked = true  # 白盒：模拟握手完成，放行消息入队
	var sel: Control = _setup_timeline()
	# 先真实绘制一帧，拿到节点矩形
	sel.queue_redraw()
	await wait_frames(2)
	assert_true(sel._tl_rects.has("snap-a"), "节点矩形应已建立")

	var node_pos: Vector2 = sel._tl_rects["snap-a"].get_center()
	_move(sel, node_pos)
	_click(sel, node_pos)
	assert_eq(sel._tl_confirm_id, "snap-a", "第一次点击进入待确认")

	var queue_size_before: int = Connection._send_queue.size()
	_click(sel, node_pos)
	assert_eq(sel._tl_confirm_id, "", "确认后应复位")
	assert_true(sel._busy, "回滚请求已发出")
	assert_eq(Connection._send_queue.size(), queue_size_before + 1, "应入队一条请求")

	# 解码请求体验证内容
	var framed: PackedByteArray = Connection._send_queue[queue_size_before]
	var decoded: Dictionary = Connection._codec.frame_decode(framed)
	assert_eq(decoded["bodies"].size(), 1)
	var msg: Dictionary = JsonCodec.decode(decoded["bodies"][0])
	assert_eq(msg["type"], "request")
	assert_eq(msg["request_type"], "save_load")
	assert_eq(msg["payload"]["world_id"], "w1")
	assert_eq(msg["payload"]["snapshot"], "snap-a", "回滚应携带目标快照")


func test_click_live_node_loads_world() -> void:
	"""真实点击当前时间点 → 加载活目录（无 snapshot 字段）。"""
	Connection._hello_acked = true  # 白盒：模拟握手完成，放行消息入队
	var sel: Control = _setup_timeline()
	sel.queue_redraw()
	await wait_frames(2)

	var live_pos: Vector2 = sel._tl_rects[TimelineLayout.LIVE_ID].get_center()
	_move(sel, live_pos)
	_click(sel, live_pos)

	assert_true(sel._busy)
	var framed: PackedByteArray = Connection._send_queue[Connection._send_queue.size() - 1]
	var decoded: Dictionary = Connection._codec.frame_decode(framed)
	var msg: Dictionary = JsonCodec.decode(decoded["bodies"][0])
	assert_eq(msg["request_type"], "save_load")
	assert_eq(msg["payload"]["snapshot"], "", "活目录加载不应带 snapshot")
