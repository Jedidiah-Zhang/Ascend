extends GutTest


func test_init_sets_label() -> void:
	var section: ConnectionSection = ConnectionSection.new()
	assert_eq(section.label, "连接")


func test_get_lines_returns_line() -> void:
	var section: ConnectionSection = ConnectionSection.new()
	var lines: PackedStringArray = section.get_lines()
	assert_eq(lines.size(), 1)
	assert_gt(lines[0].length(), 0)


func test_get_lines_contains_port() -> void:
	var section: ConnectionSection = ConnectionSection.new()
	var lines: PackedStringArray = section.get_lines()
	assert_string_contains(lines[0], "127.0.0.1")
	assert_string_contains(lines[0], "9081")
