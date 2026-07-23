"""主世界 3D 场景 — 透视等轴视角 + 流式 chunk 地形。
"""
extends Node3D

const Config = preload("res://scripts/config.gd")

# ── 相机常量 ──────────────────────────────────────────────

const CAMERA_FOV: float = Config.CAMERA_3D_FOV
const CAMERA_DISTANCE_DEFAULT: float = Config.CAMERA_3D_DISTANCE_DEFAULT
const CAMERA_ZOOM_DISTANCE_STEP: float = Config.CAMERA_3D_DISTANCE_STEP
const CAMERA_DISTANCE_MIN: float = Config.CAMERA_3D_DISTANCE_MIN
const CAMERA_DISTANCE_MAX: float = Config.CAMERA_3D_DISTANCE_MAX
const PLAYER_SPEED: float = Config.PLAYER_3D_SPEED
const PLAYER_FAST_MULT: float = Config.PLAYER_3D_FAST_MULT

# ── 流式 chunk 常量 ───────────────────────────────────────

const CHUNK_SIZE: int = Config.TILE_MAP_SIZE
const STREAM_MARGIN: int = 1
const UNLOAD_MARGIN: int = 2
const MAX_PENDING: int = 3

## 终端节点
@onready var _terminal: TerminalWidget = $TerminalLayer/TerminalWidget
## 3D 伪正交相机（极小 FOV 近似正交）
@onready var _camera: Camera3D = $World/Camera3D
## 调试信息覆盖层
@onready var _debug_overlay: DebugOverlay = $DebugLayer/DebugOverlay
## 事件日志面板
@onready var _event_log: EventLog = $DebugLayer/EventLog
## WorldEnvironment 节点
@onready var _world_env: WorldEnvironment = $World/WorldEnvironment
## 方向光（太阳）
@onready var _sun_light: DirectionalLight3D = $World/SunLight

## 相机焦点（世界空间中的观察目标点）
var _camera_focus: Vector3 = Vector3(0, 0, 0)
## 当前相机距离
var _camera_distance: float = CAMERA_DISTANCE_DEFAULT
## 地形 chunk 容器
var _terrain_parent: Node3D
## 是否已对齐相机到地形表面
var _camera_grounded: bool = false

## chunk 状态追踪: {Vector2i(cx, cy): chunk_data_dict}
var _chunks: Dictionary = {}
## 正在请求中的 chunk: {Vector2i(cx, cy): true}
var _pending: Dictionary = {}
## 已渲染的 chunk: {Vector2i(cx, cy): true}
var _loaded: Dictionary = {}
## 待请求 tile 数据的队列
var _tile_queue: Array[Vector2i] = []

## 性能计时（微秒）
var _stream_us: int = 0

## 玩家实体占位
var _player: Node3D
## 玩家世界位置（XZ 平面移动，Y 由地形决定）
var _player_pos: Vector3 = Vector3.ZERO
## 出生 chunk（后端权威）
var _birth_chunk: Vector2i = Vector2i.ZERO
var _has_birth: bool = false


func _ready() -> void:
	_terminal.remote_command_submitted.connect(_on_terminal_command)

	Connection.connection_established.connect(_on_connected)
	Connection.connection_lost.connect(_on_disconnected)
	Connection.message_received.connect(_on_message)

	_terrain_parent = Node3D.new()
	_terrain_parent.name = "TerrainChunks"
	$World.add_child(_terrain_parent)

	_create_player()

	_setup_debug_overlay()
	_configure_camera()
	_configure_environment()

	Connection.connect_to_server()


func _exit_tree() -> void:
	if Connection.connection_established.is_connected(_on_connected):
		Connection.connection_established.disconnect(_on_connected)
	if Connection.connection_lost.is_connected(_on_disconnected):
		Connection.connection_lost.disconnect(_on_disconnected)
	if Connection.message_received.is_connected(_on_message):
		Connection.message_received.disconnect(_on_message)


func _configure_camera() -> void:
	if _camera == null:
		push_error("MainWorld3D: Camera3D not found!")
		return
	_camera.projection = Camera3D.PROJECTION_PERSPECTIVE
	_camera.fov = CAMERA_FOV
	_camera.near = 1.0
	_camera.far = 20000.0
	_camera_distance = CAMERA_DISTANCE_DEFAULT
	_camera_focus = _player_pos
	_apply_camera_transform()

func _configure_environment() -> void:
	if _world_env == null:
		push_error("MainWorld3D: WorldEnvironment not found!")
		return

	var env := _world_env.environment
	if env == null:
		env = Environment.new()
		_world_env.environment = env

	env.background_mode = Environment.BG_COLOR
	env.background_color = Color(0.15, 0.15, 0.5, 1)
	env.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	env.ambient_light_color = Color(0.55, 0.55, 0.6, 1)
	env.ambient_light_energy = 1.0
	env.tonemap_mode = Environment.TONE_MAPPER_LINEAR

	# ── 阴影配置 ──
	if _sun_light:
		_sun_light.shadow_enabled = true
		_sun_light.directional_shadow_mode = DirectionalLight3D.SHADOW_PARALLEL_4_SPLITS
		_sun_light.directional_shadow_blend_splits = true
		_sun_light.directional_shadow_split_1 = 0.05
		_sun_light.directional_shadow_split_2 = 0.15
		_sun_light.directional_shadow_split_3 = 0.25
		_sun_light.shadow_bias = 0.15
		_sun_light.shadow_normal_bias = 5.0

	print("MainWorld3D: Environment configured — ambient=%.1f, bg=%s" % [env.ambient_light_energy, env.background_color])


func _create_player() -> void:
	var mesh := BoxMesh.new()
	mesh.size = Vector3(0.8, 1.8, 0.8)

	var player_body := MeshInstance3D.new()
	player_body.name = "PlayerBody"
	player_body.mesh = mesh
	player_body.position = Vector3(0, 0.9, 0)

	var mat := StandardMaterial3D.new()
	mat.albedo_color = Color(0.9, 0.3, 0.3, 1)
	player_body.material_override = mat

	_player = Node3D.new()
	_player.name = "Player"
	_player.add_child(player_body)
	_player.visible = false  # 等出生点和地形就绪后再显示
	$World.add_child(_player)
	_player_pos = Vector3.ZERO
	_player.position = _player_pos
	print("MainWorld3D: player created")


func _get_ground_elevation_at(pos: Vector3) -> float:
	var cx: int = floori(pos.x / float(CHUNK_SIZE))
	var cz: int = floori(pos.z / float(CHUNK_SIZE))
	var key := Vector2i(cx, cz)
	var chunk: Dictionary = _chunks.get(key, {})
	if chunk == null:
		return NAN
	var elev: Array = chunk.get("elevation", [])
	if elev.size() < CHUNK_SIZE * CHUNK_SIZE:
		return NAN
	var tx: int = int(pos.x) - cx * CHUNK_SIZE
	var tz: int = int(pos.z) - cz * CHUNK_SIZE
	if tx < 0 or tx >= CHUNK_SIZE or tz < 0 or tz >= CHUNK_SIZE:
		return NAN
	return float(elev[tz * CHUNK_SIZE + tx])


func _set_birth_chunk(cx: int, cy: int) -> void:
	if _has_birth:
		return
	_has_birth = true
	_birth_chunk = Vector2i(cx, cy)
	_player_pos.x = float(cx * CHUNK_SIZE + CHUNK_SIZE / 2.0)
	_player_pos.z = float(cy * CHUNK_SIZE + CHUNK_SIZE / 2.0)
	_player.position = _player_pos
	_camera_focus = _player_pos
	_apply_camera_transform()
	print("MainWorld3D: birth chunk (%d,%d), player at (%.0f, %.0f)" % [cx, cy, _player_pos.x, _player_pos.z])


# ── 调试数据 getter（供 DebugSection 自行拉取）────────────

func get_debug_camera_info() -> Dictionary:
	if _camera == null:
		return {}
	return {
		"position": Vector2(_camera.position.x, _camera.position.z),
		"camera_display": "距离: %.0f m" % _camera_distance,
	}


func get_debug_player_info() -> Dictionary:
	return {
		"world_pos": Vector2(_player_pos.x, _player_pos.z),
		"chunk": Vector2i(
			floori(_player_pos.x / float(CHUNK_SIZE)),
			floori(_player_pos.z / float(CHUNK_SIZE))),
		"elevation": _player_pos.y - 1.0,
	}


func get_debug_terrain_at(world_pos: Vector2) -> Dictionary:
	var cx: int = floori(world_pos.x / float(CHUNK_SIZE))
	var cz: int = floori(world_pos.y / float(CHUNK_SIZE))
	var key := Vector2i(cx, cz)
	var chunk: Dictionary = _chunks.get(key, {})
	if chunk == null:
		return {}
	var elev: Array = chunk.get("elevation", [])
	var slope: Array = chunk.get("slope", [])
	if elev.size() < CHUNK_SIZE * CHUNK_SIZE:
		return {}
	var tx: int = int(world_pos.x) - cx * CHUNK_SIZE
	var tz: int = int(world_pos.y) - cz * CHUNK_SIZE
	if tx < 0 or tx >= CHUNK_SIZE or tz < 0 or tz >= CHUNK_SIZE:
		return {}
	var idx: int = tz * CHUNK_SIZE + tx
	var result: Dictionary = {}
	if idx < elev.size():
		result["elevation"] = int(elev[idx])
	if idx < slope.size():
		result["slope"] = float(slope[idx])
	return result


func get_debug_climate_at(world_pos: Vector2) -> Dictionary:
	var cx: int = floori(world_pos.x / float(CHUNK_SIZE))
	var cz: int = floori(world_pos.y / float(CHUNK_SIZE))
	var key := Vector2i(cx, cz)
	var chunk: Dictionary = _chunks.get(key, {})
	if chunk == null:
		return {}
	var result: Dictionary = {}
	if chunk.has("temperature"):
		result["temperature"] = float(chunk["temperature"])
	if chunk.has("humidity"):
		result["humidity"] = float(chunk["humidity"])
	if chunk.has("climate"):
		result["climate_zone"] = int(chunk["climate"])
	return result


func get_debug_chunk_stats() -> Dictionary:
	return {
		"loaded": _loaded.size(),
		"placing": 0,
		"cached": 0,
		"pending": _pending.size(),
	}


func get_debug_timing() -> Dictionary:
	return {
		"stream": _stream_us,
		"place": 0,
		"erase": 0,
		"queue": 0,
		"conn": Connection.last_process_us,
	}


func _update_player_ground() -> void:
	var ground_y := _get_ground_elevation_at(_player_pos)
	if not is_nan(ground_y):
		_player_pos.y = maxf(ground_y, 0.0) + 1.0
		_player.position = _player_pos


func _build_terrain_chunk(cx: int, cy: int, elevation: Array) -> void:
	const CS: int = CHUNK_SIZE
	var key := Vector2i(cx, cy)
	if _loaded.has(key) or _terrain_parent.has_node(NodePath("Chunk_%d_%d" % [cx, cy])):
		return

	var land_count := 0
	var transforms: Array[Transform3D] = []
	transforms.resize(CS * CS)

	for z in CS:
		for x in CS:
			var idx := z * CS + x
			var elev: float = elevation[idx]
			if elev < 0.0:
				continue
			var wy := roundi(elev)
			transforms[land_count] = Transform3D(
				Basis(), Vector3(float(x) + 0.5, wy + 0.5, float(z) + 0.5))
			land_count += 1

	if land_count == 0:
		_loaded[key] = true
		return

	var box_mesh := BoxMesh.new()
	box_mesh.size = Vector3(1.001, 1.001, 1.001)

	var multimesh := MultiMesh.new()
	multimesh.mesh = box_mesh
	multimesh.transform_format = MultiMesh.TRANSFORM_3D
	multimesh.instance_count = land_count
	for i in land_count:
		multimesh.set_instance_transform(i, transforms[i])

	var mat := StandardMaterial3D.new()
	mat.albedo_color = Color(0.45, 0.55, 0.45, 1)

	var mmi := MultiMeshInstance3D.new()
	mmi.name = "Chunk_%d_%d" % [cx, cy]
	mmi.multimesh = multimesh
	mmi.material_override = mat
	mmi.position = Vector3(float(cx * CS), 0.0, float(cy * CS))
	_terrain_parent.add_child(mmi)
	_loaded[key] = true
	print("MainWorld3D: chunk (%d,%d) — %d land blocks" % [cx, cy, land_count])

	# 首次 chunk 覆盖玩家时，吸附玩家到地面
	if not _camera_grounded and land_count > 0:
		if cx == floori(_player_pos.x / float(CS)) and cy == floori(_player_pos.z / float(CS)):
			_camera_grounded = true
			var ground_y := _get_ground_elevation_at(_player_pos)
			if not is_nan(ground_y):
				_player_pos.y = maxf(ground_y, 0.0) + 1.0
				_player.position = _player_pos
				_camera_focus = _player_pos
				_player.visible = true
				_apply_camera_transform()
				print("MainWorld3D: player grounded at y=%.1f" % _player_pos.y)


func _process(delta: float) -> void:
	_process_camera(delta)

	if _debug_overlay and _debug_overlay.is_shown():
		_debug_overlay.process_sections(delta)

	if Connection.status != Connection.Status.CONNECTED:
		return

	if _terminal and _terminal.is_open():
		return

	if _event_log:
		_event_log.set_player_chunk(Vector2i(
			floori(_player_pos.x / float(CHUNK_SIZE)),
			floori(_player_pos.z / float(CHUNK_SIZE))))

	_stream_chunks()
	_process_input(delta)


func _unhandled_input(_event: InputEvent) -> void:
	pass


func _process_camera(_delta: float) -> void:
	if _camera == null:
		return

	var zoom_delta: float = 0.0
	if Input.is_action_just_pressed("zoom_in"):
		zoom_delta = -CAMERA_ZOOM_DISTANCE_STEP
	elif Input.is_action_just_pressed("zoom_out"):
		zoom_delta = CAMERA_ZOOM_DISTANCE_STEP

	if zoom_delta != 0.0:
		_camera_distance = clampf(
			_camera_distance + zoom_delta,
			CAMERA_DISTANCE_MIN,
			CAMERA_DISTANCE_MAX)
		_apply_camera_transform()


func _apply_camera_transform() -> void:
	# 等轴方向：45°偏航 + ~35°俯角
	var dir := Vector3(1, 1, 1).normalized()
	_camera.position = _camera_focus + dir * _camera_distance
	_camera.look_at(_camera_focus, Vector3.UP)

	# 阴影覆盖全部可见地形
	if _sun_light:
		_sun_light.directional_shadow_max_distance = _camera_distance * 1.5 + 500.0
		_sun_light.directional_shadow_fade_start = 0.99
		_sun_light.position = _camera_focus


func _process_input(delta: float) -> void:
	var move_input := Input.get_vector("move_left", "move_right", "move_up", "move_down")
	if move_input != Vector2.ZERO:
		var forward: Vector3 = -_camera.global_transform.basis.z
		var right: Vector3 = _camera.global_transform.basis.x
		forward.y = 0.0
		right.y = 0.0
		if forward.length_squared() > 0.0:
			forward = forward.normalized()
		if right.length_squared() > 0.0:
			right = right.normalized()

		var speed := PLAYER_SPEED
		if Input.is_key_pressed(KEY_SHIFT):
			speed *= PLAYER_FAST_MULT

		_player_pos.x += (forward * -move_input.y + right * move_input.x).x * speed * delta
		_player_pos.z += (forward * -move_input.y + right * move_input.x).z * speed * delta
		_update_player_ground()
		_camera_focus = _player_pos
		_apply_camera_transform()

	if Input.is_action_just_pressed("interact"):
		Connection.send({
			"type": "request",
			"request_type": "player_interact",
			"payload": {}
		})

	if Input.is_action_just_pressed("menu"):
		Connection.send({
			"type": "request",
			"request_type": "open_menu",
			"payload": {}
		})


func _on_terminal_command(command: String) -> void:
	Connection.send({
		"type": "request",
		"request_type": "terminal_cmd",
		"payload": {"command": command},
	})


# ── 调试覆盖层 ──────────────────────────────────────────────

func _setup_debug_overlay() -> void:
	_debug_overlay.setup_default_sections(self)


# ── Connection 信号处理 ───────────────────────────────────

func _on_connected(host: String, port: int) -> void:
	print("MainWorld3D: connected to %s:%d" % [host, port])
	Connection.send({
		"type": "request",
		"request_type": "entity_snapshot",
		"payload": {},
	})
	Connection.send({
		"type": "request",
		"request_type": "player_state",
		"payload": {},
	})


func _on_disconnected() -> void:
	print("MainWorld3D: disconnected")


func _on_message(message: Dictionary) -> void:
	var msg_type: String = message.get("type", "")

	match msg_type:
		"event":
			_handle_event(message)
		"response":
			_handle_response(message)
		"error":
			_handle_error(message)
		_:
			push_warning("MainWorld3D: unknown message type: %s" % msg_type)


func _handle_event(message: Dictionary) -> void:
	var event_type: String = message.get("event_type", "")
	var payload: Dictionary = message.get("payload", {})
	var data: Dictionary = payload.get("data", {})

	if event_type == "player_teleported":
		var tx: float = float(data.get("x", _player_pos.x))
		var tz: float = float(data.get("y", _player_pos.z))
		_player_pos.x = tx
		_player_pos.z = tz
		_update_player_ground()
		_camera_focus = _player_pos
		_apply_camera_transform()
		if _event_log:
			_event_log.push_event("[%02d:%02d] 传送至 (%.0f, %.0f)" % [
				payload.get("game_hour", 0), payload.get("game_minute", 0),
				tx, tz])
		return

	if _debug_overlay:
		_debug_overlay.broadcast_event(event_type, payload)

	if _event_log:
		_event_log.on_world_event(event_type, payload)


func _handle_response(message: Dictionary) -> void:
	var request_type: String = message.get("request_type", "")
	var payload: Dictionary = message.get("payload", {})

	# 广播到所有调试分区（Section 按其关心的 request_type 自行过滤）
	if _debug_overlay:
		_debug_overlay.broadcast_response(request_type, payload)

	match request_type:
		"get_chunks":
			if not _has_birth and payload.has("birth_chunk"):
				var bc: Array = payload["birth_chunk"]
				_set_birth_chunk(bc[0], bc[1])

			var chunks: Array = payload.get("chunks", [])
			for chunk in chunks:
				var cx: int = int(chunk.get("cx", 0))
				var cy: int = int(chunk.get("cy", 0))
				var key := Vector2i(cx, cy)
				_chunks[key] = chunk
				_pending.erase(key)

				var elev: Array = chunk.get("elevation", [])
				if elev.size() == CHUNK_SIZE * CHUNK_SIZE:
					if not _loaded.has(key):
						_build_terrain_chunk(cx, cy, elev)
		"terminal_cmd":
			if _terminal:
				_terminal.write(payload.get("output", ""))
		_:
			pass


## ── 流式 chunk 管理 ──────────────────────────────────────

func _stream_chunks() -> void:
	var t0: int = Time.get_ticks_usec()

	var center_cx: int = floori(_player_pos.x / float(CHUNK_SIZE))
	var center_cy: int = floori(_player_pos.z / float(CHUNK_SIZE))
	var stream_r := _stream_radius()

	_unload_distant_chunks(center_cx, center_cy, stream_r)

	var coords: Array[Array] = []
	for dx in range(-stream_r, stream_r + 1):
		for dy in range(-stream_r, stream_r + 1):
			var key := Vector2i(center_cx + dx, center_cy + dy)
			if not _chunks.has(key):
				_chunks[key] = null
				coords.append([key.x, key.y])

	if not coords.is_empty():
		_send_chunk_request(coords, false)

	for dx in range(-stream_r, stream_r + 1):
		for dy in range(-stream_r, stream_r + 1):
			var key := Vector2i(center_cx + dx, center_cy + dy)
			if _chunks.has(key) and _chunks[key] != null:
				if _loaded.has(key):
					continue
				if _pending.has(key):
					continue
				if key not in _tile_queue:
					_tile_queue.append(key)

	var pending_count: int = _pending.size()
	while not _tile_queue.is_empty() and pending_count < MAX_PENDING:
		var key: Vector2i = _tile_queue.pop_front()
		_pending[key] = true
		_send_chunk_request([[key.x, key.y]], true)
		pending_count += 1

	_stream_us = Time.get_ticks_usec() - t0


func _stream_radius() -> int:
	var half_fov_rad: float = deg_to_rad(CAMERA_FOV * 0.5)
	var visible_half: float = _camera_distance * tan(half_fov_rad) * 1.5
	var radius: int = ceili(visible_half / float(CHUNK_SIZE))
	return maxi(STREAM_MARGIN, radius + 1)


func _send_chunk_request(coords: Array[Array], include_tiles: bool) -> void:
	Connection.send({
		"type": "request",
		"request_type": "get_chunks",
		"payload": {
			"chunks": coords,
			"include_tiles": include_tiles,
			"force_fields": true,
		},
	})


func _unload_distant_chunks(center_cx: int, center_cy: int, stream_r: int) -> void:
	var unload_r := stream_r + UNLOAD_MARGIN
	for key in _loaded.keys():
		var cx: int = key.x
		var cy: int = key.y
		if abs(cx - center_cx) > unload_r or abs(cy - center_cy) > unload_r:
			var node_name := "Chunk_%d_%d" % [cx, cy]
			if _terrain_parent.has_node(NodePath(node_name)):
				_terrain_parent.get_node(NodePath(node_name)).queue_free()
			_loaded.erase(key)
			_chunks.erase(key)
			_pending.erase(key)
			print("MainWorld3D: unloaded chunk (%d,%d)" % [cx, cy])


func _handle_error(message: Dictionary) -> void:
	var error_msg: String = message.get("error", "unknown error")
	push_error("MainWorld3D: server error: %s" % error_msg)
