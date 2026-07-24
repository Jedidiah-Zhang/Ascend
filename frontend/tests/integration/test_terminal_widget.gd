extends GutTest

const Config = preload("res://scripts/config.gd")


func _make_key_event(keycode: Key, pressed: bool = true, echo: bool = false) -> InputEventKey:
	var ev := InputEventKey.new()
	ev.keycode = keycode
	ev.pressed = pressed
	ev.echo = echo
	return ev


func _make_text_event(char_code: int) -> InputEventKey:
	var ev := InputEventKey.new()
	ev.keycode = KEY_NONE
	ev.pressed = true
	ev.echo = false
	ev.unicode = char_code
	return ev


func _create_widget() -> TerminalWidget:
	var w: TerminalWidget = autoqfree(TerminalWidget.new())
	add_child(w)
	return w


func _type_text(widget: TerminalWidget, text: String) -> void:
	for ch in text:
		widget._input(_make_text_event(ch.unicode_at(0)))


func _press_enter(widget: TerminalWidget) -> void:
	widget._input(_make_key_event(KEY_ENTER))


# ── 打开/关闭 ────────────────────────────────────────────────

func test_slash_toggles_open() -> void:
	var w: TerminalWidget = _create_widget()
	assert_false(w.is_open())
	w._input(_make_key_event(KEY_SLASH))
	assert_true(w.is_open())


func test_slash_toggles_close() -> void:
	var w: TerminalWidget = _create_widget()
	w._input(_make_key_event(KEY_SLASH))
	assert_true(w.is_open())
	w._input(_make_key_event(KEY_SLASH))
	assert_false(w.is_open())


func test_toggle_method() -> void:
	var w: TerminalWidget = _create_widget()
	assert_false(w.is_open())
	w.toggle()
	assert_true(w.is_open())
	w.toggle()
	assert_false(w.is_open())


# ── 本地指令 ────────────────────────────────────────────────

func test_help_command_shows_local_help() -> void:
	var w: TerminalWidget = _create_widget()
	var signal_count := 0
	w.remote_command_submitted.connect(func(_cmd: String): signal_count += 1)

	w._input(_make_key_event(KEY_SLASH))
	_type_text(w, "help")
	_press_enter(w)

	assert_gt(w._output_lines.size(), 0, "help 应产生输出")
	var found := false
	for line in w._output_lines:
		if "clear" in line:
			found = true
			break
	assert_true(found, "help 应列出 clear 指令")


func test_clear_command_clears_output() -> void:
	var w: TerminalWidget = _create_widget()
	w._input(_make_key_event(KEY_SLASH))
	_type_text(w, "test output")
	_press_enter(w)
	assert_gt(w._output_lines.size(), 0, "应有输出行")

	_type_text(w, "clear")
	_press_enter(w)
	assert_eq(w._output_lines.size(), 0, "clear 应清空所有输出")


func test_unknown_command_emits_remote_signal() -> void:
	var w: TerminalWidget = _create_widget()
	var results: Array = [""]
	w.remote_command_submitted.connect(func(cmd: String): results[0] = cmd)

	w._input(_make_key_event(KEY_SLASH))
	_type_text(w, "some_remote_cmd arg1 arg2")
	_press_enter(w)

	assert_eq(results[0], "some_remote_cmd arg1 arg2")


# ── 历史导航 ────────────────────────────────────────────────

func test_history_up_recalls_previous_command() -> void:
	var w: TerminalWidget = _create_widget()
	w._input(_make_key_event(KEY_SLASH))

	_type_text(w, "first")
	_press_enter(w)
	_type_text(w, "second")
	_press_enter(w)

	w._input(_make_key_event(KEY_UP))
	assert_eq(w._input_text, "second", "上翻应召回上一条指令")

	w._input(_make_key_event(KEY_UP))
	assert_eq(w._input_text, "first", "再次上翻应召回更早的指令")


func test_history_down_returns_to_empty() -> void:
	var w: TerminalWidget = _create_widget()
	w._input(_make_key_event(KEY_SLASH))

	_type_text(w, "cmd1")
	_press_enter(w)

	w._input(_make_key_event(KEY_UP))
	assert_eq(w._input_text, "cmd1")
	w._input(_make_key_event(KEY_DOWN))
	assert_eq(w._input_text, "", "下翻到底应为空")


# ── 文本编辑 ────────────────────────────────────────────────

func test_backspace_deletes_character() -> void:
	var w: TerminalWidget = _create_widget()
	w._input(_make_key_event(KEY_SLASH))
	_type_text(w, "abc")
	w._input(_make_key_event(KEY_BACKSPACE))
	assert_eq(w._input_text, "ab")


func test_delete_removes_character_at_cursor() -> void:
	var w: TerminalWidget = _create_widget()
	w._input(_make_key_event(KEY_SLASH))
	_type_text(w, "abc")
	w._input(_make_key_event(KEY_LEFT))
	w._input(_make_key_event(KEY_DELETE))
	assert_eq(w._input_text, "ab")


func test_cursor_left_right_navigation() -> void:
	var w: TerminalWidget = _create_widget()
	w._input(_make_key_event(KEY_SLASH))
	_type_text(w, "ab")
	assert_eq(w._cursor_pos, 2)
	w._input(_make_key_event(KEY_LEFT))
	assert_eq(w._cursor_pos, 1)
	w._input(_make_key_event(KEY_RIGHT))
	assert_eq(w._cursor_pos, 2)


func test_home_and_end_keys() -> void:
	var w: TerminalWidget = _create_widget()
	w._input(_make_key_event(KEY_SLASH))
	_type_text(w, "abc")
	w._input(_make_key_event(KEY_LEFT))
	w._input(_make_key_event(KEY_LEFT))
	assert_eq(w._cursor_pos, 1)
	w._input(_make_key_event(KEY_HOME))
	assert_eq(w._cursor_pos, 0)
	w._input(_make_key_event(KEY_END))
	assert_eq(w._cursor_pos, 3)


# ── 滚动 ────────────────────────────────────────────────────

func test_page_up_page_down_scrolling() -> void:
	var w: TerminalWidget = _create_widget()
	w._input(_make_key_event(KEY_SLASH))

	for i in range(50):
		_type_text(w, "line %d" % i)
		_press_enter(w)

	assert_eq(w._scroll_offset, 0)
	w._input(_make_key_event(KEY_PAGEUP))
	assert_gt(w._scroll_offset, 0, "PageUp 应增加滚动偏移")
	w._input(_make_key_event(KEY_PAGEDOWN))
	assert_eq(w._scroll_offset, 0, "PageDown 回到底部")


# ── 自定义指令注册 ──────────────────────────────────────────

func test_register_and_execute_custom_command() -> void:
	var w: TerminalWidget = _create_widget()
	w.register_command("greet", func(args: PackedStringArray) -> String:
		return "Hello, " + args[0] + "!"
	, "greet <name>")

	w._input(_make_key_event(KEY_SLASH))
	_type_text(w, "greet World")
	_press_enter(w)

	var found := false
	for line in w._output_lines:
		if "Hello, World!" in line:
			found = true
			break
	assert_true(found, "自定义指令应产生输出")


func test_unregister_command() -> void:
	var w: TerminalWidget = _create_widget()
	w.register_command("test_cmd", func(_args: PackedStringArray) -> String:
		return "ok"
	, "")

	w.unregister_command("test_cmd")
	var results: Array = [""]
	w.remote_command_submitted.connect(func(cmd: String): results[0] = cmd)

	w._input(_make_key_event(KEY_SLASH))
	_type_text(w, "test_cmd")
	_press_enter(w)

	assert_eq(results[0], "test_cmd", "已注销的指令应转发为远程指令")


# ── 输入长度限制 ────────────────────────────────────────────

func test_input_length_limit() -> void:
	var w: TerminalWidget = _create_widget()
	w._input(_make_key_event(KEY_SLASH))
	for i in range(250):
		w._input(_make_text_event(ord("A")))
	assert_lte(w._input_text.length(), 200, "输入不应超过 200 字符")
