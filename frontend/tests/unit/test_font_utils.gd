extends GutTest


func test_font_utils_class_exists() -> void:
	var cls = load("res://scripts/utils/font_utils.gd")
	assert_not_null(cls, "FontUtils 类应存在")


func test_get_mono_font_returns_font() -> void:
	var font = FontUtils.get_mono_font()
	assert_not_null(font, "get_mono_font() 必须返回一个 Font 实例")


func test_get_mono_font_is_font_type() -> void:
	var font = FontUtils.get_mono_font()
	assert_true(font is Font, "返回值应是 Font 类型")


func test_get_mono_font_does_not_return_system_font() -> void:
	var font = FontUtils.get_mono_font()
	assert_ne(font, null, "font 不应为 null")
