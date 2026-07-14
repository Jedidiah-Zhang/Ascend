"""Tile 级地图 — 等轴侧渲染，多层 TileMapLayer + spritesheet 纹理
"""
extends Node2D

class_name MapDisplay


# ── 常量 ──────────────────────────────────────────────────

const CHUNK_SIZE: int = 200
const INITIAL_VIEW_RADIUS: int = 2
const STREAM_MARGIN: int = 1
const UNLOAD_RADIUS: int = 3
const MAX_PENDING_TILES: int = 3
const PLAYER_SPEED: float = 80.0
const CELLS_PER_FRAME: int = 8000

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
@onready var _player: Sprite2D = $Player


# ── 属性 ──────────────────────────────────────────────────

var _player_pos: Vector2 = Vector2.ZERO
var _player_elevation: int = 0

var _chunks: Dictionary = {}
var _pending: Dictionary = {}
var _tiles_loaded: Dictionary = {}
var _tiles_cached: Dictionary = {}
var _being_placed: Dictionary = {}
var _tile_queue: Array[Vector2i] = []
var _birth_chunk: Vector2i = Vector2i.ZERO
var _has_birth: bool = false

var _layers: Dictionary = {}
var _tileset: TileSet = null
var _cell_step_x: Vector2 = Vector2.ZERO
var _cell_step_y: Vector2 = Vector2.ZERO
var _place_queue: Array = []
var _place_cursor: int = 0
var _chunk_elevations: Dictionary = {}

var _erase_queue: Array[Dictionary] = []
var _erase_cursor: int = 0


# ── 生命周期 ──────────────────────────────────────────────

func _ready() -> void:
	_tileset = _layer0.tile_set
	_layers[0] = _layer0
	_cell_step_x = _layer0.map_to_local(Vector2i(1, 0)) - _layer0.map_to_local(Vector2i.ZERO)
	_cell_step_y = _layer0.map_to_local(Vector2i(0, 1)) - _layer0.map_to_local(Vector2i.ZERO)

	_player.centered = false
	_player.offset = Vector2(-16, -16)
	_player.z_index = 0

	Connection.connection_established.connect(_on_connected)


func _process(_delta: float) -> void:
	if _player and _camera:
		_camera.global_position = _player.global_position
	_process_place_queue()
	_process_erase_queue()


# ── 公共接口 ──────────────────────────────────────────────

func move_player(direction: Vector2, delta: float) -> void:
	if _player == null:
		return
	_player_pos += direction * PLAYER_SPEED * delta
	_place_player_at(_player_pos, _player_elevation)


func set_birth_chunk(cx: int, cy: int) -> void:
	if _has_birth:
		return
	_has_birth = true
	_birth_chunk = Vector2i(cx, cy)
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
		return

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

	var pending_count: int = 0
	for _p in _pending:
		pending_count += 1

	while not _tile_queue.is_empty() and pending_count < MAX_PENDING_TILES:
		var pos: Vector2i = _tile_queue.pop_front()
		_pending[pos] = true
		_send_request([[pos.x, pos.y]], true)
		pending_count += 1


# ── 内部实现 ──────────────────────────────────────────────

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


func _place_player_at(pos: Vector2, elevation: int) -> void:
	_player_pos = pos
	if _player_elevation != elevation:
		if _player.get_parent():
			_player.get_parent().remove_child(_player)
		_get_layer(elevation).add_child(_player)
		_player_elevation = elevation
	var cell := Vector2i(int(pos.x), int(pos.y))
	var frac := pos - Vector2(cell)
	var layer := _get_layer(elevation)
	var base := layer.map_to_local(cell)
	_player.position = base + _cell_step_x * frac.x + _cell_step_y * frac.y


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


func _process_place_queue() -> void:
	if _place_queue.is_empty():
		return
	var t0: int = Time.get_ticks_usec()
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

	for _i in range(CELLS_PER_FRAME):
		if _place_cursor >= total:
			var dt_done: int = int((Time.get_ticks_usec() - t0) / 1000.0)
			print("[place] chunk (%d,%d) done: %d layers, %dms" % [cx, cy, used_set.size(), dt_done])
			_tiles_loaded[key] = true
			_being_placed.erase(key)
			_place_queue.pop_front()
			_place_cursor = 0
			return
		var y: int = int(_place_cursor / float(CHUNK_SIZE))
		var x: int = _place_cursor % int(CHUNK_SIZE)
		var idx: int = y * CHUNK_SIZE + x
		var tile: int = terrain[idx]
		var elev: int
		if tile == 6 or tile == 7:
			elev = 0
		elif elevation != null:
			elev = int(elevation[idx])
		else:
			elev = TERRAIN_BANDS[tile]
		if not used_set.has(elev):
			used_set[elev] = true
		var atlas_coord: Vector2i = TERRAIN_TILES[tile]
		_get_layer(elev).set_cell(Vector2i(base_x + x, base_y + y), 0, atlas_coord)
		_place_cursor += 1
	var dt_batch: int = int((Time.get_ticks_usec() - t0) / 1000.0)
	print("[place] chunk (%d,%d) batch: cursor=%d, %dms" % [cx, cy, _place_cursor, dt_batch])


func _process_erase_queue() -> void:
	if _erase_queue.is_empty():
		return
	var data: Dictionary = _erase_queue.front()
	var elevs: Array = data["elevations"]
	var bx: int = data["base_x"]
	var by: int = data["base_y"]
	var total: int = CHUNK_SIZE * CHUNK_SIZE * elevs.size()

	for _i in range(CELLS_PER_FRAME):
		if _erase_cursor >= total:
			_erase_queue.pop_front()
			_erase_cursor = 0
			return
		var cell_index: int = int(_erase_cursor / float(elevs.size()))
		var elev_index: int = _erase_cursor % int(elevs.size())
		var elev: int = elevs[elev_index]
		var ty: int = int(cell_index / float(CHUNK_SIZE))
		var tx: int = cell_index % int(CHUNK_SIZE)
		var layer: TileMapLayer = _layers.get(elev, null)
		if layer != null:
			layer.erase_cell(Vector2i(bx + tx, by + ty))
		_erase_cursor += 1


func _unload_chunk(cx: int, cy: int) -> void:
	var key := Vector2i(cx, cy)
	var base_x: int = cx * CHUNK_SIZE
	var base_y: int = cy * CHUNK_SIZE
	var used: Dictionary = _chunk_elevations.get(key, {})
	if not used.is_empty():
		var elevs: Array[int] = []
		for elev in used:
			elevs.append(elev)
		_erase_queue.append({
			"key": key, "base_x": base_x, "base_y": base_y,
			"elevations": elevs,
		})
	_chunk_elevations.erase(key)
	_chunks.erase(key)
	_tiles_loaded.erase(key)
	_tiles_cached.erase(key)
	_being_placed.erase(key)


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


func _on_connected(_host: String, _port: int) -> void:
	pass


func _send_request(coord_array: Array, include_tiles: bool) -> void:
	var payload: Dictionary = {"chunks": coord_array}
	if include_tiles:
		payload["include_tiles"] = true
	Connection.send({
		"type": "request",
		"request_type": "get_chunks",
		"payload": payload,
	})
