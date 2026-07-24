extends GutTest


func _create_overlay() -> DebugOverlay:
	var overlay: DebugOverlay = autoqfree(DebugOverlay.new())
	add_child(overlay)
	return overlay


func _make_key_event(keycode: Key, pressed: bool = true) -> InputEventKey:
	var ev := InputEventKey.new()
	ev.keycode = keycode
	ev.pressed = pressed
	ev.echo = false
	return ev


# ── 切换 ────────────────────────────────────────────────────

func test_f3_toggles_visible() -> void:
	var o: DebugOverlay = _create_overlay()
	assert_false(o.is_shown())
	o._input(_make_key_event(KEY_F3))
	assert_true(o.is_shown())
	o._input(_make_key_event(KEY_F3))
	assert_false(o.is_shown())


func test_toggle_method() -> void:
	var o: DebugOverlay = _create_overlay()
	assert_false(o.is_shown())
	o.toggle()
	assert_true(o.is_shown())
	o.toggle()
	assert_false(o.is_shown())


func test_toggled_signal() -> void:
	var o: DebugOverlay = _create_overlay()
	var results: Array = [false]
	o.toggled.connect(func(shown: bool): results[0] = shown)
	o.toggle()
	assert_true(results[0])
	o.toggle()
	assert_false(results[0])


# ── 分区管理 ────────────────────────────────────────────────

const EXPECTED_SECTION_COUNT := 9


class TestSection extends DebugSection:
	var process_calls: int = 0
	var event_calls: int = 0
	var response_calls: int = 0
	var last_event: String = ""
	var last_response: String = ""

	func _init(p_label: String) -> void:
		label = p_label

	func process_section(_delta: float) -> void:
		process_calls += 1

	func on_world_event(event_type: String, _payload: Dictionary) -> void:
		event_calls += 1
		last_event = event_type

	func on_world_response(request_type: String, _payload: Dictionary) -> void:
		response_calls += 1
		last_response = request_type


func test_add_and_remove_section() -> void:
	var o: DebugOverlay = _create_overlay()
	var section: TestSection = autoqfree(TestSection.new("Test"))
	o.add_section(section)
	assert_eq(o._sections.size(), 1)
	assert_eq(o.get_section("Test"), section)
	o.remove_section("Test")
	assert_eq(o._sections.size(), 0)
	assert_null(o.get_section("Test"))


func test_remove_nonexistent_section() -> void:
	var o: DebugOverlay = _create_overlay()
	o.remove_section("nonexistent")
	assert_eq(o._sections.size(), 0)


func test_default_sections_created() -> void:
	var o: DebugOverlay = _create_overlay()
	var world_double: Node = Node.new()
	add_child(world_double)
	o.setup_default_sections(world_double)
	assert_eq(o._sections.size(), EXPECTED_SECTION_COUNT)


# ── 分区生命周期调度 ────────────────────────────────────────

func test_process_sections_calls_enabled_sections() -> void:
	var o: DebugOverlay = _create_overlay()
	var section: TestSection = autoqfree(TestSection.new("TestSection"))
	o.add_section(section)
	o.process_sections(0.016)
	assert_eq(section.process_calls, 1)


func test_process_sections_skips_disabled() -> void:
	var o: DebugOverlay = _create_overlay()
	var section: TestSection = autoqfree(TestSection.new("TestSection"))
	section.enabled = false
	o.add_section(section)
	o.process_sections(0.016)
	assert_eq(section.process_calls, 0)


func test_broadcast_event_to_all_sections() -> void:
	var o: DebugOverlay = _create_overlay()
	var section1: TestSection = autoqfree(TestSection.new("S0"))
	var section2: TestSection = autoqfree(TestSection.new("S1"))
	o.add_section(section1)
	o.add_section(section2)
	o.broadcast_event("minute_change", {})
	assert_eq(section1.event_calls, 1)
	assert_eq(section1.last_event, "minute_change")
	assert_eq(section2.event_calls, 1)


func test_broadcast_response_to_all_sections() -> void:
	var o: DebugOverlay = _create_overlay()
	var section1: TestSection = autoqfree(TestSection.new("S0"))
	var section2: TestSection = autoqfree(TestSection.new("S1"))
	o.add_section(section1)
	o.add_section(section2)
	o.broadcast_response("get_weather", {})
	assert_eq(section1.response_calls, 1)
	assert_eq(section1.last_response, "get_weather")
	assert_eq(section2.response_calls, 1)


# ── 可见区域 ────────────────────────────────────────────────

func test_input_ignored_when_not_shown() -> void:
	var o: DebugOverlay = _create_overlay()
	assert_false(o.is_shown())
	o._input(_make_key_event(KEY_A))
	assert_false(o.is_shown())
