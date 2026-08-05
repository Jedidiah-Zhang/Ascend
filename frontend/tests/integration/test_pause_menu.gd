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
