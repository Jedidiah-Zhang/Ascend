extends GutTest


func test_init_sets_label() -> void:
	var section: ElevationSection = ElevationSection.new()
	assert_eq(section.label, "地形")


func test_no_data_shows_dash() -> void:
	var section: ElevationSection = ElevationSection.new()
	assert_eq(section._has_data, false)
	var lines: PackedStringArray = section.get_lines()
	assert_eq(lines.size(), 1)
	assert_string_contains(lines[0], "海拔: —")
	assert_string_contains(lines[0], "坡度: —")


func test_get_lines_after_data_set() -> void:
	var section: ElevationSection = ElevationSection.new()
	section.elevation_value = 320
	section.slope_value = 12.5
	section._has_data = true
	var lines: PackedStringArray = section.get_lines()
	assert_string_contains(lines[0], "海拔: 320")
	assert_string_contains(lines[0], "坡度: 12.5°")
