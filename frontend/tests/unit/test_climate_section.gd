extends GutTest


func test_init_sets_label() -> void:
	var section: ClimateSection = ClimateSection.new()
	assert_eq(section.label, "气候")


func test_no_data_shows_dashes() -> void:
	var section: ClimateSection = ClimateSection.new()
	var lines: PackedStringArray = section.get_lines()
	assert_eq(lines.size(), 2)
	assert_string_contains(lines[0], "年均温: —")
	assert_string_contains(lines[0], "年均湿度: —")
	assert_string_contains(lines[1], "气候: —")


func test_get_lines_with_temperature_humidity() -> void:
	var section: ClimateSection = ClimateSection.new()
	section.temperature = 18.5
	section._has_temp = true
	section.humidity = 65.0
	section._has_humidity = true
	var lines: PackedStringArray = section.get_lines()
	assert_string_contains(lines[0], "年均温: 18.5°C")
	assert_string_contains(lines[0], "年均湿度: 65%")


func test_get_lines_with_climate_zone() -> void:
	var section: ClimateSection = ClimateSection.new()
	section.climate_zone = 0
	var lines: PackedStringArray = section.get_lines()
	assert_string_contains(lines[1], "气候: 热带雨林")


func test_climate_zone_out_of_bounds_shows_dash() -> void:
	var section: ClimateSection = ClimateSection.new()
	section.climate_zone = 999
	var lines: PackedStringArray = section.get_lines()
	assert_string_contains(lines[1], "气候: —")
