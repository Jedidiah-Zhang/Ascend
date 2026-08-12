"""集成测试共享假层（门面注入测试替身）。

位于 tests/fakes/（不在 GUT 扫描目录 tests/unit、tests/integration），
仅供 test_connection.gd / test_main_world.gd 等 preload 使用。

假层继承真实层类以匹配门面 _set_layers 的类型签名；覆盖所有
公共方法使 tick/start/stop 等不产生真实副作用（进程/套接字/线程）。
"""

const FrameCodecClass = preload("res://scripts/utils/frame_codec.gd")
const BackendProcessClass = preload("res://scripts/net/backend_process.gd")
const TcpTransportClass = preload("res://scripts/net/tcp_transport.gd")
const HandshakeClass = preload("res://scripts/net/handshake.gd")
const DecodeWorkerClass = preload("res://scripts/net/decode_worker.gd")


class FakeProcess:
	extends BackendProcessClass

	var start_args: Array[PackedStringArray] = []
	var stop_count: int = 0
	var sync_stop_count: int = 0

	func _init() -> void:
		super("127.0.0.1", 1, "/tmp/proj", "/tmp/data")

	func start(p_args: PackedStringArray = PackedStringArray()) -> void:
		args = p_args
		start_args.append(p_args)

	func stop() -> void:
		stop_count += 1
		state = State.IDLE
		stopped.emit()

	func stop_sync() -> void:
		sync_stop_count += 1
		state = State.IDLE

	func tick(_delta: float) -> void:
		pass

	func args_equal(other: PackedStringArray) -> bool:
		return args == other

	func is_alive() -> bool:
		return pid > 0


class FakeTransport:
	extends TcpTransportClass

	var send_frames: Array[PackedByteArray] = []
	var pending: Array[PackedByteArray] = []
	var connect_count: int = 0
	var disconnect_count: int = 0
	var retry_count: int = 0

	func _init() -> void:
		super("127.0.0.1", 1)

	func reset() -> void:
		state = State.DISCONNECTED
		pending.clear()

	func reset_for_reconnect() -> void:
		retry_count += 1
		state = State.DISCONNECTED

	func connect_to_host() -> void:
		connect_count += 1
		state = State.CONNECTING

	func disconnect_from_host() -> void:
		disconnect_count += 1
		state = State.DISCONNECTED

	func send_frame(body: PackedByteArray) -> void:
		send_frames.append(body)
		pending.append(body)

	func send_frame_front(body: PackedByteArray) -> void:
		send_frames.append(body)
		pending.push_front(body)

	func drain_pending_frames() -> Array[PackedByteArray]:
		var out: Array[PackedByteArray] = pending.duplicate()
		pending.clear()
		return out

	func tick(_delta: float) -> void:
		pass


class FakeHandshake:
	extends HandshakeClass

	var start_count: int = 0
	var reset_count: int = 0

	func _init() -> void:
		super(FrameCodecClass.new(), Callable(), "/tmp/data/token")

	func start() -> void:
		start_count += 1

	func reset() -> void:
		reset_count += 1
		state = State.IDLE

	func tick(_delta: float) -> void:
		pass


class FakeWorker:
	extends DecodeWorkerClass

	var start_count: int = 0
	var stop_count: int = 0
	var decoded: Array[Dictionary] = []

	func _init() -> void:
		super()

	func start() -> void:
		start_count += 1

	func stop() -> void:
		stop_count += 1

	func push(_body: PackedByteArray) -> void:
		pass

	func drain() -> Array[Dictionary]:
		var out: Array[Dictionary] = decoded.duplicate()
		decoded.clear()
		return out