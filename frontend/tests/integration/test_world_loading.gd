extends GutTest

const WorldLoading = preload("res://scripts/ui/world_loading.gd")


## 测试专用子类：覆写切场景钩子，避免真实 change_scene_to_file
## 释放当前场景打断 GUT 运行；记录调用并校验切场景时节点仍有效。
class TestLoading extends WorldLoading:
	var change_scene_calls: int = 0
	var viewport_valid_at_switch: bool = false

	func _change_to_menu_scene() -> void:
		change_scene_calls += 1
		# 原 bug：get_viewport() 在 change_scene_to_file 之后被调用时
		# 返回 null（节点已出树）→ set_input_as_handled 崩溃。正确顺序
		# 是 set_input_as_handled 先执行（见 _leave_to_main_menu），
		# 故本钩子被调时节点必仍在树中、viewport 必有效。
		viewport_valid_at_switch = is_inside_tree() and get_viewport() != null


func _make_loading() -> TestLoading:
	var loading := TestLoading.new()
	add_child_autofree(loading)
	return loading


func _set_error_with_buttons(loading: TestLoading) -> void:
	loading._state = WorldLoading.State.ERROR
	loading._retry_rect = Rect2(0, 0, 100, 50)
	loading._menu_rect = Rect2(200, 0, 100, 50)


func _click(loading: TestLoading, at: Vector2) -> void:
	var ev := InputEventMouseButton.new()
	ev.button_index = MOUSE_BUTTON_LEFT
	ev.pressed = true
	ev.position = at
	loading._input(ev)


func test_menu_click_error_state_leaves_to_menu() -> void:
	"""ERROR 态点回主菜单：触发离开路径，且切场景时节点仍在树中。

	回归：change_scene_to_file 释放本节点后 get_viewport() 返回 null，
	set_input_as_handled 崩溃（E）。修复后必须先标记输入再切场景。
	"""
	var loading := _make_loading()
	_set_error_with_buttons(loading)
	_click(loading, Vector2(250, 25))  # 落在 menu 按钮内
	assert_eq(loading.change_scene_calls, 1, "应调用一次切场景钩子")
	assert_true(loading.viewport_valid_at_switch,
		"切场景钩子被调时节点应在树中且 viewport 有效（不崩溃）")


func test_retry_click_error_state_calls_retry() -> void:
	"""ERROR 态点重试：回到 LAUNCHING，不切场景。"""
	var loading := _make_loading()
	_set_error_with_buttons(loading)
	_click(loading, Vector2(50, 25))  # 落在 retry 按钮内
	assert_eq(loading.change_scene_calls, 0, "重试不应切场景")
	assert_eq(loading._state, WorldLoading.State.LAUNCHING, "重试应回到启动态")


func test_click_ignored_outside_error_buttons() -> void:
	"""ERROR 态点击按钮外区域：无任何动作。"""
	var loading := _make_loading()
	_set_error_with_buttons(loading)
	_click(loading, Vector2(150, 25))  # 两按钮之间
	assert_eq(loading.change_scene_calls, 0, "按钮外不应切场景")
	assert_eq(loading._state, WorldLoading.State.ERROR, "按钮外不应改变状态")


func test_click_ignored_when_not_error() -> void:
	"""非 ERROR 态（LAUNCHING/LOADING）点击按钮：全部忽略。"""
	for state in [WorldLoading.State.LAUNCHING, WorldLoading.State.LOADING]:
		var loading := _make_loading()
		_set_error_with_buttons(loading)
		loading._state = state
		_click(loading, Vector2(250, 25))
		_click(loading, Vector2(50, 25))
		assert_eq(loading.change_scene_calls, 0,
			"非 ERROR 态不应切场景（state=%d）" % state)
		assert_eq(loading._state, state, "非 ERROR 态状态不应改变")
