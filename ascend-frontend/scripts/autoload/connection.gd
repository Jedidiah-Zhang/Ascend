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

## -- 后端进程管理 --
## Python 虚拟环境相对路径（相对于项目根目录）
const VENV_PYTHON_REL: String = ".venv/bin/python"
## 后端脚本相对于项目根目录的路径
const BACKEND_SCRIPT_REL: String = "ascend-backend/run_server.py"
## 后端启动后等待端口就绪的超时时间（秒）
const BACKEND_STARTUP_TIMEOUT: float = 10.0

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

## 后端进程 PID（-1 表示未启动）
var _backend_pid: int = -1
## 后端启动计时器
var _backend_startup_timer: float = 0.0
## 后端端口检查间隔计时器
var _backend_check_timer: float = 0.0
## 是否正在等待后端启动
var _awaiting_backend: bool = false

## 后台解码线程 — 将 JSON 解析移出主线程，避免阻塞渲染
var _decode_thread: Thread = null
var _decode_mutex: Mutex = null
var _decode_sem: Semaphore = null
var _decode_input: Array[PackedByteArray] = []
var _decode_output: Array[Dictionary] = []
var _decode_running: bool = false


func _ready() -> void:
	"""自动加载初始化。编辑器模式下跳过，游戏运行时自动启动后端。"""
	if Engine.is_editor_hint():
		set_process(false)
		return
	# 游戏运行时：启动后端
	_start_backend()


func _notification(what: int) -> void:
	"""场景树通知：进程退出前关闭后端。

	Args:
		what: 通知类型。
	"""
	if what == NOTIFICATION_PREDELETE:
		_stop_decode_thread()
		_kill_backend()


func _start_backend() -> void:
	"""启动 Python 后端进程。

	使用 OS.create_process 异步启动，不阻塞 Godot 主循环。
	如果端口已被占用（后端已在运行），则跳过启动。
	"""
	# 先检查端口是否已被占用（后端可能已在运行）
	if _is_port_open(DEFAULT_HOST, DEFAULT_PORT):
		print("Connection: backend already running on %s:%d" % [DEFAULT_HOST, DEFAULT_PORT])
		return

	var project_root: String = ProjectSettings.globalize_path("res://..")
	var python_path: String = project_root.path_join(VENV_PYTHON_REL)
	var backend_dir: String = project_root.path_join("ascend-backend")
	var script_path: String = backend_dir.path_join("run_server.py")

	# 校验路径
	if not FileAccess.file_exists(python_path):
		push_error("Connection: Python not found at %s" % python_path)
		return
	if not FileAccess.file_exists(script_path):
		push_error("Connection: backend script not found at %s" % script_path)
		return

	# 启动后端进程，工作目录设为 ascend-backend/
	var pid: int = OS.create_process(python_path, [script_path], false)
	if pid == -1:
		push_error("Connection: failed to start backend process")
		return

	_backend_pid = pid
	_awaiting_backend = true
	_backend_startup_timer = 0.0
	set_process(true)
	print("Connection: backend started (PID: %d), waiting for port..." % pid)


func _kill_backend() -> void:
	"""关闭后端进程。"""
	if _backend_pid <= 0:
		return
	OS.kill(_backend_pid)
	print("Connection: backend stopped (PID: %d)" % _backend_pid)
	_backend_pid = -1
	_awaiting_backend = false


func _is_port_open(host: String, port: int) -> bool:
	"""检查指定端口是否已开放（TCP 连接测试）。

	Args:
		host: 主机地址。
		port: 端口号。

	Returns:
		True 如果端口可连接。
	"""
	var test := StreamPeerTCP.new()
	var err: Error = test.connect_to_host(host, port)
	if err != OK:
		return false
	# 轮询等待连接结果（最多 200ms）
	var elapsed: float = 0.0
	while elapsed < 0.2:
		test.poll()
		if test.get_status() == StreamPeerTCP.STATUS_CONNECTED:
			test.disconnect_from_host()
			return true
		if test.get_status() != StreamPeerTCP.STATUS_CONNECTING:
			break
		elapsed += 0.05
	test.disconnect_from_host()
	return false


func _process(delta: float) -> void:
	"""每帧轮询：检查连接、读数据、发数据。"""
	# 等待后端启动（每 0.5 秒检查一次端口，避免每帧阻塞）
	if _awaiting_backend:
		_backend_startup_timer += delta
		if _backend_startup_timer > BACKEND_STARTUP_TIMEOUT:
			push_error("Connection: backend startup timed out after %.0fs" % BACKEND_STARTUP_TIMEOUT)
			_awaiting_backend = false
			_kill_backend()
			return
		_backend_check_timer -= delta
		if _backend_check_timer > 0.0:
			return
		_backend_check_timer = 0.5
		if _is_port_open(DEFAULT_HOST, DEFAULT_PORT):
			print("Connection: backend ready on %s:%d (waited %.1fs)" % [DEFAULT_HOST, DEFAULT_PORT, _backend_startup_timer])
			_awaiting_backend = false
			if _stream != null:
				_stream.disconnect_from_host()
				_stream = null
			_connect()
		return

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
				_collect_decoded()
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
	_stop_decode_thread()
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
				_start_decode_thread()
				connection_established.emit(_host, _port)
		StreamPeerTCP.STATUS_CONNECTING:
			pass
		_:
			push_warning("Connection: lost, status=%d" % s)
			_stream = null
			status = Status.DISCONNECTED
			_reconnect_timer = RECONNECT_INTERVAL
			_stop_decode_thread()
			connection_lost.emit()


func _read_messages() -> void:
	"""读取所有可用数据，提取帧体，推入后台解码队列。

	JSON 解析在后台线程完成，主线程仅做字节帧提取。
	"""
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

	# 解析长度前缀帧，推入解码队列
	while _recv_buf.size() >= 4:
		var msg_len: int = (_recv_buf[0] << 24) | (_recv_buf[1] << 16) | (_recv_buf[2] << 8) | _recv_buf[3]
		if msg_len <= 0 or msg_len > MAX_MESSAGE_SIZE:
			push_error("Connection: invalid message length: %d" % msg_len)
			_recv_buf.clear()
			return
		if _recv_buf.size() < 4 + msg_len:
			break
		var body: PackedByteArray = _recv_buf.slice(4, 4 + msg_len)
		_recv_buf = _recv_buf.slice(4 + msg_len)

		_decode_mutex.lock()
		_decode_input.append(body)
		_decode_mutex.unlock()
		_decode_sem.post()


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


func _next_seq() -> int:
	"""生成下一个序列号。

	Returns:
		递增的序列号
	"""
	_seq += 1
	return _seq


# ── 后台解码线程 ────────────────────────────────────


func _start_decode_thread() -> void:
	"""启动后台 JSON 解码线程。"""
	if _decode_thread != null:
		return
	_decode_mutex = Mutex.new()
	_decode_sem = Semaphore.new()
	_decode_running = true
	_decode_thread = Thread.new()
	_decode_thread.start(_decode_worker)


func _stop_decode_thread() -> void:
	"""停止后台解码线程并清理。"""
	_decode_running = false
	if _decode_sem:
		_decode_sem.post()
	if _decode_thread:
		_decode_thread.wait_to_finish()
		_decode_thread = null
	_decode_mutex = null
	_decode_sem = null
	_decode_input.clear()
	_decode_output.clear()


func _decode_worker() -> void:
	"""后台线程：从输入队列取帧体 → JSON 解析 → 放入输出队列。"""
	while _decode_running:
		_decode_sem.wait()
		if not _decode_running:
			break

		_decode_mutex.lock()
		var body: PackedByteArray
		if _decode_input.size() > 0:
			body = _decode_input.pop_front()
		_decode_mutex.unlock()

		if body.is_empty():
			continue

		var message: Variant = MsgPack.decode(body)
		if message == null or not message is Dictionary:
			print("Connection: decode failed in worker thread")
			continue

		_decode_mutex.lock()
		_decode_output.append(message)
		_decode_mutex.unlock()


func _collect_decoded() -> void:
	"""主线程收集后台已解码的消息并发射信号。"""
	if _decode_mutex == null:
		return
	var messages: Array[Dictionary] = []
	_decode_mutex.lock()
	for msg in _decode_output:
		messages.append(msg)
	_decode_output.clear()
	_decode_mutex.unlock()

	for msg in messages:
		message_received.emit(msg)
