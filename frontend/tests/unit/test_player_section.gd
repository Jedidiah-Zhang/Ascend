extends GutTest


func test_init_sets_label() -> void:
	var section: PlayerSection = PlayerSection.new()
	assert_eq(section.label, "玩家")


func test_default_state() -> void:
	var section: PlayerSection = PlayerSection.new()
	assert_eq(section.world_pos, Vector2.ZERO)
	assert_eq(section.chunk, Vector2i.ZERO)
	assert_eq(section.elevation, 0.0)


func test_get_lines_with_defaults() -> void:
	var section: PlayerSection = PlayerSection.new()
	var lines: PackedStringArray = section.get_lines()
	assert_eq(lines.size(), 2)
	assert_string_contains(lines[0], "坐标: (0, 0)")
	assert_string_contains(lines[0], "区块: (0, 0)")
	assert_string_contains(lines[1], "海拔: 0 m")


func test_get_lines_reflects_state() -> void:
	var section: PlayerSection = PlayerSection.new()
	section.world_pos = Vector2(1234, -567)
	section.chunk = Vector2i(6, -3)
	section.elevation = 1500.0
	var lines: PackedStringArray = section.get_lines()
	assert_string_contains(lines[0], "坐标: (1234, -567)")
	assert_string_contains(lines[0], "区块: (6, -3)")
	assert_string_contains(lines[1], "海拔: 1500 m")
