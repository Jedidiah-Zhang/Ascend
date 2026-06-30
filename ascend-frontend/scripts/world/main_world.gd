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
## 缩放范围
const CAMERA_ZOOM_MIN: float = 0.15
const CAMERA_ZOOM_MAX: float = 4.0

## 地图显示节点
var _map_display: MapDisplay = null
## 终端节点
var _terminal: TerminalWidget = null


func _ready() -> void:
	"""场景加载时创建子节点、连接信号并发起连接。"""
	# 创建地图显示节点
	_map_display = MapDisplay.new()
	_map_display.name = "MapDisplay"
	add_child(_map_display)

	# 创建终端节点（CanvasLayer 包裹，确保 UI 独立于 2D 世界坐标系）
	var terminal_layer := CanvasLayer.new()
	terminal_layer.name = "TerminalLayer"
	terminal_layer.layer = 100  # 最顶层
	add_child(terminal_layer)

	_terminal = TerminalWidget.new()
	_terminal.name = "TerminalWidget"
	_terminal.remote_command.connect(_on_terminal_command)
	_terminal.map_view_changed.connect(_on_terminal_map_view)
	terminal_layer.add_child(_terminal)

	# 连接网络信号
	Connection.connection_established.connect(_on_connected)
	Connection.connection_lost.connect(_on_disconnected)
	Connection.message_received.connect(_on_message)

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
	"""每帧：控制相机 + 流式地图块 + 处理玩家指令。

	终端打开时跳过相机控制和玩家输入，但保持 Connection 消息处理。
	"""
	# 无条件处理 Connection（即使终端打开，远程指令响应仍需接收）
	if Connection.status != Connection.Status.CONNECTED:
		return

	# 终端打开时跳过相机和玩家输入
	if _terminal and _terminal.is_open():
		return

	_process_camera(delta)
	_process_input()

	# 根据相机位置请求新块
	if _map_display:
		_map_display.stream_chunks_for_viewport()


func _unhandled_input(event: InputEvent) -> void:
	"""处理 '/' 键切换终端。

	Args:
		event: 输入事件。
	"""
	if event is InputEventKey and event.pressed and not event.echo:
		if event.keycode == KEY_SLASH and not event.shift_pressed and not event.ctrl_pressed and not event.alt_pressed:
			if _terminal:
				_terminal.toggle()
			get_viewport().set_input_as_handled()


func _process_camera(delta: float) -> void:
	"""本地相机平移和缩放（不经过后端）。

	Args:
		delta: 帧间隔时间。
	"""
	if _map_display == null:
		return
	var cam: Camera2D = _map_display.get_node_or_null("MapCamera")
	if cam == null:
		return

	# WASD / 方向键平移
	var pan := Input.get_vector("move_left", "move_right", "move_up", "move_down")
	if pan != Vector2.ZERO:
		cam.position += pan * CAMERA_PAN_SPEED * delta / cam.zoom

	# 鼠标滚轮缩放
	var zoom_delta: float = 0.0
	if Input.is_action_just_pressed("zoom_in"):
		zoom_delta = CAMERA_ZOOM_STEP
	elif Input.is_action_just_pressed("zoom_out"):
		zoom_delta = -CAMERA_ZOOM_STEP

	if zoom_delta != 0.0:
		var new_zoom := cam.zoom.x + zoom_delta
		new_zoom = clampf(new_zoom, CAMERA_ZOOM_MIN, CAMERA_ZOOM_MAX)
		cam.zoom = Vector2(new_zoom, new_zoom)


func _process_input() -> void:
	"""读取需要后端处理的玩家指令并发送。"""
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


func _on_terminal_map_view(view_mode: String) -> void:
	"""终端地图视图切换请求。

	Args:
		view_mode: 视图模式（biome/climate/altitude/normal）。
	"""
	if _map_display == null:
		return
	match view_mode:
		"biome":
			_map_display.set_view_mode(MapDisplay.ViewMode.BIOME)
		"climate":
			_map_display.set_view_mode(MapDisplay.ViewMode.CLIMATE)
		"altitude":
			_map_display.set_view_mode(MapDisplay.ViewMode.ALTITUDE)
		_:
			_map_display.set_view_mode(MapDisplay.ViewMode.NORMAL)


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
	"""处理后端推送的事件。

	Args:
		message: 事件消息。
	"""
	var event_type: String = message.get("event_type", "")
	print("MainWorld: event '%s'" % event_type)


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
