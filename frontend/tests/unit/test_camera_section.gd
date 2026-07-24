extends GutTest


func test_init_sets_label() -> void:
	var section: CameraSection = CameraSection.new()
	assert_eq(section.label, "相机")


func test_default_state_shows_origin() -> void:
	var section: CameraSection = CameraSection.new()
	var lines: PackedStringArray = section.get_lines()
	assert_eq(lines.size(), 2)
	assert_string_contains(lines[0], "位置: (0, 0)")
	assert_eq(lines[1], "—")


func test_get_lines_reflects_state() -> void:
	var section: CameraSection = CameraSection.new()
	section.position = Vector2(456, 789)
	section._camera_display = "距离: 400m"
	var lines: PackedStringArray = section.get_lines()
	assert_string_contains(lines[0], "位置: (456, 789)")
	assert_eq(lines[1], "距离: 400m")
