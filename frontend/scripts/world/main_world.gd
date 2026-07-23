"""主世界场景 — 游戏世界入口。

负责:
  - 管理 MapDisplay 子节点（地图显示 + 相机）
  - 管理 TerminalWidget 子节点（调试终端）
  - 监听 Connection 信号（连接状态 + 后端消息）
  - 本地相机控制（平移/缩放）+ 玩家指令发送
"""

extends Node2D

const Config = preload("res://scripts/config.gd")

const CAMERA_PAN_SPEED: float = Config.CAMERA_PAN_SPEED
## 缩放步长
const CAMERA_ZOOM_STEP: float = Config.CAMERA_ZOOM_STEP
## 缩放范围
const CAMERA_ZOOM_MIN: float = Config.CAMERA_ZOOM_MIN
const CAMERA_ZOOM_MAX: float = Config.CAMERA_ZOOM_MAX

## 地图显示节点
@onready var _map_display: MapDisplay = $World/MapDisplay
## 实体视图工厂
@onready var _entity_layer: EntityLayer = $World/MapDisplay/EntityLayer
## 终端节点
@onready var _terminal: TerminalWidget = $TerminalLayer/TerminalWidget
## 地图相机（从 MapDisplay 缓存，避免每帧 get_node）
@onready var _map_camera: Camera2D = $World/MapDisplay/MapCamera
## 调试信息覆盖层
@onready var _debug_overlay: DebugOverlay = $DebugLayer/DebugOverlay
## 事件日志面板
@onready var _event_log: EventLog = $DebugLayer/EventLog


func _ready() -> void:
	"""场景加载时连接信号并发起连接。"""
	_terminal.remote_command_submitted.connect(_on_terminal_command)

	Connection.connection_established.connect(_on_connected)
	Connection.connection_lost.connect(_on_disconnected)
	Connection.message_received.connect(_on_message)

	_setup_debug_overlay()

	Connection.connect_to_server()


func _exit_tree() -> void:
	"""场景退出时断开信号。"""
	if Connection.connection_established.is_connected(_on_connected):
		Connection.connection_established.disconnect(_on_connected)
	if Connection.connection_lost.is_connected(_on_disconnected):
		Connection.connection_lost.disconnect(_on_disconnected)
	if Connection.message_received.is_connected(_on_message):
		Connection.message_received.disconnect(_on_message)


func _process(delta: float) -> void:
	"""每帧：控制相机 + 流式地图块 + 处理玩家指令 + 调度调试覆盖层。

	终端打开时跳过相机控制和玩家输入，但保持 Connection 消息处理。
	"""
	# 调度调试覆盖层（独立于连接状态和终端状态）
	if _debug_overlay and _debug_overlay.is_shown():
		_debug_overlay.process_sections(delta)

	# 无条件处理 Connection（即使终端打开，远程指令响应仍需接收）
	if Connection.status != Connection.Status.CONNECTED:
		return

	# 终端打开时跳过相机和玩家输入
	if _terminal and _terminal.is_open():
		return

	_process_camera(delta)
	_process_input(delta)

	_map_display.stream_chunks_for_viewport()


func _unhandled_input(event: InputEvent) -> void:
	"""处理快捷键（F3 和 '/' 现由 DebugOverlay/TerminalWidget 自管理）。"""
	pass


func _process_camera(_delta: float) -> void:
	"""只处理缩放。平移已由玩家移动替代，相机跟随玩家。"""
	if _map_camera == null:
		return

	var zoom_delta: float = 0.0
	if Input.is_action_just_pressed("zoom_in"):
		zoom_delta = CAMERA_ZOOM_STEP
	elif Input.is_action_just_pressed("zoom_out"):
		zoom_delta = -CAMERA_ZOOM_STEP

	if zoom_delta != 0.0:
		var new_zoom := _map_camera.zoom.x + zoom_delta
		new_zoom = clampf(new_zoom, CAMERA_ZOOM_MIN, CAMERA_ZOOM_MAX)
		_map_camera.zoom = Vector2(new_zoom, new_zoom)


func _process_input(delta: float) -> void:
	"""读取需要后端处理的玩家指令并发送。"""
	# WASD 移动玩家实体
	var move_input := Input.get_vector("move_left", "move_right", "move_up", "move_down")
	if move_input != Vector2.ZERO and _map_display:
		_map_display.move_player(move_input, delta)

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
	"""终端远程指令发送。

	Args:
		command: 完整的指令文本。
	"""
	Connection.send({
		"type": "request",
		"request_type": "terminal_cmd",
		"payload": {"command": command},
	})


# ── 调试覆盖层 ──────────────────────────────────────────────

func _setup_debug_overlay() -> void:
	_debug_overlay.setup_default_sections(self)


# ── 调试数据 getter（供 DebugSection 自行拉取）────────────

func get_debug_camera_info() -> Dictionary:
	if _map_camera == null:
		return {}
	return {
		"position": _map_camera.global_position,
		"camera_display": "缩放: %.2fx" % _map_camera.zoom.x,
	}


func get_debug_player_info() -> Dictionary:
	var player_pos := Vector2.ZERO
	var chunk := Vector2i.ZERO
	var elev: float = 0.0
	if _map_display:
		player_pos = _map_display.get_player_pos()
		chunk = Vector2i(
			floori(player_pos.x / Config.TILE_MAP_SIZE),
			floori(player_pos.y / Config.TILE_MAP_SIZE))
		elev = _map_display.get_player_elevation()
	return {
		"world_pos": player_pos,
		"chunk": chunk,
		"elevation": elev,
	}


func get_debug_terrain_at(world_pos: Vector2) -> Dictionary:
	if _map_display == null:
		return {}
	var result: Dictionary = {}
	var elev: float = _map_display.get_elevation_at(world_pos)
	if elev > -998.0:
		result["elevation"] = int(elev)
	var slope: float = _map_display.get_slope_at(world_pos)
	if slope > -998.0:
		result["slope"] = slope
	return result


func get_debug_climate_at(world_pos: Vector2) -> Dictionary:
	if _map_display == null:
		return {}
	var result: Dictionary = {}
	var temp: float = _map_display.get_chunk_temperature(world_pos)
	if temp > -998.0:
		result["temperature"] = temp
	var hum: float = _map_display.get_chunk_humidity(world_pos)
	if hum > -998.0:
		result["humidity"] = hum
	var cz: int = _map_display.get_chunk_climate(world_pos)
	if cz >= 0:
		result["climate_zone"] = cz
	return result


func get_debug_chunk_stats() -> Dictionary:
	if _map_display == null:
		return {}
	return _map_display.get_chunk_stats()


func get_debug_timing() -> Dictionary:
	if _map_display == null:
		return {}
	var timing: Dictionary = _map_display.get_timing()
	timing["conn"] = Connection.last_process_us
	return timing


func _on_connected(host: String, port: int) -> void:
	"""连接成功回调：拉取实体快照并请求玩家控制的实体。

	状态通道初始化顺序（Issue #20）：
	  1. entity_snapshot — 全量实体视图（含玩家实体）
	  2. player_state — 标记本地控制的 entity_id + 权威位置吸附
	此后实体视图靠 entity_born/died/moved 事件增量维护。

	Args:
		host: 服务器地址。
		port: 端口号。
	"""
	print("MainWorld: connected to %s:%d" % [host, port])
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
	"""连接断开回调。"""
	print("MainWorld: disconnected")


func _on_message(message: Dictionary) -> void:
	"""处理后端消息。

	Args:
		message: 后端发来的消息字典。
	"""
	var msg_type: String = message.get("type", "")

	match msg_type:
		"event":
			_handle_event(message)
		"response":
			_handle_response(message)
		"error":
			_handle_error(message)
		_:
			push_warning("MainWorld: unknown message type: %s" % msg_type)


func _handle_event(message: Dictionary) -> void:
	"""处理后端推送的事件并路由到调试分区和事件日志。

	Args:
		message: 事件消息。
	"""
	var event_type: String = message.get("event_type", "")

	var payload: Dictionary = message.get("payload", {})
	var data: Dictionary = payload.get("data", {})

	# 玩家吸附不依赖调试覆盖层，先于 overlay 检查处理
	if event_type == "player_teleported":
		if _map_display:
			_map_display.teleport_player(Vector2(
				float(data.get("x", 0.0)), float(data.get("y", 0.0))))
		if _event_log:
			_event_log.push_event("[%02d:%02d] 传送至 (%.0f, %.0f)" % [
				payload.get("game_hour", 0), payload.get("game_minute", 0),
				float(data.get("x", 0.0)), float(data.get("y", 0.0))])
		return

	match event_type:
		"entity_born":
			if _entity_layer:
				_entity_layer.on_entity_born(data)
			if _event_log:
				_event_log.push_event("[%02d:%02d] %s 诞生 (%.0f, %.0f)" % [
					payload.get("game_hour", 0), payload.get("game_minute", 0),
					str(data.get("entity_type", "?")),
					float(data.get("x", 0.0)), float(data.get("y", 0.0))])
			return
		"entity_died":
			if _entity_layer:
				_entity_layer.on_entity_died(data)
			if _event_log:
				_event_log.push_event("[%02d:%02d] %s 死亡" % [
					payload.get("game_hour", 0), payload.get("game_minute", 0),
					str(data.get("entity_type", "?"))])
			return
		"entity_moved":
			if _entity_layer:
				_entity_layer.on_entity_moved(data)
			return

	if _debug_overlay:
		_debug_overlay.broadcast_event(event_type, payload)

	if _event_log:
		_event_log.on_world_event(event_type, payload)


func _handle_response(message: Dictionary) -> void:
	"""处理后端对请求的响应。

	Args:
		message: 响应消息。
	"""
	var request_type: String = message.get("request_type", "")
	var payload: Dictionary = message.get("payload", {})

	# 广播到所有调试分区（Section 按其关心的 request_type 自行过滤）
	if _debug_overlay:
		_debug_overlay.broadcast_response(request_type, payload)

	match request_type:
		"get_chunks":
			if _map_display:
				_map_display.handle_chunk_response(payload)
		"entity_snapshot":
			if _entity_layer:
				_entity_layer.apply_snapshot(payload.get("entities", []))
		"player_state":
			if _entity_layer:
				_entity_layer.set_local_entity(str(payload.get("entity_id", "")))
			if _map_display:
				_map_display.handle_player_state(payload)
		"player_move":
			pass  # 壳子阶段权威=上报值，无需纠正；后端加校验后在此吸附
		"terminal_cmd":
			if _terminal:
				var output: String = payload.get("output", "")
				_terminal.write(output)
		_:
			pass


func _handle_error(message: Dictionary) -> void:
	"""处理后端错误。

	Args:
		message: 错误消息。
	"""
	var error_msg: String = message.get("error", "unknown error")
	push_error("MainWorld: server error: %s" % error_msg)
