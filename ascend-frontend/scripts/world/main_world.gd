"""主世界场景 — 游戏世界入口。

负责:
  - 监听 Connection 信号（连接状态 + 后端消息）
  - 处理玩家输入并发送给后端
"""

extends Node2D


func _ready() -> void:
	"""场景加载时连接后端信号并发起连接。"""
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


func _process(_delta: float) -> void:
	"""处理玩家输入并发送给后端。"""
	if Connection.status != Connection.Status.CONNECTED:
		return
	_process_input()


func _process_input() -> void:
	"""读取输入动作，打包发送。"""
	var input_dir: Vector2 = Input.get_vector("move_left", "move_right", "move_up", "move_down")
	if input_dir != Vector2.ZERO:
		Connection.send({
			"type": "request",
			"request_type": "player_move",
			"payload": {"direction": [input_dir.x, input_dir.y]}
		})

	if Input.is_action_just_pressed("interact"):
		Connection.send({
			"type": "request",
			"request_type": "player_interact",
			"payload": {}
		})

	if Input.is_action_just_pressed("zoom_in"):
		Connection.send({
			"type": "request",
			"request_type": "camera_zoom",
			"payload": {"delta": 0.1}
		})

	if Input.is_action_just_pressed("zoom_out"):
		Connection.send({
			"type": "request",
			"request_type": "camera_zoom",
			"payload": {"delta": -0.1}
		})

	if Input.is_action_just_pressed("menu"):
		Connection.send({
			"type": "request",
			"request_type": "open_menu",
			"payload": {}
		})


func _on_connected(host: String, port: int) -> void:
	"""连接成功回调。"""
	print("MainWorld: connected to %s:%d" % [host, port])


func _on_disconnected() -> void:
	"""连接断开回调。"""
	print("MainWorld: disconnected")


func _on_message(message: Dictionary) -> void:
	"""处理后端消息。

	Args:
		message: 后端发来的消息字典
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
		message: 事件消息
	"""
	var event_type: String = message.get("event_type", "")
	print("MainWorld: event '%s'" % event_type)


func _handle_response(message: Dictionary) -> void:
	"""处理后端对请求的响应。

	Args:
		message: 响应消息
	"""
	var request_type: String = message.get("request_type", "")
	print("MainWorld: response for '%s'" % request_type)


func _handle_error(message: Dictionary) -> void:
	"""处理后端错误。

	Args:
		message: 错误消息
	"""
	var error_msg: String = message.get("error", "unknown error")
	push_error("MainWorld: server error: %s" % error_msg)
