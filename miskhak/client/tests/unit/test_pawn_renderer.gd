"""PawnRenderer 单元测试 — 体型规格 → 分层部件列表（纯逻辑）。

覆盖：默认物种规格、部件顺序/叠层、朝向镜像、占位纹理、名称浮层锚点。
"""

extends GutTest


# ── 默认规格 ──────────────────────────────────────────────

func test_default_spec_creature_has_head_body_limbs() -> void:
	var spec: Dictionary = PawnRenderer.default_spec("CREATURE")
	assert_eq(spec[PawnRenderer.SPEC_TYPE], "CREATURE")
	var slots: Dictionary = spec[PawnRenderer.SPEC_SLOTS]
	assert_true(slots.has(PawnRenderer.SLOT_HEAD))
	assert_true(slots.has(PawnRenderer.SLOT_BODY))
	assert_true(slots.has(PawnRenderer.SLOT_LIMB_LEFT))
	assert_true(slots.has(PawnRenderer.SLOT_LIMB_RIGHT))


func test_default_spec_plant_has_crown_and_stem() -> void:
	var spec: Dictionary = PawnRenderer.default_spec("PLANT")
	var slots: Dictionary = spec[PawnRenderer.SPEC_SLOTS]
	assert_true(slots.has(PawnRenderer.SLOT_HEAD), "植物冠部走 head 槽")
	assert_true(slots.has(PawnRenderer.SLOT_BODY), "植物茎部走 body 槽")


func test_default_spec_unknown_falls_back_to_block() -> void:
	var spec: Dictionary = PawnRenderer.default_spec("STRUCTURE")
	var slots: Dictionary = spec[PawnRenderer.SPEC_SLOTS]
	assert_true(slots.has(PawnRenderer.SLOT_BODY))
	assert_false(slots.has(PawnRenderer.SLOT_HEAD))


# ── 部件构建 ─────────────────────────────────────────────

func test_build_parts_follows_slot_order() -> void:
	var spec: Dictionary = PawnRenderer.default_spec("CREATURE")
	var parts: Array = PawnRenderer.build_parts(spec)
	assert_eq(parts.size(), 4)
	var slots := []
	for p in parts:
		slots.append(p[PawnRenderer.PART_SLOT])
	assert_eq(slots, [
		PawnRenderer.SLOT_HEAD,
		PawnRenderer.SLOT_BODY,
		PawnRenderer.SLOT_LIMB_LEFT,
		PawnRenderer.SLOT_LIMB_RIGHT,
	], "部件必须按 SLOT_ORDER 槽位序输出")


func test_build_parts_z_increases_with_order() -> void:
	var spec: Dictionary = PawnRenderer.default_spec("CREATURE")
	var parts: Array = PawnRenderer.build_parts(spec)
	for i in parts.size():
		assert_eq(parts[i][PawnRenderer.PART_Z], i,
			"叠层 z 应为列表序（后渲染者在上）")


func test_build_parts_deterministic() -> void:
	var spec: Dictionary = PawnRenderer.default_spec("CREATURE")
	assert_eq(PawnRenderer.build_parts(spec), PawnRenderer.build_parts(spec),
		"同规格应产出完全相同部件列表")


func test_build_parts_facing_left_mirrors_x_offsets() -> void:
	var spec: Dictionary = PawnRenderer.default_spec("CREATURE")
	var right: Array = PawnRenderer.build_parts(spec, false)
	var left: Array = PawnRenderer.build_parts(spec, true)
	var width: int = spec[PawnRenderer.SPEC_WIDTH]
	for i in right.size():
		var ro: Vector2i = right[i][PawnRenderer.PART_OFFSET]
		var rs: Vector2i = right[i][PawnRenderer.PART_SIZE]
		var lo: Vector2i = left[i][PawnRenderer.PART_OFFSET]
		assert_eq(lo.x, width - ro.x - rs.x,
			"镜像后 x = width - (原 x + 宽)，y 不变")
		assert_eq(lo.y, ro.y)
		assert_eq(left[i][PawnRenderer.PART_COLOR], right[i][PawnRenderer.PART_COLOR])


func test_build_parts_mirror_swaps_left_and_right_limbs() -> void:
	"""左转向时左侧附肢应移到右侧：镜像后左肢 x 大于右肢 x。"""
	var spec: Dictionary = PawnRenderer.default_spec("CREATURE")
	var parts: Array = PawnRenderer.build_parts(spec, true)
	var left_x: int = 0
	var right_x: int = 0
	for p in parts:
		if p[PawnRenderer.PART_SLOT] == PawnRenderer.SLOT_LIMB_LEFT:
			left_x = p[PawnRenderer.PART_OFFSET].x
		if p[PawnRenderer.PART_SLOT] == PawnRenderer.SLOT_LIMB_RIGHT:
			right_x = p[PawnRenderer.PART_OFFSET].x
	assert_true(left_x > right_x, "朝左时左肢应位于右侧（镜像换位）")


func test_build_parts_handles_empty_slots() -> void:
	var spec: Dictionary = PawnRenderer.default_spec("PLANT")
	var parts: Array = PawnRenderer.build_parts(spec)
	assert_eq(parts.size(), 2, "植物规格只有头/身两部件，无附肢/变异/装备层")


# ── 占位纹理 ─────────────────────────────────────────────

func test_make_part_texture_matches_size_and_color() -> void:
	# 通道取 8-bit 可精确表示值（Image 存 8 位，0.1 → 25/255 会量化）
	var tex: ImageTexture = PawnRenderer.make_part_texture(
		Vector2i(6, 6), Color(0.2, 0.4, 0.6, 1.0))
	assert_eq(tex.get_size(), Vector2(6, 6))
	var img: Image = tex.get_image()
	assert_eq(img.get_pixel(0, 0), Color(0.2, 0.4, 0.6, 1.0))
	assert_eq(img.get_pixel(5, 5), Color(0.2, 0.4, 0.6, 1.0))


func test_make_part_texture_cached() -> void:
	"""同尺寸同色应返回同一纹理实例（朝向切换重建部件不重复生成）。"""
	var a: ImageTexture = PawnRenderer.make_part_texture(
		Vector2i(3, 3), Color(0.2, 0.4, 0.6, 1.0))
	var b: ImageTexture = PawnRenderer.make_part_texture(
		Vector2i(3, 3), Color(0.2, 0.4, 0.6, 1.0))
	assert_true(a == b, "缓存命中应返回同一实例")
	var c: ImageTexture = PawnRenderer.make_part_texture(
		Vector2i(4, 3), Color(0.2, 0.4, 0.6, 1.0))
	assert_true(c != a, "尺寸不同应生成新纹理")


# ── 名称浮层锚点 ─────────────────────────────────────────

func test_nameplate_offset_defaults() -> void:
	assert_eq(PawnRenderer.nameplate_offset({}), Vector2(0, -16),
		"规格缺失时应给默认头顶锚点")
	assert_eq(PawnRenderer.nameplate_offset(
		PawnRenderer.default_spec("CREATURE")), Vector2(8, -26))