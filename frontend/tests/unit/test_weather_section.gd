extends GutTest


func test_init_sets_label() -> void:
	var section: WeatherSection = WeatherSection.new()
	assert_eq(section.label, "天气")


func test_initial_state_is_sunny() -> void:
	var section: WeatherSection = WeatherSection.new()
	assert_eq(section.current_weather, "晴")
	var lines: PackedStringArray = section.get_lines()
	assert_eq(lines.size(), 1)
	assert_string_contains(lines[0], "天气: 晴")


func test_on_world_response_ignores_other_requests() -> void:
	var section: WeatherSection = WeatherSection.new()
	section.on_world_response("get_entity", {"data": "ignored"})
	assert_eq(section.current_weather, "晴", "无关响应不应修改天气")


func test_apply_weather_data_updates_all_fields() -> void:
	var section: WeatherSection = WeatherSection.new()
	var weather_data: Dictionary = {
		"weather": "雨",
		"temperature": 25.5,
		"temp_perception": "凉爽",
		"humidity": 80.0,
		"hum_perception": "潮湿",
		"wind_speed": 5.2,
		"wind_perception": "微风",
		"sunshine": 6.0,
		"sun_perception": "中等",
	}
	section.on_world_response("get_weather", {"weathers": [weather_data]})
	assert_eq(section.current_weather, "雨")
	assert_eq(section.temperature, 25.5)
	assert_eq(section.temp_perception, "凉爽")
	assert_eq(section.humidity, 80.0)
	assert_eq(section.hum_perception, "潮湿")
	assert_eq(section.wind_speed, 5.2)
	assert_eq(section.wind_perception, "微风")
	assert_eq(section.sunshine, 6.0)
	assert_eq(section.sun_perception, "中等")


func test_get_lines_after_weather_update() -> void:
	var section: WeatherSection = WeatherSection.new()
	var weather_data: Dictionary = {
		"weather": "雪",
		"temperature": -5.0,
		"temp_perception": "寒冷",
		"humidity": 40.0,
		"hum_perception": "干燥",
		"wind_speed": 12.0,
		"wind_perception": "强风",
		"sunshine": 2.0,
		"sun_perception": "弱",
		"sunrise": 6.5,
		"sunset": 18.25,
		"sunshine_intensity": 0.35,
		"light_perception": "昏暗",
	}
	section.on_world_response("get_weather", {"weathers": [weather_data]})
	var lines: PackedStringArray = section.get_lines()

	assert_string_contains(lines[0], "天气: 雪")
	assert_string_contains(lines[1], "-5.0°C(寒冷)")
	assert_string_contains(lines[1], "40%(干燥)")
	assert_string_contains(lines[1], "12.0 m/s(强风)")
	assert_string_contains(lines[2], "日照 2.0h(弱)")
	assert_string_contains(lines[3], "光照 0.35(昏暗)")
	assert_string_contains(lines[3], "日出 06:30 → 日落 18:15")


func test_empty_weather_array_does_not_crash() -> void:
	var section: WeatherSection = WeatherSection.new()
	section.on_world_response("get_weather", {"weathers": []})
	assert_eq(section.current_weather, "晴")
