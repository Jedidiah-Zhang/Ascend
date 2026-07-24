extends GutTest


func test_init_sets_label() -> void:
	var section: TimeSection = TimeSection.new()
	assert_eq(section.label, "时间")


func test_no_data_shows_dash() -> void:
	var section: TimeSection = TimeSection.new()
	assert_eq(section._has_data, false)
	var lines: PackedStringArray = section.get_lines()
	assert_eq(lines.size(), 1)
	assert_eq(lines[0], "—")


func test_minute_change_updates_time() -> void:
	var section: TimeSection = TimeSection.new()
	var data: Dictionary = {"day": 5}
	section.on_world_event("minute_change", {"data": data, "game_hour": 14, "game_minute": 30})
	assert_eq(section._has_data, true)
	assert_eq(section.day, 5)
	assert_eq(section.hour, 14)
	assert_eq(section.minute, 30)


func test_get_lines_after_update() -> void:
	var section: TimeSection = TimeSection.new()
	section.on_world_event("minute_change", {"data": {"day": 3}, "game_hour": 8, "game_minute": 5})
	var lines: PackedStringArray = section.get_lines()
	assert_string_contains(lines[0], "第 3 天 08:05")


func test_ignores_other_events() -> void:
	var section: TimeSection = TimeSection.new()
	section.on_world_event("weather_change", {"data": {}})
	assert_eq(section._has_data, false)
