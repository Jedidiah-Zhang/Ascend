extends GutTest


func test_init_sets_label() -> void:
	var section: FPSSection = FPSSection.new()
	assert_eq(section.label, "性能")


func test_get_lines_format_is_stable() -> void:
	var section: FPSSection = FPSSection.new()
	section._stream_us = 10
	section._conn_us = 40
	var lines: PackedStringArray = section.get_lines()
	assert_string_contains(lines[0], "FPS:")
	assert_string_contains(lines[0], "TPS:")
	assert_string_contains(lines[1], "MSPT:")
	assert_string_contains(lines[1], "40μs")
	assert_string_contains(lines[2], "流式: 10μs")


func test_on_world_event_updates_tps() -> void:
	var section: FPSSection = FPSSection.new()
	section._prev_game_time = 0
	section._prev_real_msec = Time.get_ticks_msec()
	await wait_seconds(0.2)
	var data: Dictionary = {"game_time": 10}
	section.on_world_event("minute_change", {"data": data})
	assert_gt(section.tps, 0.0, "TPS 应 > 0")


func test_ignores_other_events() -> void:
	var section: FPSSection = FPSSection.new()
	section.on_world_event("unknown_event", {"data": {"game_time": 999}})
	assert_eq(section.tps, 24.0, "未知事件不应改变 TPS 默认值")
