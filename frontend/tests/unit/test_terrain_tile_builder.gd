"""TerrainTileBuilder 单元测试 — 2D tile 层数据生成纯函数。

build_cells / make_placeholder_atlas / make_tile_set 均为纯计算（不触碰
场景树与 RenderingServer），可直接实例化验证。层划分断言与后端
TerrainType 枚举（9 地形）对齐。
"""

extends GutTest

const TerrainTileBuilder = preload("res://scripts/world/terrain_tile_builder.gd")
const CS: int = Config.TILE_MAP_SIZE


func _flat_terrain(id: int) -> PackedInt32Array:
	var terr := PackedInt32Array()
	terr.resize(CS * CS)
	terr.fill(id)
	return terr


func _flat_elevation(v: float) -> PackedFloat32Array:
	var elev := PackedFloat32Array()
	elev.resize(CS * CS)
	elev.fill(v)
	return elev


# ── build_cells 层划分 ─────────────────────────────────────

func test_grassland_goes_to_terrain_layer() -> void:
	var cells: Dictionary = TerrainTileBuilder.build_cells(
		_flat_terrain(0), _flat_elevation(10.0))
	assert_eq(cells[TerrainTileBuilder.LAYER_TERRAIN].size(), CS * CS,
		"全草原 chunk 应全部进地形层")
	assert_eq(cells[TerrainTileBuilder.LAYER_WATER].size(), 0,
		"无水域 chunk 水面层应为空")


func test_shallow_water_goes_to_water_layer() -> void:
	var cells: Dictionary = TerrainTileBuilder.build_cells(
		_flat_terrain(TerrainTileBuilder.SHALLOW_WATER_ID), _flat_elevation(0.0))
	assert_eq(cells[TerrainTileBuilder.LAYER_TERRAIN].size(), 0,
		"浅水不应进地形层")
	assert_eq(cells[TerrainTileBuilder.LAYER_WATER].size(), CS * CS,
		"全浅水 chunk 应全部进水面层")


func test_deep_water_uses_water_atlas_column_1() -> void:
	var cells: Dictionary = TerrainTileBuilder.build_cells(
		_flat_terrain(TerrainTileBuilder.DEEP_WATER_ID), _flat_elevation(-5.0))
	var water: Array = cells[TerrainTileBuilder.LAYER_WATER]
	assert_eq(water.size(), CS * CS)
	assert_eq(water[0][2], Vector2i(1, 0), "深水应为水面 atlas 第 2 列")


func test_shallow_water_uses_water_atlas_column_0() -> void:
	var cells: Dictionary = TerrainTileBuilder.build_cells(
		_flat_terrain(TerrainTileBuilder.SHALLOW_WATER_ID), _flat_elevation(0.0))
	var water: Array = cells[TerrainTileBuilder.LAYER_WATER]
	assert_eq(water[0][2], Vector2i(0, 0), "浅水应为水面 atlas 第 1 列")


func test_cell_shape_matches_set_cells_contract() -> void:
	"""元素形状 [cell_pos, source_id, atlas_coords] 可直接喂 TileMapLayer.set_cells。"""
	var cells: Dictionary = TerrainTileBuilder.build_cells(
		_flat_terrain(1), _flat_elevation(5.0))
	var terrain: Array = cells[TerrainTileBuilder.LAYER_TERRAIN]
	var cell: Array = terrain[0]
	assert_eq(cell.size(), 3)
	assert_eq(cell[0], Vector2i(0, 0), "首格应为 chunk 局部 (0,0)")
	assert_eq(cell[1], 0, "source id 应为 0（单源占位 atlas）")
	assert_eq(cell[2], Vector2i(1, 0), "沙地应为地形 atlas 第 2 列")


func test_cell_coords_row_major() -> void:
	"""行优先布局：第二行首格坐标为 (0,1)。"""
	var terr := PackedInt32Array()
	terr.resize(CS * CS)
	terr.fill(0)
	terr[CS] = 1  # 第二行第一列 = 沙地
	var cells: Dictionary = TerrainTileBuilder.build_cells(terr, _flat_elevation(1.0))
	var found := false
	for cell in cells[TerrainTileBuilder.LAYER_TERRAIN]:
		if cell[0] == Vector2i(0, 1):
			assert_eq(cell[2], Vector2i(1, 0), "沙地应映射到 atlas 第 2 列")
			found = true
	assert_true(found, "行优先索引 (1*CS+0) 应落到 chunk 局部 (0,1)")


func test_terrain_mapping_length_matches_backend() -> void:
	"""9 地形映射与后端 TerrainType 枚举对齐（阶段 1 全部列索引 = terrain_id）。"""
	assert_eq(TerrainTileBuilder.TERRAIN_TO_TILE.size(), 9)


func test_short_terrain_array_pads_with_grassland() -> void:
	"""越界输入（长度不足）按 0（草原）填充——防御性契约。"""
	var terr := PackedInt32Array([1, 2, 3])
	var cells: Dictionary = TerrainTileBuilder.build_cells(terr, _flat_elevation(0.0))
	assert_eq(cells[TerrainTileBuilder.LAYER_TERRAIN].size(), CS * CS)


func test_build_cells_returns_all_six_layers() -> void:
	"""build_cells 契约：六层键全部存在（五信号齐全）。"""
	var cells: Dictionary = TerrainTileBuilder.build_cells(
		_flat_terrain(0), _flat_elevation(10.0))
	for layer in [
		TerrainTileBuilder.LAYER_TERRAIN, TerrainTileBuilder.LAYER_WATER,
		TerrainTileBuilder.LAYER_CLIFF, TerrainTileBuilder.LAYER_SHADOW,
		TerrainTileBuilder.LAYER_DECOR, TerrainTileBuilder.LAYER_CONTOUR,
	]:
		assert_true(cells.has(layer), "缺少层 %s" % layer)


func test_flat_terrain_has_no_cliff_or_shadow() -> void:
	"""海拔无高差：不应有崖壁/投影/等高线。"""
	var cells: Dictionary = TerrainTileBuilder.build_cells(
		_flat_terrain(0), _flat_elevation(10.0))
	assert_eq(cells[TerrainTileBuilder.LAYER_CLIFF].size(), 0)
	assert_eq(cells[TerrainTileBuilder.LAYER_SHADOW].size(), 0)
	assert_eq(cells[TerrainTileBuilder.LAYER_CONTOUR].size(), 0)


func test_flat_terrain_has_deterministic_decor() -> void:
	"""平地（档位 0，密度 2%）装饰数量确定且稳定（非随机源）。"""
	var a: Dictionary = TerrainTileBuilder.build_cells(
		_flat_terrain(0), _flat_elevation(10.0))
	var b: Dictionary = TerrainTileBuilder.build_cells(
		_flat_terrain(0), _flat_elevation(10.0))
	var n: int = a[TerrainTileBuilder.LAYER_DECOR].size()
	assert_eq(n, b[TerrainTileBuilder.LAYER_DECOR].size(),
		"同输入两次构建装饰数必须一致（确定性哈希）")
	assert_gt(n, 0, "密度 2% 的平地应有少量装饰")
	assert_lt(n, 2000, "装饰数应在密度预算内（~800/40000）")


func test_cliff_appears_on_high_side_of_drop() -> void:
	"""海拔断崖：高侧边缘 tile 画崖壁，低侧不画（高侧在西）。"""
	var elev := _flat_elevation(50.0)
	for x in range(0, 100):
		for z in CS:
			elev[z * CS + x] = 120.0
	var cells: Dictionary = TerrainTileBuilder.build_cells(
		_flat_terrain(0), elev)
	var cliffs: Array = cells[TerrainTileBuilder.LAYER_CLIFF]
	assert_eq(cliffs.size(), CS, "西侧 70m 断崖应整列高侧 tile 画崖壁")
	var first: Array = cliffs[0]
	assert_eq(first[0], Vector2i(99, 0), "崖壁应落在高侧（x=99 即 x<100 一侧）")


func test_cliff_on_high_side_with_drop_to_east() -> void:
	"""高侧在东：高侧边缘同样画崖壁（双向邻居探测）。"""
	var elev := _flat_elevation(50.0)
	for x in range(100, CS):
		for z in CS:
			elev[z * CS + x] = 120.0
	var cells: Dictionary = TerrainTileBuilder.build_cells(
		_flat_terrain(0), elev)
	var cliffs: Array = cells[TerrainTileBuilder.LAYER_CLIFF]
	assert_eq(cliffs.size(), CS, "东侧断崖整列高侧 tile 画崖壁")
	var first: Array = cliffs[0]
	assert_eq(first[0], Vector2i(100, 0), "崖壁落在高侧（x=100）")


func test_small_diff_no_cliff() -> void:
	"""高差低于阈值（8m）：不画崖壁。"""
	var elev := _flat_elevation(50.0)
	for x in range(100, CS):
		for z in CS:
			elev[z * CS + x] = 55.0  # 差 5m < 阈值
	var cells: Dictionary = TerrainTileBuilder.build_cells(
		_flat_terrain(0), elev)
	assert_eq(cells[TerrainTileBuilder.LAYER_CLIFF].size(), 0)


func test_shadow_falls_on_low_side_of_high_neighbor() -> void:
	"""固定方向投影：光照自西北，西侧高地 → 东侧低地铺阴影。"""
	var elev := _flat_elevation(50.0)
	for x in range(0, 100):
		for z in CS:
			elev[z * CS + x] = 120.0
	var cells: Dictionary = TerrainTileBuilder.build_cells(
		_flat_terrain(0), elev)
	var shadows: Array = cells[TerrainTileBuilder.LAYER_SHADOW]
	assert_eq(shadows.size(), CS, "东侧低地整列应被投影")
	var first: Array = shadows[0]
	assert_eq(first[0], Vector2i(100, 0), "阴影落在低侧（x=100）")


func test_shadow_from_south_high_neighbor() -> void:
	"""北侧邻居更高 → 本 tile 也被投影（北向光照）。"""
	var elev := _flat_elevation(50.0)
	for x in CS:
		for z in range(0, 100):
			elev[z * CS + x] = 120.0
	var cells: Dictionary = TerrainTileBuilder.build_cells(
		_flat_terrain(0), elev)
	var shadows: Array = cells[TerrainTileBuilder.LAYER_SHADOW]
	assert_eq(shadows.size(), CS, "北侧高地对南侧低地投影")


func test_snowcap_on_peak_above_snowline() -> void:
	"""MOUNTAIN_PEAK ≥ 2000m → 全部雪顶装饰（雪线概念）。"""
	var terr := _flat_terrain(0)
	var elev := _flat_elevation(10.0)
	for x in CS:
		for z in CS:
			terr[z * CS + x] = 5  # MOUNTAIN_PEAK
			elev[z * CS + x] = 2100.0
	var cells: Dictionary = TerrainTileBuilder.build_cells(terr, elev)
	var decor: Array = cells[TerrainTileBuilder.LAYER_DECOR]
	assert_eq(decor.size(), CS * CS, "雪线以上峰区应全部有雪顶")
	var first: Array = decor[0]
	assert_eq(first[2], Vector2i(TerrainTileBuilder.DECOR_TILE_SNOWCAP, 0),
		"应为雪顶 tile 列")


func test_no_snowcap_below_snowline() -> void:
	"""MOUNTAIN_PEAK 但海拔 < 2000m：无雪顶，按密度放岩石装饰。"""
	var terr := _flat_terrain(0)
	var elev := _flat_elevation(10.0)
	for x in CS:
		for z in CS:
			terr[z * CS + x] = 5
			elev[z * CS + x] = 1500.0
	var cells: Dictionary = TerrainTileBuilder.build_cells(terr, elev)
	for cell in cells[TerrainTileBuilder.LAYER_DECOR]:
		assert_ne(cell[2], Vector2i(TerrainTileBuilder.DECOR_TILE_SNOWCAP, 0),
			"雪线下峰区不应有雪顶")


func test_contour_at_500m_crossing() -> void:
	"""等高线：本 tile 与东邻居跨过 500m 档位 → 落在等高线上。"""
	var elev := _flat_elevation(0.0)
	for x in range(100, CS):
		for z in CS:
			elev[z * CS + x] = 600.0
	var cells: Dictionary = TerrainTileBuilder.build_cells(
		_flat_terrain(0), elev)
	var contours: Array = cells[TerrainTileBuilder.LAYER_CONTOUR]
	assert_eq(contours.size(), CS, "0m/600m 分界应画一列等高线")
	var first: Array = contours[0]
	assert_eq(first[0], Vector2i(99, 0), "等高线画在档位边界的低侧 tile")


func test_water_tiles_excluded_from_signals() -> void:
	"""水域 tile 不进五信号层（崖壁/投影/装饰均跳过水域）。"""
	var terr := _flat_terrain(TerrainTileBuilder.SHALLOW_WATER_ID)
	var cells: Dictionary = TerrainTileBuilder.build_cells(terr, _flat_elevation(0.0))
	assert_eq(cells[TerrainTileBuilder.LAYER_CLIFF].size(), 0)
	assert_eq(cells[TerrainTileBuilder.LAYER_SHADOW].size(), 0)
	assert_eq(cells[TerrainTileBuilder.LAYER_DECOR].size(), 0)
	assert_eq(cells[TerrainTileBuilder.LAYER_CONTOUR].size(), 0)


func test_missing_elevation_only_skips_signals() -> void:
	"""高程数据缺失：地形/水面层照常构建，信号层为空（防御性契约）。"""
	var cells: Dictionary = TerrainTileBuilder.build_cells(
		_flat_terrain(1), PackedFloat32Array())
	assert_eq(cells[TerrainTileBuilder.LAYER_TERRAIN].size(), CS * CS)
	assert_eq(cells[TerrainTileBuilder.LAYER_CLIFF].size(), 0)
	assert_eq(cells[TerrainTileBuilder.LAYER_SHADOW].size(), 0)
	assert_eq(cells[TerrainTileBuilder.LAYER_DECOR].size(), 0)
	assert_eq(cells[TerrainTileBuilder.LAYER_CONTOUR].size(), 0)


func test_invalid_terrain_id_skipped() -> void:
	"""未知 terrain_id 应跳过（不崩溃、不产生 tile）。"""
	var terr := _flat_terrain(99)
	var cells: Dictionary = TerrainTileBuilder.build_cells(terr, _flat_elevation(0.0))
	assert_eq(cells[TerrainTileBuilder.LAYER_TERRAIN].size(), 0)
	assert_eq(cells[TerrainTileBuilder.LAYER_WATER].size(), 0)


# ── 占位 atlas ─────────────────────────────────────────────

func test_placeholder_atlas_size() -> void:
	var tex: ImageTexture = TerrainTileBuilder.make_placeholder_atlas(
		TerrainTileBuilder.TERRAIN_TILE_COLORS)
	assert_eq(tex.get_width(), 9 * 16, "9 列 × 16px 宽")
	assert_eq(tex.get_height(), 16, "16px 高")


func test_placeholder_atlas_first_pixel_matches_color() -> void:
	var tex: ImageTexture = TerrainTileBuilder.make_placeholder_atlas(
		TerrainTileBuilder.TERRAIN_TILE_COLORS)
	var img: Image = tex.get_image()
	assert_almost_eq(img.get_pixel(0, 0).r, TerrainTileBuilder.TERRAIN_TILE_COLORS[0].r, 0.01)


func test_tile_set_tile_size() -> void:
	var ts: TileSet = TerrainTileBuilder.make_tile_set(TerrainTileBuilder.TERRAIN_TILE_COLORS)
	assert_eq(ts.tile_size, Vector2i(16, 16))


func test_tile_set_single_source() -> void:
	var ts: TileSet = TerrainTileBuilder.make_tile_set(TerrainTileBuilder.TERRAIN_TILE_COLORS)
	assert_eq(ts.get_source_count(), 1, "占位期单源 atlas")


func test_tile_set_water_atlas_has_two_tiles() -> void:
	var ts: TileSet = TerrainTileBuilder.make_tile_set(TerrainTileBuilder.WATER_TILE_COLORS)
	var src: TileSetAtlasSource = ts.get_source(0)
	assert_eq(src.get_tiles_count(), 2, "浅水/深水两个 tile")

# ── 邻居上下文（chunk 接缝连续性） ─────────────────────────

func _neighbor_edge(edge: String, terr: PackedInt32Array,
		elev: PackedFloat32Array) -> Dictionary:
	"""从完整邻居数组提取紧邻边条（与 main_world._collect_edge 同语义）。"""
	var eterr := PackedInt32Array()
	eterr.resize(CS)
	var eelev := PackedFloat32Array()
	eelev.resize(CS)
	for i in CS:
		match edge:
			"east":  # 邻居东侧列（x=CS-1）邻接本 chunk 西边
				eterr[i] = terr[i * CS + (CS - 1)]
				eelev[i] = elev[i * CS + (CS - 1)]
			"west":  # 邻居西侧列（x=0）邻接本 chunk 东边
				eterr[i] = terr[i * CS]
				eelev[i] = elev[i * CS]
			"south": # 邻居南侧行（z=CS-1）邻接本 chunk 北边
				eterr[i] = terr[(CS - 1) * CS + i]
				eelev[i] = elev[(CS - 1) * CS + i]
			"north": # 邻居北侧行（z=0）邻接本 chunk 南边
				eterr[i] = terr[i]
				eelev[i] = elev[i]
	return {"terrain": eterr, "elevation": eelev}


func test_empty_neighbors_identical_to_no_args() -> void:
	"""空邻居字典与不传邻居等价（旧调用兼容）。"""
	var terr := _flat_terrain(0)
	var elev := _flat_elevation(10.0)
	var a: Dictionary = TerrainTileBuilder.build_cells(terr, elev)
	var b: Dictionary = TerrainTileBuilder.build_cells(terr, elev, {})
	for layer in a:
		assert_eq(a[layer], b[layer], "层 %s 应一致" % layer)


func test_cliff_continues_across_west_seam_with_neighbor() -> void:
	"""西邻居低 → 本 chunk 高侧西边（x=0）应画崖壁（接缝连续）。"""
	var elev := _flat_elevation(120.0)
	var n_elev := _flat_elevation(50.0)
	var neighbors := {"west": _neighbor_edge("east", _flat_terrain(0), n_elev)}
	var cells: Dictionary = TerrainTileBuilder.build_cells(
		_flat_terrain(0), elev, neighbors)
	var cliffs: Array = cells[TerrainTileBuilder.LAYER_CLIFF]
	assert_eq(cliffs.size(), CS, "西邻低地断崖应跨接缝整列画崖壁")
	for cell in cliffs:
		assert_eq(cell[0].x, 0, "崖壁应落在本 chunk 高侧西边（x=0）")


func test_cliff_boundary_without_neighbor_skipped() -> void:
	"""无邻居上下文：边界按无邻居语义跳过（旧行为保持，不误报）。"""
	var cells: Dictionary = TerrainTileBuilder.build_cells(
		_flat_terrain(0), _flat_elevation(120.0))
	assert_eq(cells[TerrainTileBuilder.LAYER_CLIFF].size(), 0,
		"孤岛 chunk 边界不画崖壁")


func test_shadow_continues_across_west_seam_with_neighbor() -> void:
	"""西邻居高 → 本 chunk 西边（x=0）低侧被投影（固定方向投影接缝连续）。"""
	var elev := _flat_elevation(50.0)
	var n_elev := _flat_elevation(120.0)
	var neighbors := {"west": _neighbor_edge("east", _flat_terrain(0), n_elev)}
	var cells: Dictionary = TerrainTileBuilder.build_cells(
		_flat_terrain(0), elev, neighbors)
	var shadows: Array = cells[TerrainTileBuilder.LAYER_SHADOW]
	assert_eq(shadows.size(), CS, "西邻高地投影应跨接缝整列落下")
	for cell in shadows:
		assert_eq(cell[0].x, 0, "投影应落在本 chunk 西边（x=0，低侧）")


func test_contour_continues_across_east_seam_with_neighbor() -> void:
	"""东邻居跨 500m 档位边界 → 本 chunk 东边画等高线（接缝连续）。"""
	var elev := _flat_elevation(499.0)
	var n_elev := _flat_elevation(501.0)
	var neighbors := {"east": _neighbor_edge("west", _flat_terrain(0), n_elev)}
	var cells: Dictionary = TerrainTileBuilder.build_cells(
		_flat_terrain(0), elev, neighbors)
	var contours: Array = cells[TerrainTileBuilder.LAYER_CONTOUR]
	assert_eq(contours.size(), CS, "500m 档边界应跨接缝在东边画等高线")
	for cell in contours:
		assert_eq(cell[0].x, CS - 1, "等高线落在本 chunk 东边")


func test_cliff_skipped_when_neighbor_edge_is_water() -> void:
	"""水域邻居不产生崖壁（与本地邻居语义一致）。"""
	var elev := _flat_elevation(120.0)
	var n_terr := _flat_terrain(TerrainTileBuilder.SHALLOW_WATER_ID)
	var n_elev := _flat_elevation(50.0)
	var neighbors := {"west": _neighbor_edge("east", n_terr, n_elev)}
	var cells: Dictionary = TerrainTileBuilder.build_cells(
		_flat_terrain(0), elev, neighbors)
	assert_eq(cells[TerrainTileBuilder.LAYER_CLIFF].size(), 0,
		"水域邻居不产生崖壁")
