extends GutTest

const PAUSE_MENU_SCRIPT: String = "res://scripts/ui/pause_menu.gd"


# ── 场景加载 ────────────────────────────────────────────────

func _make_pause_menu() -> Control:
	"""独立实例化暂停菜单（不挂 main.tscn，避免 main_world 的存档转发干扰）。"""
	var menu: Control = load(PAUSE_MENU_SCRIPT).new()
	autoqfree(menu)
	add_child(menu)
	return menu


func after_each() -> void:
	get_tree().paused = false


func _make_esc_event() -> InputEventKey:
	var ev := InputEventKey.new()
	ev.keycode = KEY_ESCAPE
	ev.pressed = true
	return ev


# ── 打开/关闭与暂停 ────────────────────────────────────────

func test_open_pauses_and_close_resumes() -> void:
	var menu: Control = _make_pause_menu()
	menu.open()
	assert_true(menu.visible, "打开后菜单应可见")
	assert_true(get_tree().paused, "打开时应暂停游戏")

	menu.close()
	assert_false(menu.visible, "关闭后菜单应隐藏")
	assert_false(get_tree().paused, "关闭时应恢复游戏")


func test_open_is_idempotent() -> void:
	var menu: Control = _make_pause_menu()
	menu.open()
	menu.open()
	assert_true(menu.visible)
	menu.close()
	menu.close()
	assert_false(get_tree().paused, "重复关闭不应再次误恢复（无副作用）")


# ── ESC 切换 ───────────────────────────────────────────────

func test_esc_toggles_menu() -> void:
	var menu: Control = _make_pause_menu()
	assert_false(menu.visible)

	menu._input(_make_esc_event())
	assert_true(menu.visible, "ESC 应打开暂停菜单")
	assert_true(get_tree().paused)

	menu._input(_make_esc_event())
	assert_false(menu.visible, "再次 ESC 应关闭暂停菜单")
	assert_false(get_tree().paused, "关闭后应恢复游戏")


# ── ESC 分流（终端打开时归终端） ───────────────────────────

func test_esc_ignored_while_terminal_open() -> void:
	"""终端打开时 ESC 不弹暂停菜单（放行给终端关闭）。"""
	var menu: Control = _make_pause_menu()
	var term: TerminalWidget = autoqfree(TerminalWidget.new())
	add_child(term)
	term.open()
	menu.set_terminal(term)

	menu._input(_make_esc_event())

	assert_false(menu.visible, "终端打开时 ESC 不应弹暂停菜单")
	assert_false(get_tree().paused)


func test_esc_opens_menu_after_terminal_closed() -> void:
	"""终端关闭后 ESC 恢复弹暂停菜单。"""
	var menu: Control = _make_pause_menu()
	var term: TerminalWidget = autoqfree(TerminalWidget.new())
	add_child(term)
	menu.set_terminal(term)

	menu._input(_make_esc_event())
	assert_true(menu.visible, "终端未打开时 ESC 应弹暂停菜单")
	menu.close()


func test_esc_without_terminal_still_toggles() -> void:
	"""未注入终端引用（独立场景）时 ESC 行为不变。"""
	var menu: Control = _make_pause_menu()
	menu._input(_make_esc_event())
	assert_true(menu.visible)
	menu.close()


# ── 按钮动作 ───────────────────────────────────────────────

func test_resume_button_closes_menu() -> void:
	var menu: Control = _make_pause_menu()
	menu.open()
	menu._activate("resume")
	assert_false(menu.visible)
	assert_false(get_tree().paused)


func test_save_button_emits_signal() -> void:
	var menu: Control = _make_pause_menu()
	menu.open()
	watch_signals(menu)
	menu._activate("save")
	assert_signal_emitted(menu, "save_requested", "手动存档应发出 save_requested 信号")
	assert_string_contains(menu._status_text, "正在保存", "应显示存档中状态")


func test_save_button_busy_guard() -> void:
	"""存档请求在途时重复点击不应重复发出。"""
	var menu: Control = _make_pause_menu()
	menu.open()
	watch_signals(menu)
	menu._activate("save")
	menu._activate("save")
	assert_signal_emit_count(menu, "save_requested", 1, "请求在途时不应重复触发")


func test_settings_button_shows_placeholder() -> void:
	var menu: Control = _make_pause_menu()
	menu.open()
	menu._activate("settings")
	assert_string_contains(menu._status_text, "未实现", "设置未实现时应显示占位提示")


func test_show_status_resets_saving() -> void:
	var menu: Control = _make_pause_menu()
	menu.open()
	menu._activate("save")
	assert_true(menu._saving)
	menu.show_status("已保存", false)
	assert_false(menu._saving, "响应回填后应复位存档中状态")
	assert_string_contains(menu._status_text, "已保存")


# ── 保存完成提示（2 秒自动消失） ───────────────────────────

func test_show_save_complete_shows_node_number() -> void:
	"""保存完成应显示「节点 N」并启动自动消失计时。"""
	var menu: Control = _make_pause_menu()
	menu.open()
	menu.show_save_complete(3)
	assert_eq(menu._status_text, "保存完成（节点 3）")
	assert_gt(menu._status_hide_timer, 0.0, "应启动自动消失计时")
	assert_false(menu._saving)


func test_show_save_complete_without_number() -> void:
	"""编号未知（异常路径）时只显示保存完成。"""
	var menu: Control = _make_pause_menu()
	menu.open()
	menu.show_save_complete(0)
	assert_eq(menu._status_text, "保存完成")
	assert_gt(menu._status_hide_timer, 0.0)


func test_save_complete_hides_after_timeout() -> void:
	"""保存完成提示应在 2 秒后自动消失。"""
	var menu: Control = _make_pause_menu()
	menu.open()
	menu.show_save_complete(2)
	menu._process(menu.SAVE_STATUS_HIDE_SECONDS + 0.1)
	assert_eq(menu._status_text, "", "超时后应清空提示")


func test_save_complete_hides_incrementally() -> void:
	"""计时按帧递减，未到 2 秒不清空。"""
	var menu: Control = _make_pause_menu()
	menu.open()
	menu.show_save_complete(2)
	menu._process(1.0)
	assert_eq(menu._status_text, "保存完成（节点 2）", "未到超时不应清空")
	assert_lt(menu._status_hide_timer, menu.SAVE_STATUS_HIDE_SECONDS)
	menu._process(1.1)
	assert_eq(menu._status_text, "")


func test_other_statuses_stay_permanent() -> void:
	"""错误/占位提示不自动消失（仅保存完成带计时）。"""
	var menu: Control = _make_pause_menu()
	menu.open()
	menu.show_status("存档失败：xxx", true)
	assert_eq(menu._status_hide_timer, 0.0)
	menu._process(3.0)
	assert_eq(menu._status_text, "存档失败：xxx", "错误提示应常驻")
