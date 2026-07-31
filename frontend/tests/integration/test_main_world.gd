extends GutTest

const Config = preload("res://scripts/config.gd")
const TerrainMeshBuilder = preload("res://scripts/world/terrain_mesh_builder.gd")
const CS: int = Config.TILE_MAP_SIZE


# ── 场景加载 ────────────────────────────────────────────────

func test_main_scene_loads() -> void:
	var scene: PackedScene = load("res://scenes/main.tscn")
	assert_not_null(scene, "main.tscn 应可加载")
	var instance: Node = autoqfree(scene.instantiate())
	add_child(instance)
	assert_not_null(instance, "场景实例化不应为 null")


func test_main_scene_is_node3d() -> void:
	var scene: PackedScene = load("res://scenes/main.tscn")
	var instance: Node = autoqfree(scene.instantiate())
	add_child(instance)
	assert_true(instance is Node3D)


# ── Chunk 地形查询（纯逻辑方法） ──────────────────────────────

func _make_world_instance() -> Node3D:
	var scene: PackedScene = load("res://scenes/main.tscn")
	var instance: Node = autoqfree(scene.instantiate())
	add_child(instance)
	return instance as Node3D


func test_ground_elevation_at_no_chunk_returns_nan() -> void:
	var main: Node3D = _make_world_instance()
	var result: float = main._get_ground_elevation_at(Vector3(100, 0, 100))
	assert_true(is_nan(result), "无 chunk 数据时应返回 NaN")


func test_ground_elevation_at_with_data() -> void:
	var main: Node3D = _make_world_instance()

	var key := Vector2i(0, 0)
	var elev_arr: Array = []
	elev_arr.resize(CS * CS)
	elev_arr.fill(10.0)
	main._chunks[key] = {"elevation": elev_arr}

	var result: float = main._get_ground_elevation_at(Vector3(50, 0, 50))
	assert_eq(result, 10.0, "应返回正确海拔")


func test_terrain_at_returns_data() -> void:
	var main: Node3D = _make_world_instance()

	var key := Vector2i(0, 0)
	var elev_arr: Array = []
	elev_arr.resize(CS * CS)
	elev_arr.fill(5.0)
	var slope_arr: Array = []
	slope_arr.resize(CS * CS)
	slope_arr.fill(0.5)
	main._chunks[key] = {"elevation": elev_arr, "slope": slope_arr}

	var data: Dictionary = main.get_debug_terrain_at(Vector2(100, 100))
	assert_eq(int(data["elevation"]), 5)
	assert_eq(float(data["slope"]), 0.5)


func test_climate_at_returns_data() -> void:
	var main: Node3D = _make_world_instance()

	var key := Vector2i(0, 0)
	main._chunks[key] = {"temperature": 22.5, "humidity": 65.0, "climate": 3}

	var data: Dictionary = main.get_debug_climate_at(Vector2(50, 50))
	assert_eq(float(data["temperature"]), 22.5)
	assert_eq(float(data["humidity"]), 65.0)
	assert_eq(int(data["climate_zone"]), 3)


# ── 出生点 ──────────────────────────────────────────────────

func test_birth_chunk_sets_player_position() -> void:
	var main: Node3D = _make_world_instance()

	assert_false(main._has_birth)
	main._set_birth_chunk(5, 3)
	assert_true(main._has_birth)
	assert_eq(main._birth_chunk, Vector2i(5, 3))
	assert_eq(main._player_pos.x, 5.0 * CS + CS / 2.0)
	assert_eq(main._player_pos.z, 3.0 * CS + CS / 2.0)


func test_birth_chunk_only_set_once() -> void:
	var main: Node3D = _make_world_instance()

	main._set_birth_chunk(2, 2)
	assert_eq(main._birth_chunk, Vector2i(2, 2))
	main._set_birth_chunk(9, 9)
	assert_eq(main._birth_chunk, Vector2i(2, 2), "出生区块只应设置一次")


# ── Debug 数据 getter ───────────────────────────────────────

func test_debug_player_info_defaults() -> void:
	var main: Node3D = _make_world_instance()

	var info: Dictionary = main.get_debug_player_info()
	assert_not_null(info.get("world_pos"))
	assert_not_null(info.get("chunk"))
	assert_true(info.has("elevation"))


func test_debug_chunk_stats_defaults() -> void:
	var main: Node3D = _make_world_instance()

	var stats: Dictionary = main.get_debug_chunk_stats()
	assert_eq(stats["loaded"], 0)
	assert_eq(stats["cached"], 0)
	assert_eq(stats["pending"], 0)


func test_debug_timing_has_expected_keys() -> void:
	var main: Node3D = _make_world_instance()

	var timing: Dictionary = main.get_debug_timing()
	assert_true(timing.has("stream"))
	assert_true(timing.has("conn"))


# ── 流式半径 ────────────────────────────────────────────────

func test_stream_radius_minimum() -> void:
	var main: Node3D = _make_world_instance()
	main._camera_distance = main.CAMERA_DISTANCE_MIN

	var r: int = main._stream_radius()
	assert_gt(r, 0, "半径应 > 0")


# ── 阴影覆盖 ──────────────────────────────────────────────

func test_shadow_coverage_scales_with_zoom() -> void:
	var main: Node3D = _make_world_instance()

	main._camera_distance = 400.0
	var near: float = main._compute_shadow_coverage(0.5)
	main._camera_distance = 1200.0
	var far: float = main._compute_shadow_coverage(0.5)

	assert_gt(far, near, "拉远时覆盖范围应扩大")
	assert_lt(far, near * 4.0, "覆盖范围应与可视范围线性相关")


func test_shadow_coverage_covers_visible_footprint() -> void:
	var main: Node3D = _make_world_instance()
	main._camera_distance = 400.0

	var coverage: float = main._compute_shadow_coverage(0.5)
	var radius: float = main._compute_visible_radius()
	assert_gt(coverage, radius, "覆盖半径应大于可视半径")
	assert_lt(coverage, radius * 2.0, "覆盖应紧凑，避免浪费阴影分辨率")


func test_low_sun_expands_shadow_coverage() -> void:
	var main: Node3D = _make_world_instance()

	var low: float = main._compute_shadow_coverage(0.15)
	var noon: float = main._compute_shadow_coverage(0.5)
	assert_gt(low, noon, "低角度太阳时应放大覆盖范围")


func test_shadow_disabled_below_cutoff() -> void:
	var main: Node3D = _make_world_instance()
	main._game_hour = main._sunrise

	main._update_lighting()
	assert_false(main._sun_light.shadow_enabled, "日出瞬间太阳高度角为 0，应关闭阴影")


func test_sunrise_smooth_energy_ramp() -> void:
	var main: Node3D = _make_world_instance()
	main._sunshine_intensity = 1.0
	main._game_hour = main._sunrise + (main._sunset - main._sunrise) * 0.02

	main._update_lighting()
	var full: float = 1.0 * 1.2
	assert_gt(main._sun_light.light_energy, 0.0, "日出后直射光应从 0 渐入")
	assert_lt(main._sun_light.light_energy, full, "日出初期直射光不应瞬间满能量")


func test_sunset_shadow_opacity_fades() -> void:
	var main: Node3D = _make_world_instance()
	main._game_hour = main._sunset - (main._sunset - main._sunrise) * 0.01

	main._update_lighting()
	assert_eq(main._sun_light.shadow_opacity, 0.0, "日落前阴影应淡出为 0")


func test_shadow_opacity_gradient_region() -> void:
	var main: Node3D = _make_world_instance()
	main._game_hour = main._sunrise + (main._sunset - main._sunrise) * 0.04

	main._update_lighting()
	var op: float = main._sun_light.shadow_opacity
	assert_gt(op, 0.0, "低角度渐变区阴影不应全透明")
	assert_lt(op, 1.0, "低角度渐变区阴影不应全不透明")


func test_ambient_smooth_at_sunrise() -> void:
	var main: Node3D = _make_world_instance()
	main._sunshine_intensity = 1.0
	main._game_hour = main._sunrise + (main._sunset - main._sunrise) * 0.02

	main._update_lighting()
	var env: Environment = main._world_env.environment
	assert_gt(env.ambient_light_energy, 0.5, "日出初期环境光应高于夜间最小值")
	assert_lt(env.ambient_light_energy, 1.0, "日出初期环境光不应瞬间满值")


func test_shadow_enabled_at_noon_with_tight_coverage() -> void:
	var main: Node3D = _make_world_instance()
	main._game_hour = (main._sunrise + main._sunset) * 0.5

	main._update_lighting()
	assert_true(main._sun_light.shadow_enabled, "正午应开启阴影")
	var expected: float = main._compute_shadow_coverage(1.0)
	assert_almost_eq(main._sun_light.directional_shadow_max_distance, expected, 0.01)


# ── 正交相机（阴影精度核心） ────────────────────────────────

func test_camera_is_orthographic_with_zoom_mapped_size() -> void:
	var main: Node3D = _make_world_instance()
	assert_eq(main._camera.projection, Camera3D.PROJECTION_ORTHOGONAL, "应为真正交投影")

	var expected: float = main.CAMERA_DISTANCE_DEFAULT * tan(deg_to_rad(main.CAMERA_FOV * 0.5))
	assert_almost_eq(main._camera.size, expected, 0.01, "size 应由相机距离映射")


func test_camera_slab_contains_visible_ground() -> void:
	var main: Node3D = _make_world_instance()
	main._camera_distance = 400.0
	main._apply_camera_transform()

	var half_perp: float = 400.0 * tan(deg_to_rad(main.CAMERA_FOV * 0.5))
	var elevation: float = asin(1.0 / sqrt(3.0))
	var ground_half: float = half_perp / sin(elevation)

	assert_lt(main._camera.near, 400.0 - ground_half, "近平面应在地面最近边之前")
	assert_gt(main._camera.far, 400.0 + ground_half, "远平面应覆盖地面最远边")
	assert_lt(main._camera.far, 1000.0, "远平面应紧凑：阴影范围 = 相机视锥，视锥越薄阴影越精")


func test_camera_slab_zoom_scales_range() -> void:
	var main: Node3D = _make_world_instance()
	main._camera_distance = 1200.0
	main._apply_camera_transform()
	var far_zoom: float = main._camera.far

	main._camera_distance = 60.0
	main._apply_camera_transform()
	var near_zoom: float = main._camera.far

	assert_gt(far_zoom, near_zoom, "拉远时视锥范围应扩大（阴影范围随之扩大）")


# ── 地形映射 ────────────────────────────────────────────────

func test_terrain_mapping_length() -> void:
	assert_eq(TerrainMeshBuilder.TERRAIN_TO_MESH.size(), 9, "应有 9 种地形类型映射")


func test_terrain_materials_use_vertex_colors() -> void:
	var main: Node3D = _make_world_instance()
	var mats: Dictionary = main._lazy_load_materials()
	assert_false(mats.is_empty(), "应加载到地形材质")
	for item_id: int in mats:
		var mat: StandardMaterial3D = mats[item_id]
		assert_true(mat.vertex_color_use_as_albedo,
			"地形材质应启用顶点色倍乘，否则 AO 顶点色不生效 (item_id=%d)" % item_id)


# ── Chunk 卸载 ──────────────────────────────────────────────

func test_unload_distant_chunks_removes_distant() -> void:
	var main: Node3D = _make_world_instance()

	var far_key := Vector2i(100, 100)
	main._loaded[far_key] = true
	main._chunks[far_key] = {"terrain": [], "elevation": []}
	main._pending[far_key] = true

	main._unload_distant_chunks(0, 0, 1)

	assert_false(main._loaded.has(far_key), "远处区块应卸载")
	assert_false(main._chunks.has(far_key), "远处区块数据应清除")
	assert_false(main._pending.has(far_key), "远处区块请求应取消")


func test_unload_preserves_nearby_chunks() -> void:
	var main: Node3D = _make_world_instance()

	var near_key := Vector2i(0, 0)
	main._loaded[near_key] = true
	main._chunks[near_key] = {"terrain": [], "elevation": []}

	main._unload_distant_chunks(0, 0, 2)

	assert_true(main._loaded.has(near_key), "近距离区块不应卸载")
	assert_true(main._chunks.has(near_key), "近距离区块数据应保留")
