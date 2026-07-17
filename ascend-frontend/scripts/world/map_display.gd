"""Tile 级地图 — 等轴侧渲染，多层 TileMapLayer + spritesheet 纹理
"""
@warning_ignore("integer_division")
extends Node2D

class_name MapDisplay

const Config = preload("res://scripts/config.gd")


# ── 常量 ──────────────────────────────────────────────────

const CHUNK_SIZE: int = Config.TILE_MAP_SIZE
const INITIAL_VIEW_RADIUS: int = Config.INITIAL_VIEW_RADIUS
const STREAM_MARGIN: int = Config.STREAM_MARGIN
const UNLOAD_RADIUS: int = Config.UNLOAD_RADIUS
const MAX_PENDING_TILES: int = Config.MAX_PENDING_TILES
const PLAYER_SPEED: float = Config.PLAYER_SPEED

## 玩家位置上报间隔（秒），权威实体在后端
const MOVE_REPORT_INTERVAL: float = Config.MOVE_REPORT_INTERVAL

## 放置操作每帧时间预算（微秒），超时后下帧继续
const PLACE_TIME_BUDGET_US: int = Config.PLACE_TIME_BUDGET_US
const PLACE_BATCH_CHECK_MASK: int = 0x3F  # 每 64 格检查一次时间预算
const TERRAIN_TILES: Array[Vector2i] = [
	Vector2i(0, 2),
	Vector2i(0, 0),
	Vector2i(6, 1),
	Vector2i(8, 5),
	Vector2i(8, 5),
	Vector2i(8, 5),
	Vector2i(0, 10),
	Vector2i(0, 8),
	Vector2i(6, 1),
]

const TERRAIN_BANDS: Array[int] = [
	3, 2, 2, 4, 4, 5, 1, 0, 2,
]


# ── @onready ──────────────────────────────────────────────

@onready var _camera: Camera2D = $MapCamera
@onready var _elevation_layers: Node2D = $ElevationLayers
@onready var _layer0: TileMapLayer = $ElevationLayers/Layer0

## 本地控制实体的视图节点（EntityLayer 创建后经 bind_local_entity_view 注入）
var _player: Node2D = null


# ── 属性 ──────────────────────────────────────────────────

var _player_pos: Vector2 = Vector2.ZERO
var _player_elevation: int = 0

## 位置上报累积计时（秒）与待上报标记
var _move_report_accum: float = 0.0
var _move_dirty: bool = false
## 是否已收到后端权威 player_state
var _has_player_state: bool = false

var _chunks: Dictionary = {}
var _pending: Dictionary = {}
var _tiles_loaded: Dictionary = {}
var _tiles_cached: Dictionary = {}
var _being_placed: Dictionary = {}
var _tile_queue: Array[Vector2i] = []
var _birth_chunk: Vector2i = Vector2i.ZERO
var _has_birth: bool = false

var _layers: Dictionary = {}
var _entity_view_layers: Dictionary = {}
var _tileset: TileSet = null
var _cell_step_x: Vector2 = Vector2.ZERO
var _cell_step_y: Vector2 = Vector2.ZERO
var _place_queue: Array = []
var _place_cursor: int = 0
var _chunk_elevations: Dictionary = {}

## 擦除队列：卸载时逐帧擦除共享层上的 tile，保持 cell 数有限
var _erase_queue: Array = []

## 性能计时器（微秒），用于 F3 面板定位瓶颈
var _last_place_us: int = 0
var _last_erase_us: int = 0
var _last_stream_us: int = 0
var _last_queue_us: int = 0


# ── 生命周期 ──────────────────────────────────────────────

func _ready() -> void:
	_tileset = _layer0.tile_set
	_layers[0] = _layer0
	_cell_step_x = _layer0.map_to_local(Vector2i(1, 0)) - _layer0.map_to_local(Vector2i.ZERO)
	_cell_step_y = _layer0.map_to_local(Vector2i(0, 1)) - _layer0.map_to_local(Vector2i.ZERO)


func _process(_delta: float) -> void:
	if _player and _camera:
		_camera.global_position = _player.global_position
	_process_queues()
	_report_move(_delta)


# ── 公共接口 ──────────────────────────────────────────────

func bind_local_entity_view(view: Node2D) -> void:
	"""绑定本地控制实体的视图节点（EntityLayer 创建视图后调用）。

	绑定后本地预测移动、相机跟随作用于该节点。
	Player 与其他实体共用 EntityView 渲染管线，本方法只是把
	"输入预测的驱动目标"指向其中一个视图。

	Args:
		view: 本地控制实体的视图节点。
	"""
	_player = view
	if _has_player_state:
		_place_player_at(_player_pos, _player_elevation)


func unbind_local_entity_view(view: Node2D) -> void:
	"""解绑本地控制实体视图（视图销毁时调用）。

	Args:
		view: 之前绑定的视图节点。
	"""
	if _player == view:
		_player = null


func move_player(direction: Vector2, delta: float) -> void:
	if not _has_player_state:
		return
	_player_pos += direction * PLAYER_SPEED * delta
	_move_dirty = true
	_place_player_at(_player_pos, _player_elevation)


func teleport_player(pos: Vector2) -> void:
	"""吸附玩家到权威位置（player_teleported 事件 / player_state 响应）。

	周边区块由下一帧 stream_chunks_for_viewport 按新位置自动请求。
	不标记 _move_dirty——权威位置无需回报。

	Args:
		pos: 后端权威世界坐标（tile 单位）。
	"""
	_move_dirty = false
	_place_player_at(pos, _player_elevation)


func handle_player_state(payload: Dictionary) -> void:
	"""处理 player_state 响应：以后端权威位置初始化本地位置。

	Args:
		payload: {entity_id, x, y}。
	"""
	if not payload.has("x") or not payload.has("y"):
		return
	_has_player_state = true
	teleport_player(Vector2(float(payload["x"]), float(payload["y"])))


func get_birth_chunk() -> Vector2i:
	"""返回出生区块坐标（未收到后端数据前为 (0,0)）。"""
	return _birth_chunk


func set_birth_chunk(cx: int, cy: int) -> void:
	if _has_birth:
		return
	_has_birth = true
	_birth_chunk = Vector2i(cx, cy)
	# 已收到权威 player_state 时不重置位置，避免出生数据晚到覆盖
	if not _has_player_state:
		_place_player_at(Vector2(cx * CHUNK_SIZE, cy * CHUNK_SIZE), 0)
	_request_chunks_around_birth()


func handle_chunk_response(payload: Dictionary) -> void:
	if not _has_birth and payload.has("birth_chunk"):
		var bc: Array = payload["birth_chunk"]
		set_birth_chunk(bc[0], bc[1])

	var chunk_list: Array = payload.get("chunks", [])
	for entry in chunk_list:
		var cx: int = entry["cx"]
		var cy: int = entry["cy"]
		var pos: Vector2i = Vector2i(cx, cy)
		_chunks[pos] = entry
		_pending.erase(pos)

		if entry.has("terrain") and not _tiles_loaded.has(pos) and not _tiles_cached.has(pos) and not _being_placed.has(pos):
			var center_cx: int = int(_player_pos.x / float(CHUNK_SIZE))
			var center_cy: int = int(_player_pos.y / float(CHUNK_SIZE))
			if abs(cx - center_cx) <= STREAM_MARGIN and abs(cy - center_cy) <= STREAM_MARGIN:
				_being_placed[pos] = true
				_place_queue.append(entry)
			else:
				_tiles_cached[pos] = true


func stream_chunks_for_viewport() -> void:
	if _camera == null or Connection.status != Connection.Status.CONNECTED:
		_last_stream_us = 0
		return

	var t0: int = Time.get_ticks_usec()

	var center_cx: int = int(_player_pos.x / float(CHUNK_SIZE))
	var center_cy: int = int(_player_pos.y / float(CHUNK_SIZE))

	_unload_distant_chunks(center_cx, center_cy)

	var coords: Array[Array] = []
	for dx: int in range(-STREAM_MARGIN, STREAM_MARGIN + 1):
		for dy: int in range(-STREAM_MARGIN, STREAM_MARGIN + 1):
			var pos: Vector2i = Vector2i(center_cx + dx, center_cy + dy)
			if not _chunks.has(pos):
				_chunks[pos] = null
				coords.append([pos.x, pos.y])

	if not coords.is_empty():
		_send_request(coords, false)

	for dx: int in range(-STREAM_MARGIN, STREAM_MARGIN + 1):
		for dy: int in range(-STREAM_MARGIN, STREAM_MARGIN + 1):
			var pos: Vector2i = Vector2i(center_cx + dx, center_cy + dy)
			if _chunks.has(pos) and _chunks[pos] != null:
				if _tiles_loaded.has(pos) or _being_placed.has(pos):
					continue
				var entry: Dictionary = _chunks[pos]
				if entry.has("terrain"):
					if _tiles_cached.has(pos):
						_tiles_cached.erase(pos)
					_being_placed[pos] = true
					_place_queue.append(entry)

	for dx: int in range(-STREAM_MARGIN, STREAM_MARGIN + 1):
		for dy: int in range(-STREAM_MARGIN, STREAM_MARGIN + 1):
			var pos: Vector2i = Vector2i(center_cx + dx, center_cy + dy)
			if (_chunks.has(pos) and _chunks[pos] != null
				and not _tiles_loaded.has(pos) and not _tiles_cached.has(pos)
				and pos not in _tile_queue
				and not _pending.has(pos)):
				_tile_queue.append(pos)

	var pending_count: int = _pending.size()

	while not _tile_queue.is_empty() and pending_count < MAX_PENDING_TILES:
		var pos: Vector2i = _tile_queue.pop_front()
		_pending[pos] = true
		_send_request([[pos.x, pos.y]], true)
		pending_count += 1

	_last_stream_us = Time.get_ticks_usec() - t0


# ── 内部实现 ──────────────────────────────────────────────

func _report_move(delta: float) -> void:
	"""节流上报本地移动到后端权威实体（player_move）。

	仅在位置有变更（_move_dirty）且连接可用时发送，
	间隔 MOVE_REPORT_INTERVAL 秒。

	Args:
		delta: 帧时间（秒）。
	"""
	_move_report_accum += delta
	if _move_report_accum < MOVE_REPORT_INTERVAL:
		return
	_move_report_accum = 0.0
	if not _move_dirty or Connection.status != Connection.Status.CONNECTED:
		return
	_move_dirty = false
	Connection.send({
		"type": "request",
		"request_type": "player_move",
		"payload": {"x": _player_pos.x, "y": _player_pos.y},
	})


func _get_layer(elevation: int) -> TileMapLayer:
	if _layers.has(elevation):
		return _layers[elevation]
	var layer := TileMapLayer.new()
	layer.name = "Layer%d" % elevation
	layer.tile_set = _tileset
	layer.y_sort_enabled = false
	layer.z_index = elevation
	layer.position = Vector2(0, -elevation * 16)
	_elevation_layers.add_child(layer)
	_layers[elevation] = layer
	return layer


func _get_entity_layer(elevation: int) -> TileMapLayer:
	"""获取渲染海拔对应的实体视图层（懒创建）。

	实体视图挂在与地形同构的 TileMapLayer 下，保证 z 排序与
	海拔抬升偏移一致。

	Args:
		elevation: 渲染海拔层。

	Returns:
		该海拔的实体层。
	"""
	if _entity_view_layers.has(elevation):
		return _entity_view_layers[elevation]
	var layer := TileMapLayer.new()
	layer.name = "EntityViews%d" % elevation
	layer.tile_set = _tileset
	layer.y_sort_enabled = false
	layer.z_index = elevation
	layer.position = Vector2(0, -elevation * 16)
	_elevation_layers.add_child(layer)
	_entity_view_layers[elevation] = layer
	return layer


func place_entity_node(node: Node2D, pos: Vector2, elevation: int) -> void:
	"""把实体视图节点放置到等距地图的精确像素位置。

	节点挂载到对应渲染海拔的实体层下，按 tile 小数部分沿
	等距基向量插值。Player 与其他实体共用本方法（统一渲染管线）。

	Args:
		node: 实体视图节点。
		pos: 世界坐标（tile 单位，float 精确值）。
		elevation: 渲染海拔层。
	"""
	var layer := _get_entity_layer(elevation)
	if node.get_parent() != layer:
		if node.get_parent():
			node.get_parent().remove_child(node)
		layer.add_child(node)
	var cell := Vector2i(int(pos.x), int(pos.y))
	var frac := pos - Vector2(cell)
	node.position = layer.map_to_local(cell) \
		+ _cell_step_x * frac.x + _cell_step_y * frac.y


func _place_player_at(pos: Vector2, elevation: int) -> void:
	_player_pos = pos
	_player_elevation = elevation
	if _player:
		place_entity_node(_player, pos, elevation)


func _request_chunks_around_birth() -> void:
	var coords: Array[Array] = []
	for dx: int in range(-INITIAL_VIEW_RADIUS, INITIAL_VIEW_RADIUS + 1):
		for dy: int in range(-INITIAL_VIEW_RADIUS, INITIAL_VIEW_RADIUS + 1):
			var pos: Vector2i = Vector2i(_birth_chunk.x + dx, _birth_chunk.y + dy)
			if not _chunks.has(pos):
				_chunks[pos] = null
				coords.append([pos.x, pos.y])
	if not coords.is_empty():
		_send_request(coords, false)


func _process_queues() -> void:
	"""统一处理放置和擦除队列，共享帧时间预算。"""
	var t0: int = Time.get_ticks_usec()
	var deadline: int = t0 + PLACE_TIME_BUDGET_US

	var t1: int = Time.get_ticks_usec()
	_place_batch(deadline)
	_last_place_us = Time.get_ticks_usec() - t1

	if Time.get_ticks_usec() < deadline:
		var t2: int = Time.get_ticks_usec()
		_erase_batch(deadline)
		_last_erase_us = Time.get_ticks_usec() - t2
	else:
		_last_erase_us = 0

	_last_queue_us = Time.get_ticks_usec() - t0


func _place_batch(deadline: int) -> void:
	if _place_queue.is_empty():
		return
	var data: Dictionary = _place_queue.front()
	var cx: int = data["cx"]
	var cy: int = data["cy"]

	var terrain: Array = data["terrain"]
	var elevation: Array = data.get("elevation", null)
	var key := Vector2i(cx, cy)
	var base_x: int = cx * CHUNK_SIZE
	var base_y: int = cy * CHUNK_SIZE
	var total: int = CHUNK_SIZE * CHUNK_SIZE
	var used_set: Dictionary = _chunk_elevations.get(key, {})
	_chunk_elevations[key] = used_set

	var layers: Dictionary = {}

	while _place_cursor < total:
		var y: int = floori(_place_cursor / float(CHUNK_SIZE))
		var x: int = _place_cursor - y * CHUNK_SIZE
		var tile: int = terrain[_place_cursor]
		var elev: int
		if tile == 6 or tile == 7:
			elev = 0
		elif elevation != null:
			elev = int(elevation[_place_cursor])
		else:
			elev = TERRAIN_BANDS[tile]
		used_set[elev] = true

		var layer: TileMapLayer = layers.get(elev, null)
		if layer == null:
			layer = _get_layer(elev)
			layers[elev] = layer

		layer.set_cell(Vector2i(base_x + x, base_y + y), 0, TERRAIN_TILES[tile])
		_place_cursor += 1

		if (_place_cursor & PLACE_BATCH_CHECK_MASK) == 0 and Time.get_ticks_usec() >= deadline:
			return

	if _place_cursor >= total:
		print("[place] chunk (%d,%d) done: %d layers" % [cx, cy, used_set.size()])
		_tiles_loaded[key] = true
		_being_placed.erase(key)
		_place_queue.pop_front()
		_place_cursor = 0


func _unload_chunk(cx: int, cy: int) -> void:
	var key := Vector2i(cx, cy)
	var used: Dictionary = _chunk_elevations.get(key, {})

	# 如果该 chunk 还在放置队列中，取消放置
	if not _place_queue.is_empty():
		var front_data: Dictionary = _place_queue.front()
		if front_data.get("cx", -1) == cx and front_data.get("cy", -1) == cy:
			_place_queue.pop_front()
			_place_cursor = 0

	var chunk_data = _chunks.get(key)
	if not used.is_empty() and chunk_data != null and chunk_data.has("terrain"):
		_erase_queue.append({
			"cx": cx,
			"cy": cy,
			"terrain": chunk_data["terrain"],
			"elevation": chunk_data.get("elevation", null),
			"evals": used.keys(),
			"cursor": 0,
		})
	_chunk_elevations.erase(key)
	_chunks.erase(key)
	_tiles_loaded.erase(key)
	_tiles_cached.erase(key)
	_being_placed.erase(key)


func _erase_batch(deadline: int) -> void:
	if _erase_queue.is_empty():
		return
	var entry: Dictionary = _erase_queue.front()
	var cx: int = entry["cx"]
	var cy: int = entry["cy"]
	var terrain: Array = entry["terrain"]
	var elevation: Array = entry["elevation"]
	var evals: Array = entry["evals"]
	var cursor: int = entry["cursor"]
	var base_x: int = cx * CHUNK_SIZE
	var base_y: int = cy * CHUNK_SIZE
	var total: int = CHUNK_SIZE * CHUNK_SIZE

	var layers: Dictionary = {}
	for elev in evals:
		layers[elev] = _layers.get(elev, null)

	while cursor < total:
		var tile: int = terrain[cursor]
		var elev: int
		if tile == 6 or tile == 7:
			elev = 0
		elif elevation != null:
			elev = int(elevation[cursor])
		else:
			elev = TERRAIN_BANDS[tile]

		var y: int = floori(cursor / float(CHUNK_SIZE))
		var x: int = cursor - y * CHUNK_SIZE
		var layer: TileMapLayer = layers.get(elev, null)
		if layer:
			layer.set_cell(Vector2i(base_x + x, base_y + y), -1)
		cursor += 1

		if (cursor & PLACE_BATCH_CHECK_MASK) == 0 and Time.get_ticks_usec() >= deadline:
			entry["cursor"] = cursor
			return

	_erase_queue.pop_front()
	for elev in evals:
		var active := false
		for other_key in _tiles_loaded:
			var other_used: Dictionary = _chunk_elevations.get(other_key, {})
			if other_used.has(elev):
				active = true
				break
		if not active:
			var layer: TileMapLayer = _layers.get(elev, null)
			if layer:
				_layers.erase(elev)
				layer.queue_free()


func _unload_distant_chunks(center_cx: int, center_cy: int) -> void:
	var to_unload: Array[Vector2i] = []
	var to_drop_tiles: Array[Vector2i] = []
	for pos: Vector2i in _tiles_loaded:
		var dx: int = abs(pos.x - center_cx)
		var dy: int = abs(pos.y - center_cy)
		if dx > UNLOAD_RADIUS or dy > UNLOAD_RADIUS:
			to_unload.append(pos)
		elif dx > STREAM_MARGIN or dy > STREAM_MARGIN:
			to_drop_tiles.append(pos)

	var unload_keys: Array[Vector2i] = []
	for pos: Vector2i in _chunks:
		if _chunks[pos] == null:
			continue
		var dx: int = abs(pos.x - center_cx)
		var dy: int = abs(pos.y - center_cy)
		if dx > UNLOAD_RADIUS or dy > UNLOAD_RADIUS:
			if not _tiles_loaded.has(pos):
				unload_keys.append(pos)
	for key in unload_keys:
		_unload_chunk(key.x, key.y)

	for key in to_drop_tiles:
		_tiles_loaded.erase(key)
	for key in to_unload:
		_unload_chunk(key.x, key.y)


func get_player_pos() -> Vector2:
	return _player_pos


func get_player_elevation() -> int:
	return _player_elevation


func get_camera() -> Camera2D:
	return _camera


func get_chunk_stats() -> Dictionary:
	return {
		"loaded": _tiles_loaded.size(),
		"placing": _being_placed.size(),
		"cached": _tiles_cached.size(),
		"pending": _pending.size(),
	}


func get_elevation_at(world_pos: Vector2) -> float:
	"""获取指定世界坐标的逐格海拔。

	Args:
		world_pos: 世界坐标。

	Returns:
		海拔值（米），无数据时返回 -999.0。
	"""
	var cx: int = int(world_pos.x / float(CHUNK_SIZE))
	var cy: int = int(world_pos.y / float(CHUNK_SIZE))
	var key := Vector2i(cx, cy)
	var chunk_data = _chunks.get(key)
	if chunk_data == null or chunk_data.is_empty() or not chunk_data.has("elevation"):
		return -999.0

	var base_x: int = cx * CHUNK_SIZE
	var base_y: int = cy * CHUNK_SIZE
	var tx: int = int(world_pos.x) - base_x
	var ty: int = int(world_pos.y) - base_y
	var idx: int = ty * CHUNK_SIZE + tx

	var elev_arr: Array = chunk_data["elevation"]
	if idx < 0 or idx >= elev_arr.size():
		return -999.0
	return float(elev_arr[idx])


func get_slope_at(world_pos: Vector2) -> float:
	"""获取指定世界坐标的逐格坡度。

	Args:
		world_pos: 世界坐标。

	Returns:
		坡度值（m/m），无数据时返回 -999.0。
	"""
	var cx: int = int(world_pos.x / float(CHUNK_SIZE))
	var cy: int = int(world_pos.y / float(CHUNK_SIZE))
	var key := Vector2i(cx, cy)
	var chunk_data = _chunks.get(key)
	if chunk_data == null or chunk_data.is_empty() or not chunk_data.has("slope"):
		return -999.0

	var base_x: int = cx * CHUNK_SIZE
	var base_y: int = cy * CHUNK_SIZE
	var tx: int = int(world_pos.x) - base_x
	var ty: int = int(world_pos.y) - base_y
	var idx: int = ty * CHUNK_SIZE + tx

	var slope_arr: Array = chunk_data["slope"]
	if idx < 0 or idx >= slope_arr.size():
		return -999.0
	return float(slope_arr[idx])


func get_chunk_temperature(world_pos: Vector2) -> float:
	"""获取指定世界坐标所在区块的年均温。

	Args:
		world_pos: 世界坐标。

	Returns:
		年均温（°C），无数据时返回 -999.0。
	"""
	var cx: int = int(world_pos.x / float(CHUNK_SIZE))
	var cy: int = int(world_pos.y / float(CHUNK_SIZE))
	var key := Vector2i(cx, cy)
	var chunk_data = _chunks.get(key)
	if chunk_data == null or chunk_data.is_empty() or not chunk_data.has("temperature"):
		return -999.0
	return float(chunk_data["temperature"])


func get_chunk_humidity(world_pos: Vector2) -> float:
	"""获取指定世界坐标所在区块的年均湿度。

	Args:
		world_pos: 世界坐标。

	Returns:
		年均湿度（%，0-100），无数据时返回 -999.0。
	"""
	var cx: int = int(world_pos.x / float(CHUNK_SIZE))
	var cy: int = int(world_pos.y / float(CHUNK_SIZE))
	var key := Vector2i(cx, cy)
	var chunk_data = _chunks.get(key)
	if chunk_data == null or chunk_data.is_empty() or not chunk_data.has("humidity"):
		return -999.0
	return float(chunk_data["humidity"])


func get_chunk_climate(world_pos: Vector2) -> int:
	"""获取指定世界坐标所在区块的气候带编码。

	与后端 ClimateZone 枚举一致:
	0=热带雨林 1=热带草原 2=沙漠 3=草原
	4=温带森林 5=亚寒带针叶林 6=极地苔原 7=高山

	Args:
		world_pos: 世界坐标。

	Returns:
		气候带编码，无数据时返回 -1。
	"""
	var cx: int = int(world_pos.x / float(CHUNK_SIZE))
	var cy: int = int(world_pos.y / float(CHUNK_SIZE))
	var key := Vector2i(cx, cy)
	var chunk_data = _chunks.get(key)
	if chunk_data == null or chunk_data.is_empty() or not chunk_data.has("climate"):
		return -1
	return int(chunk_data["climate"])


func get_timing() -> Dictionary:
	"""返回上帧性能计时器快照（微秒），读取后自动清零。"""
	var out := {
		"stream": _last_stream_us,
		"place": _last_place_us,
		"erase": _last_erase_us,
		"queue": _last_queue_us,
	}
	_last_stream_us = 0
	_last_place_us = 0
	_last_erase_us = 0
	return out


func _send_request(coord_array: Array, include_tiles: bool) -> void:
	var payload: Dictionary = {"chunks": coord_array, "force_fields": true}
	if include_tiles:
		payload["include_tiles"] = true
	Connection.send({
		"type": "request",
		"request_type": "get_chunks",
		"payload": payload,
	})
