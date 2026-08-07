extends GutTest

const SAVE_SELECT_SCRIPT: String = "res://scripts/ui/save_select.gd"
const TimelineLayout = preload("res://scripts/ui/timeline_layout.gd")


func _make_select() -> Control:
	var sel: Control = load(SAVE_SELECT_SCRIPT).new()
	sel.size = Vector2(1280, 720)
	autoqfree(sel)
	add_child(sel)
	# 测试隔离：断开真实后端消息订阅——后端就绪握手会触发 _on_connected
	# 刷新，真实 save_list 空响应会覆盖测试注入的世界数据（时间线测试失败）
	if Connection.message_received.is_connected(sel._on_message):
		Connection.message_received.disconnect(sel._on_message)
	return sel


func _payload(worlds: Array, snaps: Array = [], current: String = "") -> Dictionary:
	return {"worlds": worlds, "snapshots": snaps,
		"current_world_id": current}


# ── 时间线打开/关闭 ───────────────────────────────────────

func test_apply_worlds_stores_lineage_data() -> void:
	var sel: Control = _make_select()
	sel._apply_worlds(_payload([
		{"world_id": "w1", "name": "世界A", "game_time": 500, "live_origin": "s2"},
	], [
		{"world_id": "w1", "file": "s1", "parent": "", "game_time": 100, "suffix": "manual"},
		{"world_id": "w1", "file": "s2", "parent": "", "game_time": 200, "suffix": "manual"},
	], "w1"))
	assert_eq(sel._snapshots.size(), 2)
	assert_eq(sel._current_world_id, "w1")
	assert_eq(sel._worlds[0]["live_origin"], "s2")


func test_toggle_timeline_builds_fork() -> void:
	var sel: Control = _make_select()
	sel._apply_worlds(_payload([
		{"world_id": "w1", "name": "世界A", "game_time": 500, "live_origin": "s1"},
	], [
		{"world_id": "w1", "file": "s1", "parent": "", "game_time": 100, "suffix": "manual"},
		{"world_id": "w1", "file": "s2", "parent": "", "game_time": 200, "suffix": "manual"},
	]))

	sel._toggle_timeline(0)

	assert_eq(sel._expanded_row, 0, "时间线应展开")
	assert_eq(sel._tl_nodes.size(), 3, "两个快照 + 当前时间点")
	assert_eq(sel._tl_edges.size(), 2)


func test_close_timeline_returns_to_list() -> void:
	var sel: Control = _make_select()
	sel._apply_worlds(_payload([
		{"world_id": "w1", "name": "世界A", "game_time": 100},
	]))
	sel._toggle_timeline(0)
	sel._close_timeline()
	assert_eq(sel._expanded_row, -1, "应回到列表模式")


func test_timeline_filters_snapshots_by_world() -> void:
	"""时间线只显示该世界的快照（多世界数据不串）。"""
	var sel: Control = _make_select()
	sel._apply_worlds(_payload([
		{"world_id": "w1", "name": "世界A", "game_time": 100},
	], [
		{"world_id": "w1", "file": "s1", "parent": "", "game_time": 50, "suffix": "manual"},
		{"world_id": "w2", "file": "x1", "parent": "", "game_time": 60, "suffix": "manual"},
	]))
	sel._toggle_timeline(0)
	assert_eq(sel._tl_nodes.size(), 2, "仅 w1 的快照 + 当前点")


func test_timeline_includes_auto_protection_nodes() -> void:
	"""跳转分支后旧分支的自动保护点应可见且可回滚（不消失）。"""
	var sel: Control = _make_select()
	sel._apply_worlds(_payload([
		{"world_id": "w1", "name": "世界A", "game_time": 300, "live_origin": "s1"},
	], [
		{"world_id": "w1", "file": "s1", "parent": "", "game_time": 100, "suffix": "manual"},
		{"world_id": "w1", "file": "s2", "parent": "", "game_time": 200, "suffix": "manual"},
		{"world_id": "w1", "file": "a1", "parent": "s2", "game_time": 250, "suffix": "auto"},
	]))
	sel._toggle_timeline(0)
	var ids: Array = []
	var auto_ids: Array = []
	for n in sel._tl_nodes:
		ids.append(n["id"])
		if n["suffix"] == "auto":
			auto_ids.append(n["id"])
	assert_true(ids.has("a1"), "自动保护点应参与时间线（分支延续）")
	assert_eq(auto_ids, ["a1"])

	# 自动节点同样可回滚（两次点击确认）
	var launched: Array = []
	sel.backend_launcher = func(args): launched.append(Array(args))
	sel._activate_timeline_node("a1")
	assert_eq(sel._tl_confirm_id, "a1")
	sel._activate_timeline_node("a1")
	assert_true(sel._busy, "自动节点应可回滚")
	assert_string_contains(sel._status_text, "回滚")
	assert_eq(launched, [["--world-id", "w1", "--snapshot", "a1"]])


func test_draw_timeline_does_not_crash() -> void:
	"""分叉/回滚确认态/悬停态下绘制不应报错（渲染冒烟）。"""
	var sel: Control = _make_select()
	sel._apply_worlds(_payload([
		{"world_id": "w1", "name": "世界A", "game_time": 500, "live_origin": "s1"},
	], [
		{"world_id": "w1", "file": "s1", "parent": "", "game_time": 100, "suffix": "manual"},
		{"world_id": "w1", "file": "s2", "parent": "", "game_time": 200, "suffix": "manual"},
	]))
	sel._toggle_timeline(0)
	sel._tl_hover_id = "s2"
	sel._tl_confirm_id = "s1"
	sel.queue_redraw()
	await wait_frames(2)
	sel._close_timeline()
	sel.queue_redraw()
	await wait_frames(1)
	pass_test("时间线各状态绘制无崩溃")


# ── 回滚交互 ──────────────────────────────────────────────

func test_snapshot_click_needs_confirmation_twice() -> void:
	"""回滚须两次点击确认（防误触），第二次发起世界进程切换（带 --snapshot）。"""
	var sel: Control = _make_select()
	var launched: Array = []
	sel.backend_launcher = func(args): launched.append(Array(args))
	sel._apply_worlds(_payload([
		{"world_id": "w1", "name": "世界A", "game_time": 300, "live_origin": ""},
	], [
		{"world_id": "w1", "file": "s1", "parent": "", "game_time": 100, "suffix": "manual"},
	]))
	sel._toggle_timeline(0)

	sel._activate_timeline_node("s1")
	assert_eq(sel._tl_confirm_id, "s1", "第一次点击进入待确认")
	assert_false(sel._busy, "第一次点击不应发请求")

	sel._activate_timeline_node("s1")
	assert_true(sel._busy, "第二次点击应发出回滚请求")
	assert_true(sel._entering_world, "应进入世界切换流程")
	assert_string_contains(sel._status_text, "回滚")
	assert_eq(launched, [["--world-id", "w1", "--snapshot", "s1"]],
		"回滚 = 以快照参数拉起世界进程")


func test_live_node_click_loads_world() -> void:
	"""点击当前时间点 = 以 --world-id 拉起世界进程（同「进入游戏」）。"""
	var sel: Control = _make_select()
	var launched: Array = []
	sel.backend_launcher = func(args): launched.append(Array(args))
	sel._apply_worlds(_payload([
		{"world_id": "w1", "name": "世界A", "game_time": 100},
	]))
	sel._toggle_timeline(0)
	sel._activate_timeline_node(TimelineLayout.LIVE_ID)
	assert_true(sel._busy, "点击当前点应进入世界切换流程")
	assert_eq(launched, [["--world-id", "w1"]], "应拉起世界进程")


# ── 行内展开/收起 ──────────────────────────────────────────

func test_row_click_toggles_expansion() -> void:
	"""点击行主体展开时间线，再次点击收起；下方行随之让位。"""
	var sel: Control = _make_select()
	sel._apply_worlds(_payload([
		{"world_id": "w1", "name": "世界A", "game_time": 500, "live_origin": "s1"},
		{"world_id": "w2", "name": "世界B", "game_time": 100},
	], [
		{"world_id": "w1", "file": "s1", "parent": "", "game_time": 100, "suffix": "manual"},
		{"world_id": "w1", "file": "s2", "parent": "", "game_time": 200, "suffix": "manual"},
	]))
	# 行 0 主体点击（避开右侧操作按钮；行从 HEADER_H 起算）
	var ev := InputEventMouseButton.new()
	ev.button_index = MOUSE_BUTTON_LEFT
	ev.pressed = true
	ev.position = Vector2(60, sel.HEADER_H + 20)
	sel._input(ev)
	assert_eq(sel._expanded_row, 0, "点击行应展开时间线")

	# 展开后下方行下移
	var y0: float = sel._row_display_y(0, sel.HEADER_H)
	var y1: float = sel._row_display_y(1, sel.HEADER_H)
	assert_eq(y1 - y0, sel.ROW_H + sel.ROW_GAP + sel.TL_INLINE_H + sel.TL_GAP,
		"展开行下方的行应整体下移")

	# 再次点击收起
	sel._input(ev)
	assert_eq(sel._expanded_row, -1, "再次点击行应收起")


func test_expanding_second_row_collapses_first() -> void:
	"""展开另一行时原展开行收起（同一时间只展开一行）。"""
	var sel: Control = _make_select()
	sel._apply_worlds(_payload([
		{"world_id": "w1", "name": "世界A", "game_time": 100},
		{"world_id": "w2", "name": "世界B", "game_time": 100},
	]))
	var ev := InputEventMouseButton.new()
	ev.button_index = MOUSE_BUTTON_LEFT
	ev.pressed = true
	ev.position = Vector2(60, sel.HEADER_H + 20)
	sel._input(ev)
	assert_eq(sel._expanded_row, 0)
	# 行 1 已被展开行下移 TL_INLINE_H + TL_GAP
	ev.position = Vector2(60, sel.HEADER_H + 20 + sel.ROW_H + sel.ROW_GAP \
		+ sel.TL_INLINE_H + sel.TL_GAP)
	sel._input(ev)
	assert_eq(sel._expanded_row, 1, "应切换到第二行")


# ── 编号节点 + 图例（标签不重叠的表现形式） ───────────────

func test_node_numbering_follows_save_order() -> void:
	"""编号按保存顺序递增（saved_at），而非视觉位置或游戏时间。

	回归场景：回滚后游戏时间倒退（a1 的 time=150 小于 s2 的 200），
	但 a1 保存于 s2 之前——编号必须反映真实保存顺序。
	"""
	var sel: Control = _make_select()
	sel._apply_worlds(_payload([
		{"world_id": "w1", "name": "世界A", "game_time": 400, "live_origin": "s2"},
	], [
		{"world_id": "w1", "file": "s1", "parent": "", "game_time": 100,
			"saved_at": 1000.0, "suffix": "manual"},
		{"world_id": "w1", "file": "s2", "parent": "", "game_time": 200,
			"saved_at": 3000.0, "suffix": "manual"},
		{"world_id": "w1", "file": "a1", "parent": "s1", "game_time": 150,
			"saved_at": 2000.0, "suffix": "auto"},
	]))
	sel._toggle_timeline(0)
	sel.queue_redraw()
	await wait_frames(2)
	assert_eq(sel._tl_numbers["s1"], 1, "最早保存")
	assert_eq(sel._tl_numbers["a1"], 2, "按保存顺序而非游戏时间（150 < 200 仍排第二）")
	assert_eq(sel._tl_numbers["s2"], 3)
	assert_false(sel._tl_numbers.has(TimelineLayout.LIVE_ID), "当前点用星标不占编号")


func test_legend_rows_built_and_clickable() -> void:
	"""图例含全部节点（当前点置顶）；点击图例行 = 点击节点。"""
	var sel: Control = _make_select()
	sel._apply_worlds(_payload([
		{"world_id": "w1", "name": "世界A", "game_time": 300, "live_origin": "s1"},
	], [
		{"world_id": "w1", "file": "s1", "parent": "", "game_time": 100, "suffix": "manual"},
		{"world_id": "w1", "file": "s2", "parent": "s1", "game_time": 200, "suffix": "manual"},
	]))
	sel._toggle_timeline(0)
	sel.queue_redraw()
	await wait_frames(2)
	assert_eq(sel._tl_legend_rects.size(), 3, "图例 = 当前点 + 2 快照")

	# 点击图例行 s2 → 进入确认（同节点点击）
	sel.backend_launcher = func(_args): pass
	sel._handle_click(sel._tl_legend_rects["s2"].get_center())
	assert_eq(sel._tl_confirm_id, "s2", "图例点击应选中节点")
	sel._handle_click(sel._tl_legend_rects["s2"].get_center())
	assert_true(sel._busy, "图例再次点击应发出回滚")


# ── 拖拽平移 + 滚轮缩放（长树） ───────────────────────────

func test_drag_pans_tree() -> void:
	"""树区空白处拖拽应平移视口。"""
	var sel: Control = _make_select()
	sel._apply_worlds(_payload([
		{"world_id": "w1", "name": "世界A", "game_time": 300, "live_origin": "s1"},
	], [
		{"world_id": "w1", "file": "s1", "parent": "", "game_time": 100, "suffix": "manual"},
		{"world_id": "w1", "file": "s2", "parent": "", "game_time": 200, "suffix": "manual"},
	]))
	sel._toggle_timeline(0)
	sel.queue_redraw()
	await wait_frames(2)
	assert_true(sel._tl_body_rect.size.x > 0, "树视口应已布局")

	# 找树区空白点（避开节点与图例）
	var grab: Vector2 = sel._tl_body_rect.position + Vector2(6, 6)
	var grab_ev := InputEventMouseButton.new()
	grab_ev.button_index = MOUSE_BUTTON_LEFT
	grab_ev.pressed = true
	grab_ev.position = grab
	sel._input(grab_ev)
	assert_true(sel._tl_dragging, "空白处按下应进入拖拽")

	var pan_before: Vector2 = sel._tl_pan
	var move_ev := InputEventMouseMotion.new()
	move_ev.position = grab + Vector2(50, 30)
	sel._input(move_ev)
	assert_eq(sel._tl_pan, pan_before + Vector2(50, 30), "拖拽应平移视口")

	var release_ev := InputEventMouseButton.new()
	release_ev.button_index = MOUSE_BUTTON_LEFT
	release_ev.pressed = false
	release_ev.position = grab + Vector2(50, 30)
	sel._input(release_ev)
	assert_false(sel._tl_dragging, "释放后应结束拖拽")


func test_zoom_fits_and_clamps() -> void:
	"""展开时自动适配缩放；滚轮缩放有上下限。"""
	var sel: Control = _make_select()
	var snaps: Array = []
	# 长链：20 个快照，超出视口高度
	for i in range(20):
		snaps.append({"world_id": "w1", "file": "s%d" % i,
			"parent": "" if i == 0 else "s%d" % (i - 1),
			"game_time": (i + 1) * 100, "suffix": "manual"})
	sel._apply_worlds(_payload([
		{"world_id": "w1", "name": "世界A", "game_time": 2100, "live_origin": "s19"},
	], snaps))
	sel._toggle_timeline(0)
	sel.queue_redraw()
	await wait_frames(2)
	assert_lt(sel._tl_zoom, 1.0, "长树展开时应自动缩小适配")

	# 滚轮放大
	var wheel := InputEventMouseButton.new()
	wheel.button_index = MOUSE_BUTTON_WHEEL_UP
	wheel.pressed = true
	wheel.position = sel._tl_body_rect.get_center()
	sel._input(wheel)
	assert_gt(sel._tl_zoom, 0.35, "滚轮应可放大")
	# 反复放大到上限
	for i in range(30):
		sel._input(wheel)
	assert_eq(sel._tl_zoom, sel.TL_ZOOM_MAX, "放大应钳制在上限")
	# 反复缩小到下限
	var wheel_down := InputEventMouseButton.new()
	wheel_down.button_index = MOUSE_BUTTON_WHEEL_DOWN
	wheel_down.pressed = true
	wheel_down.position = sel._tl_body_rect.get_center()
	for i in range(40):
		sel._input(wheel_down)
	assert_eq(sel._tl_zoom, sel.TL_ZOOM_MIN, "缩小应钳制在下限")


func test_wheel_over_legend_scrolls_list() -> void:
	"""图例区滚轮滚动图例列表（不缩放树）。"""
	var sel: Control = _make_select()
	var snaps: Array = []
	for i in range(30):
		snaps.append({"world_id": "w1", "file": "s%d" % i,
			"parent": "" if i == 0 else "s%d" % (i - 1),
			"game_time": (i + 1) * 100, "suffix": "manual"})
	sel._apply_worlds(_payload([
		{"world_id": "w1", "name": "世界A", "game_time": 3100, "live_origin": "s29"},
	], snaps))
	sel._toggle_timeline(0)
	sel.queue_redraw()
	await wait_frames(2)
	assert_gt(sel._tl_legend_rects.size(), 0)

	var zoom_before: float = sel._tl_zoom
	var wheel := InputEventMouseButton.new()
	wheel.button_index = MOUSE_BUTTON_WHEEL_DOWN
	wheel.pressed = true
	wheel.position = sel._tl_legend_rect.position + Vector2(20, 60)
	sel._input(wheel)
	assert_eq(sel._tl_zoom, zoom_before, "图例区滚轮不应改变树缩放")
	assert_gt(sel._tl_legend_scroll, 0.0, "图例应向下滚动")
