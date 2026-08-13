extends GutTest

const Config = preload("res://scripts/config.gd")
const TerrainMeshBuilder = preload("res://scripts/world/terrain_mesh_builder.gd")
const CS: int = Config.TILE_MAP_SIZE
const Fakes = preload("res://tests/fakes/connection_layers.gd")

var _real_process: Object
var _real_transport: Object
var _real_handshake: Object
var _real_worker: Object


## 注入假层：本文件只驱动 send 队列（白盒 readback），不能有真实流量
## 流到运行中的后端（旧代码靠 disconnect 测试的 set_process(false) 冻结
## 真实处理链，注入了假层后由 _process 继续无副作用驱动）。
func before_each() -> void:
	# 断言中文文案：固定 zh_CN，与用户设置文件 locale 解耦
	TranslationServer.set_locale("zh_CN")
	if _real_process == null:
		_real_process = Connection._process_layer
		_real_transport = Connection._transport
		_real_handshake = Connection._handshake
		_real_worker = Connection._worker
	Connection._set_layers(
		Fakes.FakeProcess.new(), Fakes.FakeTransport.new(),
		Fakes.FakeHandshake.new(), Fakes.FakeWorker.new())


func after_each() -> void:
	if _real_process != null:
		Connection._set_layers(_real_process, _real_transport, _real_handshake, _real_worker)


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
	# 白盒：模拟已连接（_stream_chunks 的 gate 判定），不 stub 网络层
	Connection.status = Connection.Status.CONNECTED
	Connection._force_handshake_acked()
	# 禁用帧处理：测试全部显式调用方法；否则残留实例的 _process 会在
	# 帧间继续发请求，污染共享 Connection 发送队列（回归：队列里
	# 出现前一个测试实例发出的完整请求）。
	instance.process_mode = Node.PROCESS_MODE_DISABLED
	# 注入同步网格构建器：构建结果立即入队（_poll_build_results 挂载），
	# 测试无需等待后台线程，断言与旧同步语义一致。契约与异步任务相同：
	# 入队纯数据（build_data），由 _poll_build_results 在主线程组 mesh。
	instance.mesh_builder = func(key, terr, elev, seq):
		instance._build_results.append([key, TerrainMeshBuilder.build_data(terr, elev), seq])
	return instance as Node3D


## 读取并清空 Connection 发送队列中的 get_chunks 请求，返回 include_tiles 序列。
## 白盒：握手置位后 send() 才入队（见 connection.gd send），
## 不 stub 网络层（GUT 的 stub 只接受 double 实例，autoload 无法 stub）。
func _drain_chunk_requests() -> Array:
	Connection._force_handshake_acked()
	var out: Array = []
	for framed in Connection._drain_pending_frames():
		var decoded: Dictionary = Connection._codec.frame_decode(framed, Config.MAX_MESSAGE_SIZE)
		for body in decoded["bodies"]:
			var msg: Dictionary = JsonCodec.decode(body)
			if msg.get("request_type", "") == "get_chunks":
				out.append(bool(msg["payload"]["include_tiles"]))
	return out


## 构造与后端 BLOB 布局一致的 tiles_b64（4B header + uint16 LE terrain + float32 LE elevation + slope）。
func _make_tiles_b64(terr: PackedInt32Array, elev: PackedFloat32Array) -> String:
	var raw := PackedByteArray()
	raw.resize(4)
	for t in terr:
		raw.append(t & 0xFF)
		raw.append((t >> 8) & 0xFF)
	for e in elev:
		var tmp := PackedByteArray()
		tmp.resize(4)
		tmp.encode_float(0, e)  # 小端 float32（与后端 TileGrid.to_bytes 一致）
		raw.append_array(tmp)
	for e in elev:
		var tmp := PackedByteArray()
		tmp.resize(4)
		tmp.encode_float(0, e)
		raw.append_array(tmp)
	return Marshalls.raw_to_base64(raw)


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
	assert_true(main._loading_overlay.visible, "地形就绪前应保持加载提示")


func test_world_initialized_resets_previous_world_state() -> void:
	"""world_initialized 应清空旧世界的 chunk/地形（换世界读档）。"""
	var main: Node3D = _make_world_instance()
	main._set_birth_chunk(2, 2)
	main._chunks[Vector2i(0, 0)] = {"elevation": []}
	main._stream_machine.mark_built(Vector2i(0, 0))
	main._stream_machine.collect_field_requests(Vector2i(1, 1), 0)
	main._stream_machine.select_full_requests(func(k): return true, 10)

	main._on_world_initialized({"birth_chunk": [8, 8]})

	assert_eq(main._birth_chunk, Vector2i(8, 8), "应切到新世界的出生点")
	assert_true(main._chunks.is_empty(), "旧世界 chunk 数据应清空")
	assert_eq(main._stream_machine.size(), 0, "旧世界 chunk 状态应清空")


func test_terrain_ready_shows_world_after_neighborhood_loaded() -> void:
	"""出生点 3×3 邻域全部加载后：隐藏加载提示并显示玩家。"""
	var main: Node3D = _make_world_instance()
	main._set_birth_chunk(4, 4)

	assert_false(main._world_visible)
	assert_true(main._loading_overlay.visible, "地形加载中应显示加载提示")

	# 只加载部分邻域 → 仍不可见
	main._stream_machine.mark_built(Vector2i(3, 3))
	main._check_terrain_ready()
	assert_false(main._world_visible, "邻域未全加载前不可见")

	# 补全 3×3 邻域 → 就绪：进度条进入补满动画，补满前世界不可见
	for dx in range(-1, 2):
		for dy in range(-1, 2):
			main._stream_machine.mark_built(Vector2i(4 + dx, 4 + dy))
	main._check_terrain_ready()

	assert_false(main._world_visible, "进度条未补满前世界不可见（等待补满动画）")
	assert_true(main._loading_overlay.visible, "补满动画期间加载层保持显示")

	# 推进补满动画到 100% → completed 信号 → 显示世界与玩家
	main._loading_overlay._process(10.0)

	assert_true(main._world_visible, "进度条补满后世界可见")
	assert_false(main._loading_overlay.visible, "就绪后应隐藏加载提示")
	assert_true(main._player != null, "就绪后应创建玩家节点")
	assert_true(main._player.visible, "就绪后玩家应可见")


func test_terrain_ready_force_timeout_shows_world() -> void:
	"""超时兜底：区块永不就绪时强制显示世界（同样先补满进度条）。"""
	var main: Node3D = _make_world_instance()
	main._set_birth_chunk(1, 1)

	main._check_terrain_ready(true)

	assert_false(main._world_visible, "超时兜底也应先补满进度条收尾")
	assert_true(main._loading_overlay.visible, "补满收尾期间加载层保持显示")

	# 推进补满动画到 100% → completed 信号 → 显示世界与玩家
	main._loading_overlay._process(10.0)

	assert_true(main._world_visible, "补满后超时兜底应强制就绪")
	assert_false(main._loading_overlay.visible)
	assert_true(main._player != null)
	assert_true(main._player.visible)


func test_completion_fallback_forces_world_visible() -> void:
	"""completed 信号异常（覆盖层隐藏/销毁）时兜底计时器强制收尾。

	正常路径：覆盖层补满 → completed → _finish_world_visible。若覆盖层
	异常导致信号永不发出，COMPLETION_FALLBACK_SEC 兜底计时器随
	main._process 倒计时归零后强制收尾，防玩家永久卡在加载层。
	"""
	var main: Node3D = _make_world_instance()
	main._set_birth_chunk(0, 0)

	# 就绪并进入补满收尾；模拟覆盖层异常：隐藏 → _process 停走 → 信号不发
	main._check_terrain_ready(true)
	assert_false(main._world_visible, "补满收尾未完成前世界不可见")
	main._loading_overlay.visible = false

	# 兜底计时器接近归零，main._process 推进后强制收尾
	main._completion_fallback_sec = 0.01
	main._process(0.05)

	assert_true(main._world_visible, "兜底计时器归零应强制收尾")
	assert_true(main._player != null, "强制收尾也应创建玩家")
	assert_true(main._player.visible)


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
	main._stream_machine.mark_built(far_key)
	main._chunks[far_key] = {"terrain": [], "elevation": []}
	main._stream_machine.collect_field_requests(Vector2i(101, 101), 0)
	main._stream_machine.select_full_requests(func(k): return true, 10)

	main._unload_distant_chunks(0, 0, 1)

	assert_eq(main._stream_machine.get_state(far_key), main.ChunkState.UNKNOWN, "远处区块应卸载")
	assert_false(main._chunks.has(far_key), "远处区块数据应清除")
	assert_eq(main._stream_machine.get_state(Vector2i(101, 101)), main.ChunkState.UNKNOWN, "远处区块在途请求应作废")


func test_unload_preserves_nearby_chunks() -> void:
	var main: Node3D = _make_world_instance()

	var near_key := Vector2i(0, 0)
	main._stream_machine.mark_built(near_key)
	main._chunks[near_key] = {"terrain": [], "elevation": []}

	main._unload_distant_chunks(0, 0, 2)

	assert_eq(main._stream_machine.get_state(near_key), main.ChunkState.BUILT, "近距离区块不应卸载")
	assert_true(main._chunks.has(near_key), "近距离区块数据应保留")


# ── 请求状态机（UNKNOWN → FIELD_REQUESTED → TILE_REQUESTED → RECEIVED → BUILT） ──

func test_field_only_response_stores_data_keeps_field_requested() -> void:
	"""字段响应：缓存数据，状态保持 FIELD_REQUESTED（等待流循环发完整请求）。

	防护：字段响应不得清 _pending 标记——否则完整请求标记被抹掉、
	每帧重发 include_tiles=true 请求直到响应到达（双请求竞态）。
	"""
	var main: Node3D = _make_world_instance()
	var key := Vector2i(0, 0)
	main._set_birth_chunk(0, 0)
	main._stream_machine.collect_field_requests(key, 0)  # → FIELD_REQUESTED

	main._handle_response({
		"type": "response",
		"request_type": "get_chunks",
		"payload": {
			"chunks": [{"cx": 0, "cy": 0, "temperature": 20.0}],
			"include_tiles": false,
		},
	})

	assert_eq(main._stream_machine.get_state(key), main.ChunkState.FIELD_REQUESTED,
		"字段响应后应保持 FIELD_REQUESTED，由流循环限流发完整请求")
	assert_true(main._chunks[key] is Dictionary, "字段数据应已缓存")
	assert_eq(float(main._chunks[key]["temperature"]), 20.0)


func test_tile_response_marks_received() -> void:
	"""完整响应：解码并构建（→ BUILT）。"""
	var main: Node3D = _make_world_instance()
	var key := Vector2i(0, 0)
	main._set_birth_chunk(0, 0)
	main._stream_machine.collect_field_requests(key, 0)
	main._stream_machine.select_full_requests(func(k): return true, 10)  # → TILE_REQUESTED

	var elev := PackedFloat32Array()
	elev.resize(CS * CS)
	elev.fill(10.0)
	var terr := PackedInt32Array()
	terr.resize(CS * CS)
	terr.fill(2)

	main._handle_response({
		"type": "response",
		"request_type": "get_chunks",
		"payload": {
			"chunks": [{"cx": 0, "cy": 0, "tiles_b64": _make_tiles_b64(terr, elev)}],
			"include_tiles": true,
		},
	})

	assert_eq(main._stream_machine.get_state(key), main.ChunkState.BUILT,
		"完整数据到达且材质就绪应立即构建（BUILT）")


func test_stale_response_after_unload_ignored() -> void:
	"""卸载后到达的陈旧响应应被忽略（不复活 chunk）。"""
	var main: Node3D = _make_world_instance()
	var key := Vector2i(9, 9)
	main._set_birth_chunk(0, 0)

	main._handle_response({
		"type": "response",
		"request_type": "get_chunks",
		"payload": {
			"chunks": [{"cx": 9, "cy": 9, "temperature": 1.0}],
			"include_tiles": false,
		},
	})

	assert_eq(main._stream_machine.get_state(key), main.ChunkState.UNKNOWN,
		"UNKNOWN（已卸载）的陈旧响应应忽略")
	assert_false(main._chunks.has(key), "陈旧响应不应写入缓存")


func test_disconnect_demotes_inflight_states() -> void:
	"""断线后：在途请求作废（无数据 → UNKNOWN），有数据降级重发完整请求。

	防护：断线必须清 _pending——否则重连后这些 chunk 永远被跳过，
	玩家周围地形永久缺失。
	"""
	var main: Node3D = _make_world_instance()

	# 字段在途（无数据）→ UNKNOWN
	var inflight_key := Vector2i(3, 2)
	main._stream_machine.collect_field_requests(inflight_key, 0)
	main._chunks[inflight_key] = null
	# 完整请求在途（已有字段数据）→ 保留数据降级 FIELD_REQUESTED
	var tile_key := Vector2i(4, 2)
	main._chunks[tile_key] = {"temperature": 25.0}
	main._stream_machine.collect_field_requests(tile_key, 0)
	main._stream_machine.select_full_requests(func(k): return k == tile_key, 10)
	# 已接收/已构建 → 保留
	var received_key := Vector2i(5, 2)
	main._stream_machine.on_full_response(received_key, true)
	main._chunks[received_key] = {"temperature": 26.0}
	var built_key := Vector2i(6, 2)
	main._stream_machine.mark_built(built_key)

	main._on_disconnected()

	assert_eq(main._stream_machine.get_state(inflight_key), main.ChunkState.UNKNOWN, "字段在途应作废为 UNKNOWN")
	assert_false(main._chunks.has(inflight_key), "字段在途数据应清除")
	assert_eq(main._stream_machine.get_state(tile_key), main.ChunkState.FIELD_REQUESTED,
		"完整请求在途应降级为 FIELD_REQUESTED（重连后重发完整请求）")
	assert_true(main._chunks[tile_key] is Dictionary, "已有字段数据应保留")
	assert_eq(main._stream_machine.get_state(received_key), main.ChunkState.RECEIVED,
		"已接收数据应保留（重连后恢复构建）")
	assert_eq(main._stream_machine.get_state(built_key), main.ChunkState.BUILT,
		"已构建 chunk 应保留")


func test_fast_movement_keeps_inflight_chunks() -> void:
	"""快速移动：在途请求的 chunk 不应被卸载圈立即作废（白请求）。

	回归：玩家快速移动时，请求发出 → 玩家跑出卸载圈（stream_r+1）→
	chunk 被卸载 → 响应到达被当陈旧丢弃 → 重新请求 → 又跑出圈……
	前方区块永远加载不出来（日志：一帧内卸载 13 个 chunk，含距出生
	点仅 1 格的 chunk）。
	"""
	var main: Node3D = _make_world_instance()
	main._set_birth_chunk(0, 0)
	main._player_pos = Vector3(0, 0, 0)

	# 帧 1：玩家在 (0,0)，3×3 请求在途（含 (1,1)）
	main._stream_chunks()
	_drain_chunk_requests()
	assert_eq(main._stream_machine.get_state(Vector2i(1, 1)), main.ChunkState.FIELD_REQUESTED)

	# 玩家快速移动到 (4,4)：卸载判定以新位置为中心
	main._player_pos = Vector3(4.0 * CS, 0, 4.0 * CS)
	main._stream_chunks()
	_drain_chunk_requests()

	# (1,1) 距新中心 3 格——应在卸载缓冲圈内（响应仍会到达）
	assert_eq(main._stream_machine.get_state(Vector2i(1, 1)), main.ChunkState.FIELD_REQUESTED,
		"在途请求不应因玩家快速移动被作废（白请求 → 区块加载不出来）")

	# 响应到达：数据应被缓存而非丢弃
	main._handle_response({
		"type": "response", "request_type": "get_chunks",
		"payload": {"chunks": [{"cx": 1, "cy": 1, "temperature": 20.0}], "include_tiles": false},
	})
	assert_true(main._chunks.has(Vector2i(1, 1)), "缓冲圈内的在途响应不应被丢弃")


func test_single_chunk_two_requests_from_enter_to_built() -> void:
	"""集成场景：chunk 从进入视野到构建完成恰好 2 次请求（字段 + 完整）。

	防护：字段请求在途时 tile 队列分支不得同样命中（_chunks 以 null
	占位已存在）——否则同一 chunk 并发发出 字段+完整 双请求。
	"""
	var main: Node3D = _make_world_instance()
	main._set_birth_chunk(0, 0)

	var key := Vector2i(0, 0)
	main._player_pos = Vector3(0, 0, 0)

	# 帧 1：UNKNOWN → 字段请求（1 次，false）
	main._stream_chunks()
	var requests: Array = _drain_chunk_requests()
	assert_eq(requests.size(), 1, "首帧应恰好发 1 次字段请求")
	assert_false(requests[0], "首帧应为字段版请求")
	assert_eq(main._stream_machine.get_state(key), main.ChunkState.FIELD_REQUESTED)

	# 字段响应到达（此后流循环不得重复发字段请求）
	main._handle_response({
		"type": "response", "request_type": "get_chunks",
		"payload": {"chunks": [{"cx": 0, "cy": 0, "temperature": 20.0}], "include_tiles": false},
	})
	main._stream_chunks()
	main._stream_chunks()
	requests = _drain_chunk_requests()
	assert_eq(requests.size(), 1, "字段响应后仅发 1 次完整请求，不重发字段请求")
	assert_true(requests[0], "第二帧应为完整版请求")
	assert_eq(main._stream_machine.get_state(key), main.ChunkState.TILE_REQUESTED)

	# 完整响应到达 → RECEIVED → 构建 → BUILT（不再有请求）
	var elev := PackedFloat32Array()
	elev.resize(CS * CS)
	elev.fill(10.0)
	var terr := PackedInt32Array()
	terr.resize(CS * CS)
	terr.fill(2)
	main._handle_response({
		"type": "response", "request_type": "get_chunks",
		"payload": {
			"chunks": [{"cx": 0, "cy": 0, "tiles_b64": _make_tiles_b64(terr, elev)}],
			"include_tiles": true,
		},
	})
	main._stream_chunks()
	requests = _drain_chunk_requests()
	assert_eq(requests.size(), 0, "构建完成后不应再有请求")
	assert_eq(main._stream_machine.get_state(key), main.ChunkState.BUILT)


func test_received_chunk_builds_after_stream_cycle() -> void:
	"""RECEIVED（完整数据已到）→ 流循环 → BUILT，且不再发网络请求。"""
	var main: Node3D = _make_world_instance()
	main._set_birth_chunk(0, 0)
	var key := Vector2i(0, 0)
	# 直接注入已解码数据（模拟完整响应路径到达 RECEIVED）
	var elev := PackedFloat32Array()
	elev.resize(CS * CS)
	elev.fill(10.0)
	var terr := PackedInt32Array()
	terr.resize(CS * CS)
	terr.fill(2)
	main._chunks[key] = {"terrain": terr, "elevation": elev}
	main._stream_machine.on_full_response(key, true)

	main._stream_chunks()
	main._stream_chunks()
	assert_eq(main._stream_machine.get_state(key), main.ChunkState.BUILT,
		"构建完成的 chunk 不应被打回请求态（结构性无双请求）")


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


# ── 权威位置容差（SNAP_TOLERANCE） ─────────────────────────

func test_player_move_small_delta_keeps_local_position() -> void:
	"""权威位置与本地差距小于容差（RTT 内继续移动的距离）→ 不吸附。

	防护：小差距不得无条件吸附——否则每 0.2s 一次回跳（橡皮筋）。
	"""
	var main: Node3D = _make_world_instance()
	main._player_pos = Vector3(10.0, 0.0, 20.0)

	main._handle_response({
		"type": "response",
		"request_type": "player_move",
		"payload": {"x": 10.5, "y": 20.0},
	})

	assert_eq(main._player_pos.x, 10.0, "0.5 tile 预测误差不应吸附")
	assert_eq(main._player_pos.z, 20.0)


func test_player_move_clamped_position_snapped() -> void:
	"""权威差距超阈值（真钳制/传送）→ 平滑过渡到权威位置，后端权威保留。

	防护：超阈值差距不得直接瞬跳吸附（每 0.2s 一次回跳的橡皮筋）；
	中等差距走 SNAP_DURATION 平滑过渡。
	"""
	var main: Node3D = _make_world_instance()
	main._player_pos = Vector3(10.0, 0.0, 20.0)

	main._handle_response({
		"type": "response",
		"request_type": "player_move",
		"payload": {"x": 5.0, "y": 20.0},
	})

	assert_true(main._snap_time >= 0.0, "中等差距（5 tiles）应启动平滑过渡")
	assert_eq(main._snap_target.x, 5.0, "过渡目标应为权威位置")
	assert_eq(main._player_pos.x, 10.0, "过渡启动瞬间不应瞬跳")

	# 推进过渡：半程应位于起点与目标之间
	main._process_snap(0.075)
	assert_almost_eq(main._player_pos.x, 7.5, 0.5, "半程应接近中点")
	# 推进到完成：应到达权威位置并清除过渡
	main._process_snap(0.075)
	assert_eq(main._player_pos.x, 5.0, "过渡完成后应到达权威位置")
	assert_true(main._snap_time < 0.0, "过渡完成后应清除状态")


func test_player_move_large_delta_snaps_immediately() -> void:
	"""差距 ≥ SNAP_HARD_THRESHOLD（传送/读档复位）→ 立即硬吸。"""
	var main: Node3D = _make_world_instance()
	main._player_pos = Vector3(10.0, 0.0, 20.0)

	main._handle_response({
		"type": "response",
		"request_type": "player_move",
		"payload": {"x": 1.0, "y": 2.0},
	})

	assert_eq(main._player_pos.x, 1.0, "大差距（>8 tiles）应直接吸附")
	assert_eq(main._player_pos.z, 2.0)
	assert_true(main._snap_time < 0.0, "硬吸不应进入过渡状态")


func test_snap_transition_restarts_on_new_authority() -> void:
	"""过渡期间新权威响应到达 → 从当前位置重新过渡（平滑连续纠正）。"""
	var main: Node3D = _make_world_instance()
	main._player_pos = Vector3(10.0, 0.0, 20.0)
	main._handle_response({
		"type": "response", "request_type": "player_move",
		"payload": {"x": 5.0, "y": 20.0},
	})
	main._process_snap(0.05)  # 过渡进行中（当前位置 ≈ 8.3）

	main._handle_response({
		"type": "response", "request_type": "player_move",
		"payload": {"x": 4.0, "y": 20.0},
	})

	assert_almost_eq(main._snap_from.x, main._player_pos.x, 0.01,
		"新过渡应从当前插值位置开始（无跳变）")
	assert_eq(main._snap_target.x, 4.0, "新过渡目标应为最新权威位置")


func test_snap_transition_then_input_applies() -> void:
	"""过渡期间输入照常叠加：位置 = 插值 + 输入位移（不吃移动速度）。"""
	var main: Node3D = _make_world_instance()
	main._player_pos = Vector3(10.0, 0.0, 20.0)
	main._handle_response({
		"type": "response", "request_type": "player_move",
		"payload": {"x": 5.0, "y": 20.0},
	})
	main._snap_time = 0.0
	main._snap_from = main._player_pos
	main._snap_target = Vector3(5.0, 0.0, 20.0)

	main._process_snap(0.075)
	var mid: float = main._player_pos.x
	Input.action_press("move_right")
	main._process_input(1.0 / 60.0)
	Input.action_release("move_right")

	assert_gt(main._player_pos.x, mid, "输入位移应叠加在插值之上")


# ── 服务器对账（权威位移 vs 上报位移） ─────────────────────

func test_player_move_follows_reported_keeps_local() -> void:
	"""后端原样回显上报（回声，seq 对齐）→ 零纠正。

	防护：按差距吸附会把"滞后"误当"偏离"（每 0.2s 拉回一次）；
	差距 3 tiles（> SNAP_TOLERANCE）时同样不应纠正。
	"""
	var main: Node3D = _make_world_instance()
	main._player_pos = Vector3(109.0, 0.0, 100.0)
	main._report_seq_pos = {5: Vector2(106.0, 100.0)}

	main._handle_response({
		"type": "response", "request_type": "player_move",
		"payload": {"x": 106.0, "y": 100.0, "seq": 5},
	})

	assert_eq(main._player_pos.x, 109.0, "后端跟随上报（滞后）时不应拉回")
	assert_true(main._snap_time < 0.0, "不应进入过渡")
	assert_false(main._report_seq_pos.has(5), "已响应的 seq 记录应被消费")


func test_player_move_reverse_follows_reported_keeps_local() -> void:
	"""掉头移动后端同样原样回显（方向无关）→ 零纠正。"""
	var main: Node3D = _make_world_instance()
	main._player_pos = Vector3(103.0, 0.0, 100.0)
	main._report_seq_pos = {6: Vector2(100.0, 100.0)}

	main._handle_response({
		"type": "response", "request_type": "player_move",
		"payload": {"x": 100.0, "y": 100.0, "seq": 6},
	})

	assert_eq(main._player_pos.x, 103.0, "掉头跟随也不应拉回")
	assert_true(main._snap_time < 0.0)


func test_player_move_speed_change_echoes_keeps_local() -> void:
	"""上报窗口内变速 → 权威位置仍是旧上报回声，零纠正。

	防护：不得比较"权威位移 vs 最新上报窗口位移"——变速使两窗口位移
	错位（δ_权威 = 6 ≠ δ_上报 = 12）→ 误判偏离触发拉回；seq 对齐后
	权威位置 ≈ 该次上报位置即可判定认可。
	"""
	var main: Node3D = _make_world_instance()
	main._player_pos = Vector3(118.0, 0.0, 100.0)
	main._report_seq_pos = {7: Vector2(106.0, 100.0)}

	main._handle_response({
		"type": "response", "request_type": "player_move",
		"payload": {"x": 106.0, "y": 100.0, "seq": 7},
	})

	assert_eq(main._player_pos.x, 118.0, "变速滞后同样不应拉回")
	assert_true(main._snap_time < 0.0)


func test_player_move_clamped_delta_snaps_back() -> void:
	"""后端钳制（δ_权威 = 0 ≠ δ_上报 = 6，权威位置 ≠ 上报位置）→ 真偏离，平滑拉回。"""
	var main: Node3D = _make_world_instance()
	main._player_pos = Vector3(105.0, 0.0, 100.0)
	main._report_seq_pos = {8: Vector2(106.0, 100.0)}

	main._handle_response({
		"type": "response", "request_type": "player_move",
		"payload": {"x": 100.0, "y": 100.0, "seq": 8},
	})

	assert_true(main._snap_time >= 0.0, "钳制偏离应启动平滑过渡")
	assert_eq(main._snap_target.x, 100.0, "过渡目标应为钳制后的权威位置")
	assert_eq(main._player_pos.x, 105.0, "过渡启动瞬间不应瞬跳")
	main._process_snap(0.15)
	assert_almost_eq(main._player_pos.x, 100.0, 0.01, "过渡完成后应拉回权威位置")


func test_snap_input_accumulates_across_frames() -> void:
	"""过渡期间连续多帧输入不丢失（防护：每帧从固定起点插值会覆盖
	上一帧输入位移，0.15s 内玩家只前进约一帧）。"""
	var main: Node3D = _make_world_instance()
	main._player_pos = Vector3(10.0, 0.0, 20.0)
	main._handle_response({
		"type": "response", "request_type": "player_move",
		"payload": {"x": 5.0, "y": 20.0},
	})
	main._snap_time = 0.0
	main._snap_from = main._player_pos
	main._snap_target = Vector3(5.0, 0.0, 20.0)
	# 固定相机朝向：move_right 沿世界 +X，输入位移 = PLAYER_SPEED × delta（0.5）
	main._camera.global_transform = Transform3D.IDENTITY

	# 第二帧：先插值推进（无输入），再叠加输入——输入位移应完整保留
	main._process_snap(1.0 / 60.0)
	main._process_snap(1.0 / 60.0)
	var after_snap_only: float = main._player_pos.x
	Input.action_press("move_right")
	main._process_input(1.0 / 60.0)
	Input.action_release("move_right")

	assert_almost_eq(main._player_pos.x, after_snap_only + 30.0 / 60.0, 0.01,
		"第二帧输入位移应完整保留（不被插值覆盖）")


func test_teleport_resets_authority_state() -> void:
	"""传送事件应重置吸附过渡与对账基准：在途过渡不会把位置插值"撤销"回旧目标。"""
	var main: Node3D = _make_world_instance()
	main._player_pos = Vector3(10.0, 0.0, 20.0)
	main._handle_response({
		"type": "response", "request_type": "player_move",
		"payload": {"x": 5.0, "y": 20.0},
	})
	assert_true(main._snap_time >= 0.0, "前置：过渡进行中")

	main._handle_event({
		"type": "event", "event_type": "player_teleported",
		"payload": {"data": {"x": 500.0, "y": 600.0}},
	})

	assert_eq(main._player_pos.x, 500.0, "传送应立即生效")
	assert_eq(main._player_pos.z, 600.0)
	assert_true(main._snap_time < 0.0, "过渡应被取消（不会被拉回旧目标）")
	assert_true(main._report_seq_pos.is_empty(), "对账记录应清零")
	main._process_snap(0.05)
	assert_eq(main._player_pos.x, 500.0, "过渡推进不应改变传送后的位置")


# ── 异步网格构建 ──────────────────────────────────────────

func _make_async_world_instance() -> Node3D:
	"""实例化并恢复默认（WorkerThreadPool）构建器，走真实异步路径。"""
	var main: Node3D = _make_world_instance()
	main.mesh_builder = main._default_mesh_builder
	return main


func _inject_full_chunk_response(main: Node3D, cx: int, cy: int) -> void:
	var elev := PackedFloat32Array()
	elev.resize(CS * CS)
	elev.fill(10.0)
	var terr := PackedInt32Array()
	terr.resize(CS * CS)
	terr.fill(2)
	main._handle_response({
		"type": "response",
		"request_type": "get_chunks",
		"payload": {
			"chunks": [{"cx": cx, "cy": cy, "tiles_b64": _make_tiles_b64(terr, elev)}],
			"include_tiles": true,
		},
	})


func test_full_response_submits_async_build() -> void:
	"""默认构建器：完整响应只提交后台任务（CONSTRUCTING），不阻塞主线程挂载。"""
	var main: Node3D = _make_async_world_instance()
	main._set_birth_chunk(0, 0)
	var key := Vector2i(0, 0)
	main._stream_machine.collect_field_requests(key, 0)
	main._stream_machine.select_full_requests(func(k): return true, 10)

	_inject_full_chunk_response(main, 0, 0)

	assert_eq(main._stream_machine.get_state(key), main.ChunkState.CONSTRUCTING,
		"完整数据到达应提交后台构建（异步）")
	assert_true(main._building.has(key), "在飞表应登记该 chunk")
	assert_false(main._terrain_parent.has_node(NodePath("Chunk_0_0")),
		"任务完成前不应挂载节点")

	# 等待后台任务完成并挂载（墙钟超时兜底；循环内轮询结果队列）
	var t0 := Time.get_ticks_msec()
	while main._building.has(key) and Time.get_ticks_msec() - t0 < 5000:
		await get_tree().process_frame
		main._poll_build_results()
	main._poll_build_results()

	assert_eq(main._stream_machine.get_state(key), main.ChunkState.BUILT,
		"后台任务完成后应挂载并标记 BUILT")
	assert_true(main._terrain_parent.has_node(NodePath("Chunk_0_0")),
		"挂载后节点应存在")


func test_unload_discards_inflight_result() -> void:
	"""卸载后的陈旧构建结果应丢弃（不复活 chunk）。"""
	var main: Node3D = _make_async_world_instance()
	main._set_birth_chunk(0, 0)
	var key := Vector2i(0, 0)
	main._stream_machine.collect_field_requests(key, 0)
	main._stream_machine.select_full_requests(func(k): return true, 10)

	_inject_full_chunk_response(main, 0, 0)
	assert_eq(main._stream_machine.get_state(key), main.ChunkState.CONSTRUCTING)

	main._forget_chunk(key)
	# 等待在飞任务结束并消费结果（丢弃路径），避免任务在实例释放后仍在运行
	var t0 := Time.get_ticks_msec()
	while main._building.has(key) and Time.get_ticks_msec() - t0 < 5000:
		await get_tree().process_frame
		main._poll_build_results()
	main._poll_build_results()

	assert_eq(main._stream_machine.get_state(key), main.ChunkState.UNKNOWN,
		"卸载后不应被陈旧结果复活")
	assert_false(main._terrain_parent.has_node(NodePath("Chunk_0_0")),
		"陈旧结果不应挂载节点")


func test_disconnect_discards_inflight_result_then_rebuild() -> void:
	"""断线：构建在途降级 RECEIVED，陈旧结果丢弃；重连后重新构建挂载。"""
	var main: Node3D = _make_async_world_instance()
	main._set_birth_chunk(0, 0)
	var key := Vector2i(0, 0)
	main._stream_machine.collect_field_requests(key, 0)
	main._stream_machine.select_full_requests(func(k): return true, 10)

	_inject_full_chunk_response(main, 0, 0)
	assert_eq(main._stream_machine.get_state(key), main.ChunkState.CONSTRUCTING)

	main._on_disconnected()
	assert_eq(main._stream_machine.get_state(key), main.ChunkState.RECEIVED,
		"断线后构建在途应降级 RECEIVED（数据保留）")

	# 等待旧任务结束并消费结果（应被丢弃）
	var t0 := Time.get_ticks_msec()
	while main._building.has(key) and Time.get_ticks_msec() - t0 < 5000:
		await get_tree().process_frame
		main._poll_build_results()
	main._poll_build_results()
	assert_false(main._terrain_parent.has_node(NodePath("Chunk_0_0")),
		"断线期间的陈旧结果应丢弃")

	# 重连后流循环重新提交（新 seq）→ 挂载
	main._stream_chunks()
	t0 = Time.get_ticks_msec()
	while main._building.has(key) and Time.get_ticks_msec() - t0 < 5000:
		await get_tree().process_frame
		main._poll_build_results()
	main._poll_build_results()
	assert_eq(main._stream_machine.get_state(key), main.ChunkState.BUILT,
		"重连后应重建并挂载")
	assert_true(main._terrain_parent.has_node(NodePath("Chunk_0_0")))


func test_inflight_limit_keeps_received() -> void:
	"""在飞构建达到上限：其余 RECEIVED chunk 保持等待，不超限提交。"""
	var main: Node3D = _make_async_world_instance()
	main._set_birth_chunk(0, 0)
	# 前 MAX_BUILD_INFLIGHT 个 chunk 先提交构建（占满在飞上限）
	for cx in range(main.MAX_BUILD_INFLIGHT):
		main._stream_machine.collect_field_requests(Vector2i(cx, 0), 0)
		main._stream_machine.select_full_requests(func(k): return k == Vector2i(cx, 0), 10)
		_inject_full_chunk_response(main, cx, 0)
	assert_eq(main._building.size(), main.MAX_BUILD_INFLIGHT, "在飞上限个构建应在飞")

	# 下一个 chunk 到达 RECEIVED：在飞已满 → 保持 RECEIVED 不提交
	var key3 := Vector2i(main.MAX_BUILD_INFLIGHT, 0)
	main._stream_machine.collect_field_requests(key3, 0)
	main._stream_machine.select_full_requests(func(k): return k == key3, 10)
	_inject_full_chunk_response(main, key3.x, 0)

	assert_eq(main._stream_machine.get_state(key3), main.ChunkState.RECEIVED,
		"在飞上限未满前不应提交第三个构建")

	# 等待全部在飞任务结束（避免任务在实例释放后仍在运行）
	var t0 := Time.get_ticks_msec()
	while not main._building.is_empty() and Time.get_ticks_msec() - t0 < 5000:
		await get_tree().process_frame
		main._poll_build_results()
	main._poll_build_results()
