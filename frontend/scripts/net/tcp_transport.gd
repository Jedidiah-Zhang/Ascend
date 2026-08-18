"""TCP 传输层 — 连接状态机/重连/帧切分/超时/发送队列（纯逻辑 RefCounted）。

职责（从 connection.gd 抽离）:
  - 连接：connect/poll，CONNECTING 超时 → reset_for_reconnect
  - 断开：任何连接失效 → reset_for_reconnect（disconnected 信号，
    断线去重与 UI 语义由门面负责）
  - 接收：流字节 → FrameCodec 帧切分 → 逐帧发射 frame_received
  - 发送队列：send_frame 入队（send_frame_front 队首，握手帧专用），
    CONNECTED 时逐帧落盘，失败只保留未发送部分
  - 收包超时：CONNECTED 下无数据超时（RECEIVE_TIMEOUT）→ 重连
  - 重连：DISCONNECTED 下倒计时后自动 connect；连续失败指数退避
    （reconnect_interval 翻倍，封顶 RECONNECT_MAX_INTERVAL），
    任何一次连接成功即复位基础间隔

注入点：socket_factory（默认 StreamPeerTCP.new，测试可注入 fake）。
依赖方向：connection(门面) → 本层；本层不感知其他子层。
"""

class_name TcpTransport
extends RefCounted

const Config = preload("res://scripts/config.gd")
const FrameCodecClass = preload("res://scripts/utils/frame_codec.gd")


# ── 信号（向门面汇报） ─────────────────────────────────────

## TCP 建立（仅一次；握手语义由 handshake 层负责）
signal connected
## 连接失效并进入等待重连（reset_for_reconnect 触发；reset/disconnect 不发）
signal disconnected
## 完整协议帧体（已帧切分，未解码）
signal frame_received(body: PackedByteArray)


# ── 状态机 ─────────────────────────────────────────────────

enum State { DISCONNECTED, CONNECTING, CONNECTED }

var state: State = State.DISCONNECTED


# ── 常量（唯一事实源 = config.gd） ─────────────────────────

const RECONNECT_INTERVAL: float = Config.RECONNECT_INTERVAL
const CONNECTING_TIMEOUT: float = Config.CONNECTING_TIMEOUT
const RECEIVE_TIMEOUT: float = Config.RECEIVE_TIMEOUT
const MAX_MESSAGE_SIZE: int = Config.MAX_MESSAGE_SIZE


# ── 配置属性 ───────────────────────────────────────────────

var host: String
var port: int
## 当前重连间隔（秒）：连续失败翻倍封顶，连接成功复位基础间隔
var reconnect_interval: float = Config.RECONNECT_INTERVAL


# ── 注入点 ─────────────────────────────────────────────────

## socket 工厂 () -> 具备 StreamPeerTCP 接口的对象（测试可注入 fake）
var socket_factory: Callable = _socket_default


func _socket_default() -> StreamPeerTCP:
	return StreamPeerTCP.new()


# ── 内部状态 ───────────────────────────────────────────────

## 注：类型用 Object——测试注入的 fake 不是 StreamPeerTCP 子类
var _socket: Object = null
var _reconnect_timer: float = 0.0
var _connect_elapsed: float = 0.0
## 最后收数据后的空闲累计（秒），超过 RECEIVE_TIMEOUT 视为后端挂死
var _receive_idle: float = 0.0
var _recv_buf: PackedByteArray = PackedByteArray()
var _send_queue: Array[PackedByteArray] = []
var _codec: FrameCodec
## 是否允许断线后自动重连（connect_to_host/reset_for_reconnect 开启；
## disconnect_from_host/reset 关闭）
var _allow_auto_reconnect: bool = false


func _init(p_host: String, p_port: int) -> void:
	host = p_host
	port = p_port
	_codec = FrameCodecClass.new()


# ── 公共接口 ───────────────────────────────────────────────

func connect_to_host() -> void:
	"""发起 TCP 连接。"""
	if state != State.DISCONNECTED:
		return
	_socket = socket_factory.call() as Object
	_allow_auto_reconnect = true
	_connect_elapsed = 0.0
	if _socket == null:
		push_warning("TcpTransport: socket factory returned null")
		reset_for_reconnect()
		return
	if _socket.connect_to_host(host, port) != OK:
		push_warning("TcpTransport: connect_to_host failed on %s:%d" % [host, port])
		_socket = null
		reset_for_reconnect()
		return
	state = State.CONNECTING


func disconnect_from_host() -> void:
	"""主动断开（不进入重连，不发 disconnected 信号；由调用方决定语义）。"""
	_allow_auto_reconnect = false
	_dispose_socket()
	_recv_buf = PackedByteArray()
	_reconnect_timer = 0.0
	reconnect_interval = Config.RECONNECT_INTERVAL
	_connect_elapsed = 0.0
	_receive_idle = 0.0
	state = State.DISCONNECTED


func reset() -> void:
	"""完整清理（进程切换/手动重连前）：连接、缓冲、队列、计时，无信号。"""
	_allow_auto_reconnect = false
	_dispose_socket()
	_recv_buf = PackedByteArray()
	_send_queue.clear()
	_reconnect_timer = 0.0
	reconnect_interval = Config.RECONNECT_INTERVAL
	_connect_elapsed = 0.0
	_receive_idle = 0.0
	state = State.DISCONNECTED


func reset_for_reconnect() -> void:
	"""连接失效清理 + 进入重连等待（间隔按退避递增），发射 disconnected。"""
	_allow_auto_reconnect = true
	_dispose_socket()
	_recv_buf = PackedByteArray()
	_reconnect_timer = reconnect_interval
	reconnect_interval = min(reconnect_interval * 2.0, Config.RECONNECT_MAX_INTERVAL)
	_connect_elapsed = 0.0
	_receive_idle = 0.0
	state = State.DISCONNECTED
	disconnected.emit()


func tick(delta: float) -> void:
	"""每帧推进：重连倒计时 / 连接轮询与超时 / 读取、发送、收包超时。"""
	match state:
		State.CONNECTING:
			_tick_connecting(delta)
		State.CONNECTED:
			_tick_connected(delta)
		State.DISCONNECTED:
			if _allow_auto_reconnect:
				_reconnect_timer -= delta
				if _reconnect_timer <= 0.0:
					connect_to_host()


func send_frame(body: PackedByteArray) -> void:
	"""入队一条协议帧体（CONNECTED 后逐帧发送）。"""
	_send_queue.append(body)


func send_frame_front(body: PackedByteArray) -> void:
	"""入队一条协议帧体并置于队首（握手帧专用：必须先于任何残留业务帧发出）。"""
	_send_queue.push_front(body)


func drain_pending_frames() -> Array[PackedByteArray]:
	"""返回并清空未发送帧（测试钩子，等价旧测试对 _send_queue 的读取）。"""
	var out: Array[PackedByteArray] = _send_queue.duplicate()
	_send_queue.clear()
	return out


# ── 内部：连接推进 ─────────────────────────────────────────

func _tick_connecting(delta: float) -> void:
	if _socket == null:
		push_warning("TcpTransport: connecting without socket")
		reset_for_reconnect()
		return
	_socket.poll()
	var status: int = _socket.get_status()
	if status == StreamPeerTCP.STATUS_CONNECTED:
		state = State.CONNECTED
		reconnect_interval = Config.RECONNECT_INTERVAL  # 成功即复位退避
		_receive_idle = 0.0
		connected.emit()
		_flush_send_queue()
		return
	if status != StreamPeerTCP.STATUS_CONNECTING:  # ERR / NONE → 立即失效
		push_warning("TcpTransport: connect failed (status %d)" % status)
		reset_for_reconnect()
		return
	_connect_elapsed += delta
	if _connect_elapsed >= CONNECTING_TIMEOUT:
		push_warning("TcpTransport: connect timeout after %.0fs" % CONNECTING_TIMEOUT)
		reset_for_reconnect()


# ── 内部：已连推进 ─────────────────────────────────────────

func _tick_connected(delta: float) -> void:
	if _socket == null:
		reset_for_reconnect()
		return
	_socket.poll()
	if _socket.get_status() != StreamPeerTCP.STATUS_CONNECTED:
		push_warning("TcpTransport: connection lost")
		reset_for_reconnect()
		return
	var received_any := _read_available()
	if received_any:
		_receive_idle = 0.0
	else:
		_receive_idle += delta
		if _receive_idle >= RECEIVE_TIMEOUT:
			push_warning("TcpTransport: no data for %.0fs, reconnecting" % RECEIVE_TIMEOUT)
			reset_for_reconnect()
			return
	_flush_send_queue()


func _read_available() -> bool:
	"""读取 socket 可用字节 → 帧切分 → 逐帧发射；返回是否收到数据。"""
	var got := false
	while _socket.get_available_bytes() > 0:
		var result: Array = _socket.get_partial_data(65536)
		if result[0] != OK:
			push_warning("TcpTransport: read error, reconnecting")
			reset_for_reconnect()
			return got
		var chunk: PackedByteArray = result[1]
		if chunk.is_empty():
			break
		_recv_buf.append_array(chunk)
		got = true
	if got or _recv_buf.size() >= 5:
		var decoded: Dictionary = _codec.frame_decode(_recv_buf, MAX_MESSAGE_SIZE)
		for body in decoded["bodies"]:
			frame_received.emit(body)
		_recv_buf = decoded["remaining"]
	return got


func _flush_send_queue() -> void:
	"""CONNECTED 下逐帧发送；失败只保留未发送部分并进入重连。"""
	if _send_queue.is_empty():
		return
	for i in _send_queue.size():
		if _socket.put_data(_send_queue[i]) != OK:
			push_warning("TcpTransport: send failed, keeping unsent frames")
			_send_queue = _send_queue.slice(i)
			_dispose_socket()
			reset_for_reconnect()
			return
	_send_queue.clear()


func _dispose_socket() -> void:
	if _socket != null:
		_socket.disconnect_from_host()
		_socket = null