extends GutTest


func test_init_sets_label() -> void:
	var section: MemorySection = MemorySection.new()
	assert_eq(section.label, "内存")


func test_get_lines_returns_two_lines() -> void:
	var section: MemorySection = MemorySection.new()
	var lines: PackedStringArray = section.get_lines()
	assert_eq(lines.size(), 2)
	assert_gt(lines[0].length(), 0)
	assert_gt(lines[1].length(), 0)


func test_get_lines_contains_memory_units() -> void:
	var section: MemorySection = MemorySection.new()
	var lines: PackedStringArray = section.get_lines()
	assert_string_contains(lines[0], "MB")
	assert_string_contains(lines[1], "节点数:")
