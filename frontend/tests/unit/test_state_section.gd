"""StateSection 单元测试 — 状态追赶调试分区（纯逻辑 RefCounted）。

覆盖：默认值、内部状态刷新、加速显示行（含/不含剩余秒数）。
"""

extends GutTest


func test_init_sets_label() -> void:
	var section: StateSection = StateSection.new()
	assert_eq(section.label_key, "debug.section.state")


func test_default_values() -> void:
	var section: StateSection = StateSection.new()
	assert_eq(section.boost_mult, 1.0)
	assert_eq(section.boost_remaining, 0.0)
	assert_eq(section.chase_chunks, 0)


func test_get_lines_with_defaults() -> void:
	var section: StateSection = StateSection.new()
	var lines: PackedStringArray = section.get_lines()
	assert_eq(lines.size(), 2)
	assert_string_contains(lines[0], "加速 无")
	assert_string_contains(lines[1], "追赶: 0")


func test_get_lines_reflects_internal_state() -> void:
	var section: StateSection = StateSection.new()
	section.boost_mult = 3.0
	section.boost_remaining = 15.0
	section.chase_chunks = 2

	var lines: PackedStringArray = section.get_lines()
	assert_string_contains(lines[0], "加速 ×3.0")
	assert_string_contains(lines[0], "15s")
	assert_string_contains(lines[1], "追赶: 2")


func test_process_section_skips_without_world() -> void:
	var section: StateSection = StateSection.new()
	section.process_section(0.1)  # _world 为 null → 保持默认值，不崩溃
	assert_eq(section.boost_mult, 1.0)
