extends GutTest

const Config = preload("res://scripts/config.gd")
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
	assert_eq(stats["placing"], 0)
	assert_eq(stats["cached"], 0)
	assert_eq(stats["pending"], 0)


func test_debug_timing_has_expected_keys() -> void:
	var main: Node3D = _make_world_instance()

	var timing: Dictionary = main.get_debug_timing()
	assert_true(timing.has("stream"))
	assert_true(timing.has("place"))
	assert_true(timing.has("erase"))
	assert_true(timing.has("conn"))


# ── 流式半径 ────────────────────────────────────────────────

func test_stream_radius_minimum() -> void:
	var main: Node3D = _make_world_instance()
	main._camera_distance = main.CAMERA_DISTANCE_MIN

	var r: int = main._stream_radius()
	assert_gt(r, 0, "半径应 > 0")


# ── 地形映射 ────────────────────────────────────────────────

func test_terrain_mapping_length() -> void:
	var main: Node3D = _make_world_instance()
	assert_eq(main.TERRAIN_TO_MESH.size(), 9, "应有 9 种地形类型映射")


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
