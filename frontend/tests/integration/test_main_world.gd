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


func test_pause_menu_wired_with_terminal() -> void:
	"""暂停菜单应注入终端引用（终端打开时 ESC 归终端）。"""
	var main: Node3D = _make_world_instance()
	assert_eq(main._pause_menu._terminal, main._terminal,
		"ESC 分流依赖终端引用注入")


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
	# 与后端权威出生点约定一致：chunk 原点（非 chunk 中心）
	assert_eq(main._player_pos.x, 5.0 * CS)
	assert_eq(main._player_pos.z, 3.0 * CS)


func test_birth_chunk_only_set_once() -> void:
	var main: Node3D = _make_world_instance()

	main._set_birth_chunk(2, 2)
	assert_eq(main._birth_chunk, Vector2i(2, 2))
	main._set_birth_chunk(9, 9)
	assert_eq(main._birth_chunk, Vector2i(2, 2), "出生区块只应设置一次")


# ── 世界就绪事件（服务器/世界观解耦后的就绪信号） ─────────

func test_world_initialized_sets_birth_and_requests_state() -> void:
	"""world_initialized 事件：设置出生点并重新拉取权威玩家状态。

	回归：连接全程不断线后，entity_snapshot / player_state 原由
	_on_connected 触发，读档重建后必须由事件重新请求。
	"""
	var main: Node3D = _make_world_instance()

	main._on_world_initialized({"birth_chunk": [5, 3]})

	assert_true(main._has_birth)
	assert_eq(main._birth_chunk, Vector2i(5, 3))
	assert_eq(main._player_pos.x, 5.0 * CS)
	assert_false(main._world_visible, "地形未就绪前世界不可见")
	assert_true(main._loading_label.visible, "地形就绪前应保持加载提示")


func test_world_initialized_resets_previous_world_state() -> void:
	"""world_initialized 应清空旧世界的 chunk/地形（换世界读档）。"""
	var main: Node3D = _make_world_instance()
	main._set_birth_chunk(2, 2)
	main._chunks[Vector2i(0, 0)] = {"elevation": []}
	main._loaded[Vector2i(0, 0)] = true
	main._pending[Vector2i(1, 1)] = true

	main._on_world_initialized({"birth_chunk": [8, 8]})

	assert_eq(main._birth_chunk, Vector2i(8, 8), "应切到新世界的出生点")
	assert_true(main._chunks.is_empty(), "旧世界 chunk 数据应清空")
	assert_true(main._loaded.is_empty(), "旧世界地形标记应清空")
	assert_true(main._pending.is_empty(), "旧世界在途请求应清空")


func test_terrain_ready_shows_world_after_neighborhood_loaded() -> void:
	"""出生点 3×3 邻域全部加载后：隐藏加载提示并显示玩家。"""
	var main: Node3D = _make_world_instance()
	main._set_birth_chunk(4, 4)

	assert_false(main._world_visible)
	assert_true(main._loading_label.visible, "地形加载中应显示加载提示")

	# 只加载部分邻域 → 仍不可见
	main._loaded[Vector2i(3, 3)] = true
	main._check_terrain_ready()
	assert_false(main._world_visible, "邻域未全加载前不可见")

	# 补全 3×3 邻域 → 就绪
	for dx in range(-1, 2):
		for dy in range(-1, 2):
			main._loaded[Vector2i(4 + dx, 4 + dy)] = true
	main._check_terrain_ready()

	assert_true(main._world_visible, "邻域加载完成后世界可见")
	assert_false(main._loading_label.visible, "就绪后应隐藏加载提示")
	assert_true(main._player != null, "就绪后应创建玩家节点")
	assert_true(main._player.visible, "就绪后玩家应可见")


func test_terrain_ready_force_timeout_shows_world() -> void:
	"""超时兜底：区块永不就绪时强制显示世界。"""
	var main: Node3D = _make_world_instance()
	main._set_birth_chunk(1, 1)

	main._check_terrain_ready(true)

	assert_true(main._world_visible, "超时兜底应强制就绪")
	assert_false(main._loading_label.visible)
	assert_true(main._player.visible)


func test_world_reloading_resets_state_and_shows_label() -> void:
	"""world_reloading 事件：复位世界状态并显示加载提示。"""
	var main: Node3D = _make_world_instance()
	main._set_birth_chunk(2, 2)
	main._chunks[Vector2i(0, 0)] = {"elevation": []}
	main._loaded[Vector2i(0, 0)] = true

	main._on_world_reloading({"world_id": "next"})

	assert_false(main._has_birth, "重建开始后出生点应复位")
	assert_true(main._chunks.is_empty(), "旧世界数据应清空")
	assert_true(main._loading_label.visible, "重建期间应显示加载提示")


func test_reset_world_state_is_idempotent() -> void:
	"""连续复位（reloading → initialized）不应报错。"""
	var main: Node3D = _make_world_instance()
	main._set_birth_chunk(0, 0)
	main._reset_world_state()
	main._reset_world_state()
	assert_false(main._has_birth)


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


# ── 请求状态管理（_pending / _batch_pending 分离） ─────────

func test_field_only_response_keeps_tile_pending() -> void:
	"""字段响应（无 terrain）不应清除 tile 请求的 _pending 标记。

	回归：旧实现无条件 _pending.erase(key)，导致 tile 请求标记被抹掉、
	每帧重发 include_tiles=true 请求，直到 tile 响应到达。

	注：须先设置出生点（world_initialized 事件驱动语义），
	使响应走正常流式路径而非"世界未就绪"分支。
	"""
	var main: Node3D = _make_world_instance()
	var key := Vector2i(0, 0)
	main._set_birth_chunk(0, 0)
	main._pending[key] = true
	main._batch_pending[key] = true

	main._handle_response({
		"type": "response",
		"request_type": "get_chunks",
		"payload": {
			"chunks": [{"cx": 0, "cy": 0, "temperature": 20.0}],
			"include_tiles": false,
		},
	})

	assert_true(main._pending.has(key), "tile 请求标记应保留")
	assert_false(main._batch_pending.has(key), "字段请求标记应清除")


func test_tile_response_clears_tile_pending() -> void:
	"""含 terrain 的响应清除 tile 请求标记。"""
	var main: Node3D = _make_world_instance()
	var key := Vector2i(0, 0)
	main._set_birth_chunk(0, 0)
	main._pending[key] = true
	main._loaded[key] = true  # 预标记已加载，跳过网格构建，只验证状态

	var elev: Array = []
	elev.resize(CS * CS)
	elev.fill(10.0)
	var terr: Array = []
	terr.resize(CS * CS)
	terr.fill(2)

	main._handle_response({
		"type": "response",
		"request_type": "get_chunks",
		"payload": {
			"chunks": [{"cx": 0, "cy": 0, "terrain": terr, "elevation": elev}],
			"include_tiles": true,
		},
	})

	assert_false(main._pending.has(key), "tile 请求标记应清除")


func test_disconnect_clears_pending_state() -> void:
	"""断线后应清空在途请求状态，重连后地形块才能重新请求。

	回归：旧实现断线不清 _pending，重连后这些 chunk 永远被跳过，
	玩家周围地形永久缺失。
	"""
	var main: Node3D = _make_world_instance()
	var key := Vector2i(3, 2)
	main._pending[key] = true
	main._batch_pending[key] = true
	main._tile_queue[key] = true

	main._on_disconnected()

	assert_true(main._pending.is_empty(), "断线后 tile 请求应清空")
	assert_true(main._batch_pending.is_empty(), "断线后字段请求应清空")
	assert_true(main._tile_queue.is_empty(), "断线后 tile 队列应清空")


# ── 权威位置（player_state / player_move / entity_snapshot） ──

func test_world_initialized_records_world_id() -> void:
	"""world_initialized 事件应记录当前存档位 ID（手动存档数据源）。"""
	var main: Node3D = _make_world_instance()

	main._on_world_initialized({"birth_chunk": [5, 3], "world_id": "w-abc"})

	assert_eq(main._world_id, "w-abc")


func test_world_initialized_keeps_world_id_when_missing() -> void:
	"""事件缺 world_id（旧后端）时保留已有值。"""
	var main: Node3D = _make_world_instance()
	main._world_id = "w-keep"

	main._on_world_initialized({"birth_chunk": [1, 1]})

	assert_eq(main._world_id, "w-keep", "缺字段时应保持原值（弱后端容错）")


func test_world_reloading_records_world_id() -> void:
	"""world_reloading 事件应更新目标存档位 ID（切档场景）。"""
	var main: Node3D = _make_world_instance()
	main._world_id = "w-old"

	main._on_world_reloading({"world_id": "w-next"})

	assert_eq(main._world_id, "w-next")


# ── 手动存档（暂停菜单） ────────────────────────────────────

func test_pause_save_without_world_id_rejected() -> void:
	"""世界未就绪（无 world_id）时手动存档应拒绝并提示。"""
	var main: Node3D = _make_world_instance()

	main._on_pause_save_requested()

	assert_string_contains(main._pause_menu._status_text, "无法手动存档")


func test_pause_save_sends_snapshot_request() -> void:
	"""世界就绪时手动存档应发出 save_snapshot 请求（payload 携带 world_id）。"""
	var main: Node3D = _make_world_instance()
	main._world_id = "w-abc"

	main._pause_menu._activate("save")

	assert_false(main._pause_menu._status_text.contains("无法手动存档"))
	assert_true(main._pause_menu._saving, "请求在途期间应处于存档中状态")


func test_save_snapshot_response_queues_number_lookup() -> void:
	"""save_snapshot 响应：记下文件并回查 save_list（菜单保持正在保存）。"""
	var main: Node3D = _make_world_instance()
	main._world_id = "w-abc"
	main._pause_menu._saving = true

	main._handle_response({
		"type": "response",
		"request_type": "save_snapshot",
		"payload": {"file": "snap-1.ascendsave"},
	})

	assert_eq(main._save_file, "snap-1.ascendsave", "应暂存待编号的快照")
	assert_true(main._pause_menu._saving, "编号回查期间应保持正在保存")


func test_save_list_lookup_reports_node_number() -> void:
	"""save_list 回查：按保存顺序计算节点编号并回填菜单。"""
	var main: Node3D = _make_world_instance()
	main._world_id = "w-abc"
	main._save_file = "snap-new"

	main._handle_response({
		"type": "response",
		"request_type": "save_list",
		"payload": {"snapshots": [
			{"world_id": "w-abc", "file": "snap-1", "saved_at": 100.0, "suffix": "manual"},
			{"world_id": "w-abc", "file": "snap-new", "saved_at": 200.0, "suffix": "manual"},
			{"world_id": "w-abc", "file": "snap-2", "saved_at": 300.0, "suffix": "auto"},
			{"world_id": "other", "file": "snap-x", "saved_at": 50.0, "suffix": "manual"},
		]},
	})

	assert_string_contains(main._pause_menu._status_text, "节点 2",
		"应按保存顺序编号（其它世界/来源不参与）")
	assert_eq(main._save_file, "", "回查完成后应清空暂存")
	assert_false(main._pause_menu._saving)


func test_save_number_lookup_fallback_without_number() -> void:
	"""快照不在列表中（异常）时显示保存完成而不带编号。"""
	var main: Node3D = _make_world_instance()
	main._world_id = "w-abc"
	main._save_file = "snap-ghost"

	main._handle_response({
		"type": "response",
		"request_type": "save_list",
		"payload": {"snapshots": []},
	})

	assert_eq(main._pause_menu._status_text, "保存完成", "未找到时不应显示节点号")
	assert_eq(main._save_file, "", "回查完成后应清空暂存")


func test_save_snapshot_error_clears_lookup() -> void:
	"""save_snapshot 错误：清空待编号状态并提示失败。"""
	var main: Node3D = _make_world_instance()
	main._save_file = "snap-pending"

	main._handle_error({
		"type": "error",
		"request_type": "save_snapshot",
		"error": "缺少 world_id",
	})
	assert_push_error("缺少 world_id")

	assert_string_contains(main._pause_menu._status_text, "存档失败")
	assert_eq(main._save_file, "", "失败后应清空待编号状态")


func test_save_snapshot_error_updates_pause_menu() -> void:
	"""save_snapshot 错误响应应回填暂停菜单（失败提示）。"""
	var main: Node3D = _make_world_instance()

	main._handle_error({
		"type": "error",
		"request_type": "save_snapshot",
		"error": "缺少 world_id",
	})
	assert_push_error("缺少 world_id")

	assert_string_contains(main._pause_menu._status_text, "存档失败")


func test_player_state_sets_entity_id_and_position() -> void:
	var main: Node3D = _make_world_instance()
	main._set_birth_chunk(0, 0)

	main._handle_response({
		"type": "response",
		"request_type": "player_state",
		"payload": {"entity_id": "abc123", "x": 123.0, "y": 456.0},
	})

	assert_eq(main._player_entity_id, "abc123")
	assert_eq(main._player_pos.x, 123.0)
	assert_eq(main._player_pos.z, 456.0)


func test_player_move_response_applies_authoritative_position() -> void:
	var main: Node3D = _make_world_instance()

	main._handle_response({
		"type": "response",
		"request_type": "player_move",
		"payload": {"x": 10.0, "y": 20.0},
	})

	assert_eq(main._player_pos.x, 10.0, "越界钳制后的权威位置应被采纳")
	assert_eq(main._player_pos.z, 20.0)


func test_entity_snapshot_consumes_player_entity() -> void:
	var main: Node3D = _make_world_instance()

	main._handle_response({
		"type": "response",
		"request_type": "entity_snapshot",
		"payload": {"entities": [
			{"id": "npc1", "controller": "AI", "x": 1.0, "y": 2.0},
			{"id": "plr1", "controller": "PLAYER", "x": 30.0, "y": 40.0},
		]},
	})

	assert_eq(main._player_entity_id, "plr1", "应识别 controller=PLAYER 的实体")
	assert_eq(main._player_pos.x, 30.0)
	assert_eq(main._player_pos.z, 40.0)
