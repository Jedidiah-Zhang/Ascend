extends GutTest

const TerrainMeshBuilder = preload("res://scripts/world/terrain_mesh_builder.gd")


func test_top_ao_level_ground_is_white() -> void:
	var elevation := PackedFloat32Array([0, 0, 0, 0, 0, 0, 0, 0, 0])
	var c: Color = TerrainMeshBuilder._compute_top_ao(1, 1, 0, elevation, 3)
	assert_eq(c.r, 1.0, "平地无遮挡不应有 AO")
	assert_eq(c.g, c.r)
	assert_eq(c.b, c.r)


func test_top_ao_cliff_neighbor_darkens() -> void:
	var elevation := PackedFloat32Array([0, 0, 0, 2, 0, 0, 0, 0, 0])
	var c: Color = TerrainMeshBuilder._compute_top_ao(1, 1, 0, elevation, 3)
	var expected: float = 1.0 - 2.0 * TerrainMeshBuilder.AO_STRENGTH_PER_LEVEL
	assert_almost_eq(c.r, expected, 0.0001, "相邻高 2 层应按层级加深")


func test_top_ao_lower_neighbor_not_darkened() -> void:
	var elevation := PackedFloat32Array([0, 0, 0, -3, 0, 0, 0, 0, 0])
	var c: Color = TerrainMeshBuilder._compute_top_ao(1, 1, 0, elevation, 3)
	assert_eq(c.r, 1.0, "低处邻居不产生接触阴影")


func test_top_ao_high_neighbor_capped_at_three_levels() -> void:
	var elevation := PackedFloat32Array([0, 0, 0, 9, 0, 0, 0, 0, 0])
	var c: Color = TerrainMeshBuilder._compute_top_ao(1, 1, 0, elevation, 3)
	var expected: float = 1.0 - 3.0 * TerrainMeshBuilder.AO_STRENGTH_PER_LEVEL
	assert_almost_eq(c.r, expected, 0.0001, "单邻居最多按 3 层计")


func test_top_ao_clamped_at_floor() -> void:
	var elevation := PackedFloat32Array([0, 5, 0, 5, 0, 5, 0, 5, 0])
	var c: Color = TerrainMeshBuilder._compute_top_ao(1, 1, 0, elevation, 3)
	assert_almost_eq(c.r, TerrainMeshBuilder.AO_TOP_MIN, 0.0001, "四面高墙应钳制到下限")


func test_top_ao_boundary_neighbors_skipped() -> void:
	var elevation := PackedFloat32Array([0, 0, 0, 0, 0, 0, 0, 0, 0])
	var c: Color = TerrainMeshBuilder._compute_top_ao(0, 0, 0, elevation, 3)
	assert_eq(c.r, 1.0, "角落单元格出界邻居应跳过，不报错")
