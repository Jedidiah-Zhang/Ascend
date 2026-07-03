"""宏观地图显示 — 将块渲染为彩色矩形网格。

拥有一个 Camera2D 子节点。
从后端获取块数据并通过 _draw() 渲染它们。
支持四种视图模式：NORMAL（群系）、BIOME（群系）、CLIMATE（气候）、ALTITUDE（海拔）。
"""

extends Node2D

class_name MapDisplay

## 视图模式枚举
enum ViewMode {
	NORMAL = 0,    ## 默认群系着色
	BIOME = 1,     ## 群系着色（与 NORMAL 相同）
	CLIMATE = 2,   ## 气候区域着色
	ALTITUDE = 3,  ## 海拔等高线着色
}

## 可视参数
const CHUNK_PIXEL_SIZE: int = 48         ## 屏幕上每个块的像素大小
const INITIAL_VIEW_RADIUS: int = 12       ## 初始请求半径（块数）
const STREAM_MARGIN: int = 4              ## 可见区域外的额外块以预取

## 群系颜色（与 tests/web/server.py 保持一致）
const BIOME_COLORS: Dictionary = {
	0:  Color(0.29, 0.49, 0.25),   ## TEMPERATE_DECIDUOUS_FOREST — 绿
	1:  Color(0.10, 0.42, 0.23),   ## TROPICAL_RAINFOREST — 深绿
	2:  Color(0.77, 0.64, 0.24),   ## TROPICAL_SAVANNA — 黄绿
	3:  Color(0.90, 0.78, 0.47),   ## DESERT — 沙黄
	4:  Color(0.72, 0.63, 0.38),   ## STEPPE_SHRUBLAND — 褐黄
	5:  Color(0.23, 0.42, 0.54),   ## TAIGA — 暗青
	6:  Color(0.85, 0.85, 0.91),   ## TUNDRA — 灰白
	7:  Color(0.69, 0.69, 0.75),   ## ALPINE_MEADOW — 灰紫
	10: Color(0.12, 0.42, 0.54),   ## WARM_OCEAN — 深蓝
	11: Color(0.18, 0.42, 0.54),   ## TEMPERATE_OCEAN — 中蓝
	12: Color(0.35, 0.54, 0.67),   ## COLD_OCEAN — 浅蓝
}
const UNKNOWN_CHUNK_COLOR: Color = Color(0.08, 0.08, 0.10)  ## 未请求/加载中的块

## 气候颜色（ClimateZone IntEnum: 0-7 共 8 档）
const CLIMATE_COLORS: Dictionary = {
	0: Color(0.10, 0.42, 0.23),   ## EQUATORIAL_RAINFOREST — 深绿
	1: Color(0.77, 0.64, 0.24),   ## TROPICAL_SAVANNA — 黄绿
	2: Color(0.90, 0.78, 0.47),   ## DESERT — 沙黄
	3: Color(0.72, 0.63, 0.38),   ## STEPPE — 褐黄
	4: Color(0.29, 0.49, 0.25),   ## TEMPERATE_FOREST — 绿
	5: Color(0.23, 0.42, 0.54),   ## SUBARCTIC_TAIGA — 暗青
	6: Color(0.85, 0.85, 0.91),   ## POLAR_TUNDRA — 灰白
	7: Color(0.69, 0.69, 0.75),   ## ALPINE — 灰紫
}

## 海拔等高线: [上限(m), 颜色]
const ALTITUDE_BANDS: Array = [
	[-300,  Color(0.05, 0.15, 0.35)],   ## 深海
	[-100,  Color(0.08, 0.20, 0.40)],   ## 洋底
	[-30,   Color(0.15, 0.35, 0.50)],   ## 浅海
	[0,     Color(0.25, 0.45, 0.55)],   ## 近岸
	[10,    Color(0.30, 0.55, 0.30)],   ## 低地
	[200,   Color(0.35, 0.60, 0.30)],   ##
	[500,   Color(0.50, 0.65, 0.30)],   ## 丘陵
	[1000,  Color(0.60, 0.55, 0.25)],   ## 高地
	[2000,  Color(0.65, 0.40, 0.20)],   ## 山地
	[3500,  Color(0.75, 0.30, 0.15)],   ## 高山
]
const ALTITUDE_BAND_DEFAULT: Color = Color(0.40, 0.30, 0.50)  ## >3500m

## 块缓存: { Vector2i: Dictionary | null }
## null 表示已请求但尚未收到数据
var _chunks: Dictionary = {}
## 待处理块集合: { Vector2i: true }
## Dictionary 用作集合，erase O(1) 而非 Array 的 O(N)
var _pending: Dictionary = {}
## 是否需要重绘
var _dirty: bool = false
## 相机引用
var _camera: Camera2D = null
## 玩家标记位置（块坐标）
var _player_chunk: Vector2i = Vector2i(0, 0)
var _has_player_pos: bool = false

## 当前视图模式，默认群系
var _view_mode: ViewMode = ViewMode.BIOME

## 地图视图变更信号（供 Terminal 等外部使用）
signal map_view_changed(view_mode: int)


func _ready() -> void:
	"""设置相机，等待连接后请求初始块。"""
	# 创建 Camera2D
	_camera = Camera2D.new()
	_camera.name = "MapCamera"
	_camera.anchor_mode = Camera2D.ANCHOR_MODE_DRAG_CENTER
	_camera.position = Vector2.ZERO
	add_child(_camera)

	# 等待连接后请求初始块
	if Connection.status == Connection.Status.CONNECTED:
		_request_initial_chunks()
	else:
		Connection.connection_established.connect(
			_on_connected, CONNECT_ONE_SHOT
		)


func _draw() -> void:
	"""绘制所有缓存的块，根据当前视图模式选择颜色。"""
	for pos in _chunks:
		var data = _chunks[pos]
		var color: Color = UNKNOWN_CHUNK_COLOR
		if data != null:
			color = _get_chunk_color(data)

		var rect := Rect2(
			pos.x * CHUNK_PIXEL_SIZE,
			pos.y * CHUNK_PIXEL_SIZE,
			CHUNK_PIXEL_SIZE,
			CHUNK_PIXEL_SIZE,
		)
		draw_rect(rect, color)

	# 绘制玩家位置标记（始终绘制）
	if _has_player_pos:
		var center := Vector2(
			_player_chunk.x * CHUNK_PIXEL_SIZE + CHUNK_PIXEL_SIZE / 2.0,
			_player_chunk.y * CHUNK_PIXEL_SIZE + CHUNK_PIXEL_SIZE / 2.0,
		)
		var radius: float = CHUNK_PIXEL_SIZE * 0.3
		draw_circle(center, radius, Color(1.0, 0.9, 0.4, 0.9))


## ── 公开方法 ────────────────────────────────────────


func set_view_mode(mode: int) -> void:
	"""设置视图模式并刷新显示。

	切换到 ALTITUDE 模式时，需要重新请求含完整气象数据的块。

	Args:
		mode: ViewMode 枚举值（NORMAL/BIOME/CLIMATE/ALTITUDE）。
	"""
	var new_mode: int = clamp(mode, ViewMode.NORMAL, ViewMode.ALTITUDE)
	if new_mode == _view_mode:
		return

	var old_mode: int = _view_mode
	_view_mode = new_mode
	map_view_changed.emit(_view_mode)

	# 切换到 ALTITUDE 模式：清除缓存，重新请求（需要 force_fields）
	if _view_mode == ViewMode.ALTITUDE and old_mode != ViewMode.ALTITUDE:
		_chunks.clear()
		_pending.clear()
		_request_initial_chunks()
	elif _view_mode != ViewMode.ALTITUDE and old_mode == ViewMode.ALTITUDE:
		# 从 ALTITUDE 切出：清除缓存重新请求（无需 force_fields）
		_chunks.clear()
		_pending.clear()
		_request_initial_chunks()

	_dirty = true
	queue_redraw()


func handle_chunk_response(payload: Dictionary) -> void:
	"""处理后端 get_chunks 响应。

	支持含 force_fields 的扩展数据（altitude/temperature/rainfall）。

	Args:
		payload: 响应消息的 payload 字典，含 chunks 列表。
	"""
	var chunk_list: Array = payload.get("chunks", [])
	for entry in chunk_list:
		var cx: int = entry["cx"]
		var cy: int = entry["cy"]
		var pos := Vector2i(cx, cy)
		if _chunks.has(pos):
			_chunks[pos] = {
				"biome": entry.get("biome", 0),
				"climate": entry.get("climate", 0),
				"passable": entry.get("passable", true),
				# 含 force_fields 时的扩展数据
				"altitude": entry.get("altitude", null),
				"temperature": entry.get("temperature", null),
				"rainfall": entry.get("rainfall", null),
			}
			_pending.erase(pos)

	_dirty = true
	queue_redraw()


func stream_chunks_for_viewport() -> void:
	"""根据当前视图边界请求新块。每帧由 main_world 调用。"""
	if _camera == null or Connection.status != Connection.Status.CONNECTED:
		return

	var view_rect := _get_viewport_chunk_rect()
	var coords: Array = []

	for cx in range(view_rect.position.x, view_rect.end.x):
		for cy in range(view_rect.position.y, view_rect.end.y):
			var pos := Vector2i(cx, cy)
			if not _chunks.has(pos):
				_chunks[pos] = null  ## 标记为待处理
				_pending[pos] = true
				coords.append([cx, cy])

	if not coords.is_empty():
		_send_chunk_request(coords)

	# 卸载超出边界的块（防止内存无限增长）
	_unload_distant_chunks(view_rect)


func update_player_chunk(chunk_pos: Vector2i) -> void:
	"""更新玩家所在的块坐标。

	Args:
		chunk_pos: 玩家当前所在的块坐标。
	"""
	_has_player_pos = true
	_player_chunk = chunk_pos
	_dirty = true
	queue_redraw()


## ── 内部方法 ──────────────────────────────────────────


func _on_connected(_host: String, _port: int) -> void:
	"""连接建立后请求初始地图数据。"""
	_request_initial_chunks()


func _request_initial_chunks() -> void:
	"""请求原点周围的初始块集。"""
	var coords: Array = []
	for cx in range(-INITIAL_VIEW_RADIUS, INITIAL_VIEW_RADIUS + 1):
		for cy in range(-INITIAL_VIEW_RADIUS, INITIAL_VIEW_RADIUS + 1):
			var pos := Vector2i(cx, cy)
			if not _chunks.has(pos):
				_chunks[pos] = null  ## 标记为待处理
				_pending[pos] = true
				coords.append([cx, cy])

	if not coords.is_empty():
		_send_chunk_request(coords)


func _send_chunk_request(coords: Array) -> void:
	"""向后端发送块请求。

	ALTITUDE 视图模式时发送 force_fields=true 以获取海拔数据。

	Args:
		coords: [[cx, cy], ...] 格式的坐标列表。
	"""
	var payload: Dictionary = {"chunks": coords}
	if _view_mode == ViewMode.ALTITUDE:
		payload["force_fields"] = true

	Connection.send({
		"type": "request",
		"request_type": "get_chunks",
		"payload": payload,
	})


func _get_viewport_chunk_rect() -> Rect2i:
	"""计算覆盖当前可见区域（含边距）所需的块范围。

	Returns:
		块坐标空间中的矩形区域。
	"""
	var view_size := get_viewport_rect().size / _camera.zoom
	var top_left := _camera.get_screen_center_position() - view_size / 2.0
	var bottom_right := _camera.get_screen_center_position() + view_size / 2.0

	var min_cx := floori(top_left.x / CHUNK_PIXEL_SIZE) - STREAM_MARGIN
	var min_cy := floori(top_left.y / CHUNK_PIXEL_SIZE) - STREAM_MARGIN
	var max_cx := ceili(bottom_right.x / CHUNK_PIXEL_SIZE) + STREAM_MARGIN
	var max_cy := ceili(bottom_right.y / CHUNK_PIXEL_SIZE) + STREAM_MARGIN

	return Rect2i(min_cx, min_cy, max_cx - min_cx, max_cy - min_cy)


func _unload_distant_chunks(view_rect: Rect2i) -> void:
	"""移除远离当前视口的块以释放内存。

	Args:
		view_rect: 当前视口覆盖的块范围。
	"""
	var unload_margin := STREAM_MARGIN * 3
	var unload_rect := Rect2i(
		view_rect.position.x - unload_margin,
		view_rect.position.y - unload_margin,
		view_rect.size.x + 2 * unload_margin,
		view_rect.size.y + 2 * unload_margin,
	)

	var to_remove: Array = []
	for pos in _chunks:
		if not unload_rect.has_point(pos):
			to_remove.append(pos)

	for pos in to_remove:
		_chunks.erase(pos)
		_pending.erase(pos)


## ── 颜色选择 ────────────────────────────────────────


func _get_chunk_color(data: Dictionary) -> Color:
	"""根据当前视图模式从块数据中提取颜色。

	Args:
		data: 块数据字典，含 biome/climate/altitude 等字段。

	Returns:
		对应视图模式的颜色值。
	"""
	match _view_mode:
		ViewMode.CLIMATE:
			return _get_climate_color(data)
		ViewMode.ALTITUDE:
			return _get_altitude_color(data)
		_:  # NORMAL, BIOME
			return _get_biome_color(data)


func _get_biome_color(data: Dictionary) -> Color:
	"""从块数据中获取群系颜色。

	Args:
		data: 块数据字典。

	Returns:
		群系颜色或 UNKNOWN_CHUNK_COLOR。
	"""
	var biome_id: int = data.get("biome", 0)
	return BIOME_COLORS.get(biome_id, UNKNOWN_CHUNK_COLOR)


func _get_climate_color(data: Dictionary) -> Color:
	"""从块数据中获取气候颜色。

	Args:
		data: 块数据字典。

	Returns:
		气候颜色或 UNKNOWN_CHUNK_COLOR。
	"""
	var climate_id: int = data.get("climate", 0)
	return CLIMATE_COLORS.get(climate_id, UNKNOWN_CHUNK_COLOR)


func _get_altitude_color(data: Dictionary) -> Color:
	"""从块数据中获取海拔等高线颜色。

	Args:
		data: 块数据字典，含 altitude 字段。

	Returns:
		海拔颜色或 UNKNOWN_CHUNK_COLOR。
	"""
	var altitude: Variant = data.get("altitude", null)
	if altitude == null:
		return UNKNOWN_CHUNK_COLOR

	var alt_float: float = altitude
	for band in ALTITUDE_BANDS:
		var max_alt: float = band[0]
		var color: Color = band[1]
		if alt_float <= max_alt:
			return color

	return ALTITUDE_BAND_DEFAULT
