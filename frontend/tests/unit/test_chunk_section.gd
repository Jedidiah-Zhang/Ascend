extends GutTest


func test_init_sets_label() -> void:
	var section: ChunkSection = ChunkSection.new()
	assert_eq(section.label, "区块")


func test_default_counts_are_zero() -> void:
	var section: ChunkSection = ChunkSection.new()
	assert_eq(section.loaded_count, 0)
	assert_eq(section.being_placed_count, 0)
	assert_eq(section.cached_count, 0)
	assert_eq(section.pending_count, 0)


func test_get_lines_with_defaults() -> void:
	var section: ChunkSection = ChunkSection.new()
	var lines: PackedStringArray = section.get_lines()
	assert_eq(lines.size(), 2)
	assert_string_contains(lines[0], "已加载: 0")
	assert_string_contains(lines[1], "缓存: 0")


func test_get_lines_reflects_internal_state() -> void:
	var section: ChunkSection = ChunkSection.new()
	section.loaded_count = 5
	section.being_placed_count = 2
	section.cached_count = 3
	section.pending_count = 1

	var lines: PackedStringArray = section.get_lines()
	assert_string_contains(lines[0], "已加载: 5")
	assert_string_contains(lines[0], "放置中: 2")
	assert_string_contains(lines[1], "缓存: 3")
	assert_string_contains(lines[1], "待发送: 1")
