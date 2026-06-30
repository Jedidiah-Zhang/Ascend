"""宏观地图显示 — 将块渲染为彩色矩形网格。

拥有一个 Camera2D 子节点。
从后端获取块数据并通过 _draw() 渲染它们。
"""

extends Node2D

class_name MapDisplay

## 可视参数
const CHUNK_PIXEL_SIZE: int = 48         ## 屏幕上每个块的像素大小
const INITIAL_VIEW_RADIUS: int = 12       ## 初始请求半径（块数）
const STREAM_MARGIN: int = 4              ## 可见区域外的额外块以预取

## 群系颜色（与 tests/web/server.py 保持一致）
const BIOME_COLORS: Dictionary = {
    0:  Color(0.29, 0.49, 0.25),   ## TEMPERATE_DECIDUOUS_FOREST — 绿色
    1:  Color(0.77, 0.64, 0.24),   ## ARID_SHRUBLAND — 黄色
    10: Color(0.12, 0.42, 0.54),   ## WARM_OCEAN — 深蓝
    11: Color(0.18, 0.42, 0.54),   ## TEMPERATE_OCEAN — 中蓝
    12: Color(0.35, 0.54, 0.67),   ## COLD_OCEAN — 浅蓝
}
const UNKNOWN_CHUNK_COLOR: Color = Color(0.08, 0.08, 0.10)  ## 未请求/加载中的块

## 块缓存: { Vector2i: Dictionary | null }
## null 表示已请求但尚未收到数据
var _chunks: Dictionary = {}
## 待处理块列表: Array[Vector2i]
var _pending: Array = []
## 是否需要重绘
var _dirty: bool = false
## 相机引用
var _camera: Camera2D = null
## 玩家标记位置（块坐标）
var _player_chunk: Vector2i = Vector2i(0, 0)
var _has_player_pos: bool = false


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
    """绘制所有缓存的块为彩色矩形。"""
    for pos in _chunks:
        var data = _chunks[pos]
        var color: Color = UNKNOWN_CHUNK_COLOR
        if data != null:
            var biome_id: int = data.get("biome", 0)
            color = BIOME_COLORS.get(biome_id, UNKNOWN_CHUNK_COLOR)

        var rect := Rect2(
            pos.x * CHUNK_PIXEL_SIZE,
            pos.y * CHUNK_PIXEL_SIZE,
            CHUNK_PIXEL_SIZE,
            CHUNK_PIXEL_SIZE,
        )
        draw_rect(rect, color)

    # 绘制玩家位置标记
    if _has_player_pos:
        var center := Vector2(
            _player_chunk.x * CHUNK_PIXEL_SIZE + CHUNK_PIXEL_SIZE / 2.0,
            _player_chunk.y * CHUNK_PIXEL_SIZE + CHUNK_PIXEL_SIZE / 2.0,
        )
        var radius: float = CHUNK_PIXEL_SIZE * 0.3
        draw_circle(center, radius, Color(1.0, 0.9, 0.4, 0.9))


## ── 公开方法 ────────────────────────────────────────


func handle_chunk_response(payload: Dictionary) -> void:
    """处理后端 get_chunks 响应。

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
                _pending.append(pos)
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
                _pending.append(pos)
                coords.append([cx, cy])

    if not coords.is_empty():
        _send_chunk_request(coords)


func _send_chunk_request(coords: Array) -> void:
    """向后端发送块请求。

    Args:
        coords: [[cx, cy], ...] 格式的坐标列表。
    """
    Connection.send({
        "type": "request",
        "request_type": "get_chunks",
        "payload": {"chunks": coords},
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
