"""主世界场景 — 游戏世界入口。

负责:
  - 管理 MapDisplay 子节点（地图显示 + 相机）
  - 管理 TerminalWidget 子节点（调试终端）
  - 监听 Connection 信号（连接状态 + 后端消息）
  - 本地相机控制（平移/缩放）+ 玩家指令发送
"""

extends Node2D

## 相机平移速度（像素/秒）
const CAMERA_PAN_SPEED: float = 600.0
## 缩放步长
const CAMERA_ZOOM_STEP: float = 0.15
## 缩放范围Invalid call. Nonexistent function 'create_tile' in base 'TileSet'.
const CAMERA_ZOOM_MIN: float = 0.15
const CAMERA_ZOOM_MAX: float = 4.0

## 地图显示节点
@onready var _map_display: MapDisplay = $World/MapDisplay
## 终端节点
@onready var _terminal: TerminalWidget = $TerminalLayer/TerminalWidget
## 地图相机（从 MapDisplay 缓存，避免每帧 get_node）
@onready var _map_camera: Camera2D = $World/MapDisplay/MapCamera
## 调试信息覆盖层
@onready var _debug_overlay: DebugOverlay = $DebugLayer/DebugOverlay
## 事件日志面板
@onready var _event_log: EventLog = $DebugLayer/EventLog

## 当前游戏时间（时），来自 minute_change 事件，用于事件日志时间戳
var _current_game_hour: int = -1
## 当前游戏时间（分）
var _current_game_minute: int = -1
## 上次显示的日期，仅变更时插入分隔线
var _current_game_day: int = -1


func _ready() -> void:
	"""场景加载时连接信号并发起连接。"""
	_terminal.remote_command_submitted.connect(_on_terminal_command)

	Connection.connection_established.connect(_on_connected)
	Connection.connection_lost.connect(_on_disconnected)
	Connection.message_received.connect(_on_message)

	_setup_debug_sections()

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
	"""每帧：控制相机 + 流式地图块 + 处理玩家指令 + 更新调试覆盖层。

	终端打开时跳过相机控制和玩家输入，但保持 Connection 消息处理。
	"""
	# 始终更新调试覆盖层（独立于连接状态和终端状态）
	if _debug_overlay and _debug_overlay.is_shown():
		_update_debug_sections()

	# 无条件处理 Connection（即使终端打开，远程指令响应仍需接收）
	if Connection.status != Connection.Status.CONNECTED:
		return

	# 终端打开时跳过相机和玩家输入
	if _terminal and _terminal.is_open():
		return

	_process_camera(delta)
	_process_input(delta)

	# 根据相机位置请求新块
	if _map_display:
		_map_display.stream_chunks_for_viewport()


func _unhandled_input(event: InputEvent) -> void:
	"""处理快捷键：'/' 切换终端、F3 切换调试覆盖层。

	Args:
		event: 输入事件。
	"""
	if event is InputEventKey and event.pressed and not event.echo:
		if event.keycode == KEY_SLASH and not event.shift_pressed and not event.ctrl_pressed and not event.alt_pressed:
			if _terminal:
				_terminal.toggle()
			get_viewport().set_input_as_handled()

		if event.keycode == KEY_F3:
			if _debug_overlay:
				_debug_overlay.toggle()
			if _event_log:
				if _debug_overlay and _debug_overlay.is_shown():
					_event_log.show()
				else:
					_event_log.hide()
			get_viewport().set_input_as_handled()


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

func _setup_debug_sections() -> void:
	"""注册所有调试分区到覆盖层。"""
	_debug_overlay.add_section(FPSSection.new())
	_debug_overlay.add_section(MemorySection.new())
	_debug_overlay.add_section(TimeSection.new())
	_debug_overlay.add_section(ConnectionSection.new())
	_debug_overlay.add_section(CameraSection.new())
	_debug_overlay.add_section(PlayerSection.new())
	_debug_overlay.add_section(ClimateSection.new())
	_debug_overlay.add_section(WeatherSection.new())
	_debug_overlay.add_section(ChunkSection.new())
	_debug_overlay.add_section(ElevationSection.new())


func _update_debug_sections() -> void:
	"""每帧推送动态数据到调试分区。"""
	if _map_display == null:
		return

	var mcam: Camera2D = _map_display.get_camera()
	if mcam:
		var cam_section: CameraSection = _debug_overlay.get_section("相机")
		if cam_section:
			cam_section.position = mcam.global_position
			cam_section.zoom = mcam.zoom

	var player_pos: Vector2 = _map_display.get_player_pos()

	var player_section: PlayerSection = _debug_overlay.get_section("玩家")
	if player_section:
		player_section.world_pos = player_pos
		player_section.elevation = _map_display.get_player_elevation()

	var chunk_section: ChunkSection = _debug_overlay.get_section("区块")
	if chunk_section:
		var stats: Dictionary = _map_display.get_chunk_stats()
		chunk_section.loaded_count = stats["loaded"]
		chunk_section.being_placed_count = stats["placing"]
		chunk_section.cached_count = stats["cached"]
		chunk_section.pending_count = stats["pending"]

	var elev_section: ElevationSection = _debug_overlay.get_section("地形")
	if elev_section:
		var elev: float = _map_display.get_elevation_at(player_pos)
		if elev > -998.0:
			elev_section.update_from_backend({"elevation": int(elev)})
		var slope: float = _map_display.get_slope_at(player_pos)
		if slope > -998.0:
			elev_section.update_from_backend({"slope": slope})

	var climate_section: ClimateSection = _debug_overlay.get_section("气候")
	if climate_section:
		var temp: float = _map_display.get_chunk_temperature(player_pos)
		if temp > -998.0:
			climate_section.update_from_backend({"temperature": temp})
		var hum: float = _map_display.get_chunk_humidity(player_pos)
		if hum > -998.0:
			climate_section.update_from_backend({"humidity": hum})


func _on_connected(host: String, port: int) -> void:
	"""连接成功回调。

	Args:
		host: 服务器地址。
		port: 端口号。
	"""
	print("MainWorld: connected to %s:%d" % [host, port])


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

	if _debug_overlay == null:
		return

	# 格式化时间戳
	var ts: String = "--:--"
	if _current_game_hour >= 0:
		ts = "%02d:%02d" % [_current_game_hour, _current_game_minute]

	match event_type:
		"minute_change":
			_current_game_hour = int(data.get("hour", 0))
			_current_game_minute = int(data.get("minute", 0))
			ts = "%02d:%02d" % [_current_game_hour, _current_game_minute]
			var time_section: TimeSection = _debug_overlay.get_section("时间")
			if time_section:
				time_section.update_from_backend(data)
			# 仅在日期变更时显示分隔线
			var day: int = int(data.get("day", 0))
			if day != _current_game_day:
				_current_game_day = day
				_push_log("[%s] ── 第%d天 ──" % [ts, day])

		"temperature_change":
			var section: ClimateSection = _debug_overlay.get_section("气候")
			if section:
				section.update_from_backend({"temperature": data.get("temperature", 0.0)})
			_push_log("[%s] 温度 %.1f°C" % [ts, data.get("temperature", 0.0)])

		"humidity_change":
			var section: ClimateSection = _debug_overlay.get_section("气候")
			if section:
				section.update_from_backend({"humidity": data.get("humidity", 0.0)})
			_push_log("[%s] 湿度 %.0f%%" % [ts, data.get("humidity", 0.0)])

		"wind_change":
			_push_log("[%s] 风速 %.1f m/s" % [ts, data.get("wind_speed", 0.0)])

		"sunshine_change":
			_push_log("[%s] 日照 %.1fh" % [ts, data.get("sunshine", 0.0)])

		"precipitation_start":
			var section: WeatherSection = _debug_overlay.get_section("天气")
			if section:
				var ptype: String = data.get("precip_type", "")
				var intensity: float = data.get("intensity", 0.0)
				section.update_from_backend({"weather": "%s (%.1f mm/h)" % [ptype, intensity]})
			_push_log("[%s] %s %.1fmm/h" % [ts, data.get("precip_type", ""), data.get("intensity", 0.0)])

		"precipitation_stop":
			var section: WeatherSection = _debug_overlay.get_section("天气")
			if section:
				section.update_from_backend({"weather": "晴"})
			_push_log("[%s] 雨停" % ts)


func _push_log(line: String) -> void:
	"""推入一行事件到右侧日志面板。"""
	if _event_log:
		_event_log.push_event(line)


func _handle_response(message: Dictionary) -> void:
	"""处理后端对请求的响应。

	Args:
		message: 响应消息。
	"""
	var request_type: String = message.get("request_type", "")
	var payload: Dictionary = message.get("payload", {})

	match request_type:
		"get_chunks":
			if _map_display:
				_map_display.handle_chunk_response(payload)
		"terminal_cmd":
			if _terminal:
				var output: String = payload.get("output", "")
				_terminal.write(output)
		_:
			print("MainWorld: response for '%s'" % request_type)


func _handle_error(message: Dictionary) -> void:
	"""处理后端错误。

	Args:
		message: 错误消息。
	"""
	var error_msg: String = message.get("error", "unknown error")
	push_error("MainWorld: server error: %s" % error_msg)
