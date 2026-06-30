"""Connection 单例 — 管理与 Python 后端的 TCP 连接。

协议:
  每条消息 = 4 字节大端长度前缀 + JSON 体
  消息格式: {type, seq, payload, ...}

信号:
  connection_established(host: String, port: int)
  connection_lost()
  message_received(message: Dictionary)

Usage:
  Connection.send({"type": "request", "request_type": "ping", "seq": 0, "payload": {}})
  Connection.connection_established.connect(_on_connected)
  Connection.message_received.connect(_on_message)
"""

extends Node

## 默认连接参数
const DEFAULT_HOST: String = "127.0.0.1"
const DEFAULT_PORT: int = 9081
const RECONNECT_INTERVAL: float = 2.0
const MAX_MESSAGE_SIZE: int = 16 * 1024 * 1024  # 16 MiB

## 连接状态
enum Status { DISCONNECTED, CONNECTING, CONNECTED }
var status: Status = Status.DISCONNECTED

## 信号：连接建立
signal connection_established(host: String, port: int)
## 信号：连接断开
signal connection_lost()
## 信号：收到消息
signal message_received(message: Dictionary)

## 接收缓冲区
var _recv_buf: PackedByteArray = PackedByteArray()
## 待发送消息队列
var _send_queue: Array[PackedByteArray] = []
## TCP 流
var _stream: StreamPeerTCP = null
## 当前序列号
var _seq: int = 0
## 重连计时器
var _reconnect_timer: float = 0.0
## 目标主机/端口
var _host: String = DEFAULT_HOST
var _port: int = DEFAULT_PORT


func _ready() -> void:
	"""自动加载初始化。"""
	set_process(false)


func _process(delta: float) -> void:
	"""每帧轮询：检查连接、读数据、发数据。"""
	match status:
		Status.DISCONNECTED:
			_reconnect_timer -= delta
			if _reconnect_timer <= 0.0:
				_connect()
		Status.CONNECTING:
			_poll_connection()
		Status.CONNECTED:
			_poll_connection()
			if status == Status.CONNECTED:
				_read_messages()
				_flush_send_queue()


func connect_to_server(host: String = DEFAULT_HOST, port: int = DEFAULT_PORT) -> void:
	"""连接到指定服务器。

	Args:
		host: 服务器地址
		port: 服务器端口
	"""
	_host = host
	_port = port
	if status == Status.CONNECTED:
		disconnect_from_server()
	_connect()


func disconnect_from_server() -> void:
	"""断开连接。"""
	if _stream:
		_stream.disconnect_from_host()
		_stream = null
	status = Status.DISCONNECTED
	_recv_buf.clear()
	_send_queue.clear()
	set_process(false)
	connection_lost.emit()


func send(message: Dictionary) -> void:
	"""发送一条消息。

	Args:
		message: 消息字典，会自动添加 seq（如未提供）
	"""
	if not message.has("seq"):
		message["seq"] = _next_seq()
	var encoded: PackedByteArray = MsgPack.encode(message)
	if encoded.is_empty():
		push_error("Connection: failed to encode message")
		return
	# 4 字节大端长度前缀
	var length: int = encoded.size()
	var framed: PackedByteArray = PackedByteArray()
	framed.append((length >> 24) & 0xff)
	framed.append((length >> 16) & 0xff)
	framed.append((length >> 8) & 0xff)
	framed.append(length & 0xff)
	framed.append_array(encoded)
	_send_queue.append(framed)


func _connect() -> void:
	"""发起 TCP 连接。"""
	_stream = StreamPeerTCP.new()
	var err: Error = _stream.connect_to_host(_host, _port)
	if err != OK:
		push_warning("Connection: connect error: %d, retrying in %.1fs" % [err, RECONNECT_INTERVAL])
		_stream = null
		_reconnect_timer = RECONNECT_INTERVAL
		return
	status = Status.CONNECTING
	set_process(true)


func _poll_connection() -> void:
	"""轮询连接状态。"""
	if _stream == null:
		return
	_stream.poll()
	var s: StreamPeerTCP.Status = _stream.get_status()
	match s:
		StreamPeerTCP.STATUS_CONNECTED:
			if status == Status.CONNECTING:
				status = Status.CONNECTED
				connection_established.emit(_host, _port)
		StreamPeerTCP.STATUS_CONNECTING:
			pass
		_:
			push_warning("Connection: lost, status=%d" % s)
			_stream = null
			status = Status.DISCONNECTED
			_reconnect_timer = RECONNECT_INTERVAL
			connection_lost.emit()


func _read_messages() -> void:
	"""读取所有可用数据，解析完整消息。"""
	if _stream == null:
		return

	var available: int = _stream.get_available_bytes()
	if available <= 0:
		return

	var chunk: Array = _stream.get_partial_data(available)
	if chunk[0] != OK:
		return
	var data: PackedByteArray = chunk[1]
	_recv_buf.append_array(data)

	# 解析长度前缀帧
	while _recv_buf.size() >= 4:
		var msg_len: int = (_recv_buf[0] << 24) | (_recv_buf[1] << 16) | (_recv_buf[2] << 8) | _recv_buf[3]
		if msg_len <= 0 or msg_len > MAX_MESSAGE_SIZE:
			push_error("Connection: invalid message length: %d" % msg_len)
			_recv_buf.clear()
			return
		if _recv_buf.size() < 4 + msg_len:
			break
		var body: PackedByteArray = PackedByteArray()
		for i in range(msg_len):
			body.append(_recv_buf[4 + i])
		_recv_buf = _recv_buf.slice(4 + msg_len)
		_dispatch(body)


func _flush_send_queue() -> void:
	"""发送队列中的消息。"""
	if _stream == null or _send_queue.is_empty():
		return
	for frame in _send_queue:
		var err: Error = _stream.put_data(frame)
		if err != OK:
			push_error("Connection: send error: %d" % err)
			return
	_send_queue.clear()


func _dispatch(body: PackedByteArray) -> void:
	"""分发收到的消息。

	Args:
		body: 已解码的消息体字节
	"""
	var message: Variant = MsgPack.decode(body)
	if message == null or not message is Dictionary:
		push_error("Connection: invalid message: %s" % body.get_string_from_utf8().left(200))
		return
	message_received.emit(message)


func _next_seq() -> int:
	"""生成下一个序列号。

	Returns:
		递增的序列号
	"""
	_seq += 1
	return _seq
