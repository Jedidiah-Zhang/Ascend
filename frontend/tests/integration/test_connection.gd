extends GutTest

const Config = preload("res://scripts/config.gd")
const FrameCodecClass = preload("res://scripts/utils/frame_codec.gd")
const BackendProcessClass = preload("res://scripts/net/backend_process.gd")
const TcpTransportClass = preload("res://scripts/net/tcp_transport.gd")
const HandshakeClass = preload("res://scripts/net/handshake.gd")
const Fakes = preload("res://tests/fakes/connection_layers.gd")


# ── 夹具：注入假层并复位门面状态 ──────────────────────────

var _fake_process: Fakes.FakeProcess
var _fake_transport: Fakes.FakeTransport
var _fake_handshake: Fakes.FakeHandshake
var _fake_worker: Fakes.FakeWorker
var _real_process: Object
var _real_transport: Object
var _real_handshake: Object
var _real_worker: Object

func before_all() -> void:
	_real_process = Connection._process_layer
	_real_transport = Connection._transport
	_real_handshake = Connection._handshake
	_real_worker = Connection._worker


func before_each() -> void:
	_fake_process = Fakes.FakeProcess.new()
	_fake_transport = Fakes.FakeTransport.new()
	_fake_handshake = Fakes.FakeHandshake.new()
	_fake_worker = Fakes.FakeWorker.new()
	Connection._set_layers(_fake_process, _fake_transport, _fake_handshake, _fake_worker)
	Connection.status = Connection.Status.DISCONNECTED
	Connection._restart_phase = Connection.RestartPhase.NONE
	Connection._outage_emitted = false
	Connection._restart_args = PackedStringArray()
	Connection.backend_args = PackedStringArray()


func after_each() -> void:
	Connection._set_layers(_real_process, _real_transport, _real_handshake, _real_worker)
	Connection.status = Connection.Status.DISCONNECTED
	Connection._restart_phase = Connection.RestartPhase.NONE
	Connection._outage_emitted = false


# ── Connection AutoLoad 可用性 ─────────────────────────────

func test_connection_autoload_exists() -> void:
	assert_not_null(Connection, "Connection AutoLoad 应存在")


func test_connection_is_node() -> void:
	assert_true(Connection is Node, "Connection 应是 Node 类型")


func test_connection_has_status_enum() -> void:
	assert_eq(Connection.Status.DISCONNECTED, 0)
	assert_eq(Connection.Status.CONNECTING, 1)
	assert_eq(Connection.Status.CONNECTED, 2)
	assert_eq(Connection.Status.FAILED, 3, "FAILED 为启动失败终态")


# ── 信号定义 ───────────────────────────────────────────────

func test_connection_has_signals() -> void:
	assert_true(Connection.has_signal("connection_established"))
	assert_true(Connection.has_signal("connection_lost"))
	assert_true(Connection.has_signal("message_received"))
	assert_true(Connection.has_signal("backend_failed"))


func test_signals_are_connectable() -> void:
	var results: Array = []
	Connection.connection_established.connect(func(_h, _p): results.append("connected"))
	Connection.message_received.connect(func(_m): results.append("msg"))

	Connection.connection_established.emit("127.0.0.1", 9081)
	Connection.message_received.emit({"type": "test"})

	assert_eq(results.size(), 2)
	assert_eq(results[0], "connected")
	assert_eq(results[1], "msg")


# ── FAILED 终态状态机 ──────────────────────────────────────

func test_process_failed_enters_failed_state() -> void:
	"""进程层失败 → 门面进入 FAILED 终态并发射 backend_failed。"""
	_fake_process.state = BackendProcessClass.State.FAILED
	var failed_emitted: Array = []
	var cb := func(r): failed_emitted.append(r)
	Connection.backend_failed.connect(cb)

	_fake_process.failed.emit("startup timeout")

	assert_eq(Connection.status, Connection.Status.FAILED, "应进入 FAILED 终态")
	assert_eq(failed_emitted.size(), 1, "应发射 backend_failed 信号")
	assert_eq(failed_emitted[0], "startup timeout")
	Connection.backend_failed.disconnect(cb)


func test_connect_to_server_on_failed_restarts_process() -> void:
	"""FAILED 终态经 connect_to_server 走完整重启流程（RESET→SPAWN）。"""
	_fake_process.state = BackendProcessClass.State.FAILED
	Connection.connect_to_server("127.0.0.1", 1)

	assert_eq(Connection._restart_phase, Connection.RestartPhase.RESET,
		"FAILED 时应进入重启状态机")

	Connection._process(0.0)  # RESET → SPAWN
	Connection._process(0.0)  # SPAWN → 拉起
	assert_eq(_fake_process.start_args.size(), 1, "应以 backend_args 重拉进程")


func test_connect_to_server_normal_connects() -> void:
	"""正常状态重连：直接重置并发起 TCP 连接。"""
	Connection.connect_to_server("127.0.0.1", 1)
	assert_eq(_fake_transport.connect_count, 1)
	assert_eq(Connection.status, Connection.Status.CONNECTING)

	Connection.disconnect_from_server()
	assert_eq(_fake_transport.disconnect_count, 1)
	assert_eq(_fake_worker.stop_count, 1, "断开应停解码线程")
	assert_eq(Connection.status, Connection.Status.DISCONNECTED)


# ── 消息发送 ────────────────────────────────────────────────

func test_send_does_not_crash_when_disconnected() -> void:
	Connection.send({"type": "request", "request_type": "ping", "payload": {}})
	assert_eq(_fake_transport.send_frames.size(), 0, "未握手时不应发出帧")
	pass_test("send 在未连接状态下不崩溃")


func test_send_adds_seq_when_missing() -> void:
	Connection._force_handshake_acked()  # 白盒：模拟握手完成（send 仅握手后放行）
	var msg: Dictionary = {"type": "request", "request_type": "test", "payload": {}}
	assert_false(msg.has("seq"))
	Connection.send(msg)
	assert_true(msg.has("seq"), "send 应自动添加 seq")
	assert_gt(msg["seq"], 0)
	assert_eq(_fake_transport.send_frames.size(), 1, "握手完成后应真正发出帧")


func test_send_multiple_messages() -> void:
	Connection._force_handshake_acked()
	Connection.send({"type": "request", "request_type": "a", "payload": {}})
	Connection.send({"type": "request", "request_type": "b", "payload": {}})
	Connection.send({"type": "request", "request_type": "c", "payload": {}})
	assert_eq(_fake_transport.send_frames.size(), 3, "多条 send 应全部发出")
	assert_eq(Connection._drain_pending_frames().size(), 3, "帧应在待发队列可读回")
	assert_eq(Connection._drain_pending_frames().size(), 0, "drain 应清空待发队列")


# ── 消息接收（握手消费 → 广播） ────────────────────────────

func test_message_received_after_ack() -> void:
	"""握手完成后解码消息广播给订阅者。"""
	_fake_transport.state = TcpTransportClass.State.CONNECTED
	_fake_handshake.state = HandshakeClass.State.ACKED  # 等价真实 on_message 置位
	_fake_handshake.acked.emit()
	var received: Array = []
	Connection.message_received.connect(func(m): received.append(m))

	_fake_worker.decoded = [{"type": "snapshot"}]
	Connection._process(0.0)

	assert_eq(received.size(), 1, "已握手消息应广播")
	assert_eq(received[0], {"type": "snapshot"})


# ── 握手流程（回归迁移：ack 停表语义由握手层单测覆盖） ────

func test_handshake_ack_emits_established() -> void:
	"""握手完成 → connection_established + CONNECTED。"""
	var got: Array = []
	Connection.connection_established.connect(func(h, p): got.append([h, p]))

	_fake_transport.state = TcpTransportClass.State.CONNECTED
	_fake_handshake.acked.emit()

	assert_eq(got.size(), 1, "握手完成应发射 connection_established")
	assert_eq(got[0], ["127.0.0.1", 1])
	assert_eq(Connection.status, Connection.Status.CONNECTED)


func test_handshake_rejected_retries() -> void:
	"""握手被拒 → 传输层进入重连重试（不广播断线）。"""
	var lost: int = 0
	Connection.connection_lost.connect(func(): lost += 1)

	_fake_handshake.rejected.emit("bad token")

	assert_eq(_fake_transport.retry_count, 1, "应重置传输层等待重连")
	assert_eq(lost, 0, "重试期不应广播 connection_lost")


func test_handshake_timeout_retries() -> void:
	"""握手超时 → 同样走重连重试（旧回归：ack 后不再误触发，层内覆盖）。"""
	_fake_handshake.timeout.emit()
	assert_eq(_fake_transport.retry_count, 1, "超时应重置传输层")


# ── 断线广播（去重 + 主动切换静默） ────────────────────────

func test_connection_lost_emitted_once_per_outage() -> void:
	# GDScript lambda 按值捕获基元 → 用 Array 计数而非 int
	var lost: Array = [0]
	Connection.connection_lost.connect(func(): lost[0] += 1)

	_fake_transport.disconnected.emit()
	_fake_transport.disconnected.emit()
	assert_eq(lost[0], 1, "同一断线期内只广播一次")

	_fake_handshake.acked.emit()  # 恢复（ack 重置去重标记）
	_fake_transport.disconnected.emit()
	assert_eq(lost[0], 2, "恢复后的下一次断线应再次广播")


func test_restarting_suppresses_loss_broadcast() -> void:
	"""主动切换（restart 相位中）断线不广播 connection_lost。"""
	var lost: Array = [0]
	Connection.connection_lost.connect(func(): lost[0] += 1)

	Connection._restart_phase = Connection.RestartPhase.WAIT_STOP
	_fake_transport.disconnected.emit()
	assert_eq(lost[0], 0, "主动切换中不应广播断线")


func test_disconnect_from_server_is_silent() -> void:
	"""disconnect_from_server 静默断开（调用方自定语义）。"""
	var lost: Array = [0]
	Connection.connection_lost.connect(func(): lost[0] += 1)

	Connection.disconnect_from_server()

	assert_eq(lost[0], 0)
	assert_eq(_fake_transport.disconnect_count, 1)
	assert_eq(Connection.status, Connection.Status.DISCONNECTED)


# ── 配置常量 ────────────────────────────────────────────────

func test_layer_constants_match_config() -> void:
	"""唯一事实源契约：各层常量必须与 config.gd 一致（门面不重复导出）。"""
	assert_eq(Connection.DEFAULT_HOST, Config.DEFAULT_HOST)
	assert_eq(Connection.DEFAULT_PORT, Config.DEFAULT_PORT)
	assert_eq(TcpTransportClass.RECONNECT_INTERVAL, Config.RECONNECT_INTERVAL)
	assert_eq(TcpTransportClass.CONNECTING_TIMEOUT, Config.CONNECTING_TIMEOUT)
	assert_eq(TcpTransportClass.RECEIVE_TIMEOUT, Config.RECEIVE_TIMEOUT)
	assert_eq(TcpTransportClass.MAX_MESSAGE_SIZE, Config.MAX_MESSAGE_SIZE)
	assert_eq(HandshakeClass.HELLO_TIMEOUT, Config.HELLO_TIMEOUT)
	assert_eq(HandshakeClass.PROTOCOL_VERSION, Config.PROTOCOL_VERSION)
	assert_eq(BackendProcessClass.BACKEND_STARTUP_TIMEOUT, Config.BACKEND_STARTUP_TIMEOUT)


# ── 后端进程终止（优雅关闭） ────────────────────────────────

func test_network_layer_survives_pause() -> void:
	"""网络层应免疫暂停（暂停菜单打开时仍须收发消息）。

	回归：暂停期间 Connection._process 冻结 → 存档请求发不出去、
	响应收不回来，「正在保存...」永久卡住。
	"""
	assert_eq(Connection.process_mode, Node.PROCESS_MODE_ALWAYS,
		"暂停时网络层必须继续处理")


func test_process_still_works_while_paused() -> void:
	"""树暂停时 Connection._process 仍可驱动（不崩溃、状态不破坏）。"""
	get_tree().paused = true
	Connection._process(0.016)
	Connection._process(0.016)
	get_tree().paused = false
	pass_test("暂停期间驱动网络层无异常")


func test_graceful_stop_constants() -> void:
	"""优雅终止常量：等待后端最终落盘的超时兜底。"""
	assert_eq(BackendProcessClass.BACKEND_STOP_TIMEOUT_MS, 3000, "超时兜底 3s")


func test_prdelete_stops_layers() -> void:
	"""退出时同步停解码线程与后端进程（阻塞版仅退出使用）。"""
	Connection._notification(Node.NOTIFICATION_PREDELETE)
	assert_eq(_fake_worker.stop_count, 1, "退出应停解码线程")
	assert_eq(_fake_process.sync_stop_count, 1, "退出应同步停后端")


# ── 进程切换（restart_backend） ────────────────────────────

func test_restart_backend_idempotent_when_same_mode() -> void:
	"""目标参数与当前一致且进程存活：幂等跳过（不杀进程不重拉）。"""
	_fake_process.pid = 12345
	_fake_process.args = PackedStringArray()
	Connection.backend_args = PackedStringArray()

	Connection.restart_backend(PackedStringArray())

	assert_eq(_fake_process.stop_count, 0, "同模式不应停进程")
	assert_eq(_fake_process.start_args.size(), 0, "同模式不应重拉")
	assert_eq(Connection._restart_phase, Connection.RestartPhase.NONE)


func test_restart_backend_same_world_args_idempotent() -> void:
	"""世界进程参数一致时同样幂等。"""
	_fake_process.pid = 12345
	_fake_process.args = PackedStringArray(["--world-id", "abc"])
	Connection.backend_args = PackedStringArray(["--world-id", "abc"])

	Connection.restart_backend(PackedStringArray(["--world-id", "abc"]))

	assert_eq(_fake_process.stop_count, 0)
	assert_eq(_fake_process.start_args.size(), 0)


func test_restart_backend_different_args_restarts() -> void:
	"""不同参数：WAIT_STOP → RESET → SPAWN 异步状态机，参数即刻生效。"""
	_fake_process.pid = 12345
	_fake_process.args = PackedStringArray()
	Connection.backend_args = PackedStringArray()

	Connection.restart_backend(PackedStringArray(["--world-id", "w1"]))

	assert_eq(Connection._restart_phase, Connection.RestartPhase.RESET,
		"旧进程停止后应立即推进到 RESET")
	assert_eq(_fake_process.stop_count, 1, "应停旧进程")
	assert_eq(Connection.backend_args, PackedStringArray(["--world-id", "w1"]),
		"目标参数应立即生效（UI 据此感知模式）")

	Connection._process(0.0)  # RESET → SPAWN（清残留）
	Connection._process(0.0)  # SPAWN → 拉起
	assert_eq(_fake_process.start_args.size(), 1, "旧进程退出后应拉起新进程")
	if _fake_process.start_args.size() == 1:
		assert_eq(_fake_process.start_args[0], PackedStringArray(["--world-id", "w1"]),
			"拉起参数应为目标参数")
	assert_eq(Connection._restart_phase, Connection.RestartPhase.NONE, "切换应已完成")


func test_restart_duplicate_call_ignored() -> void:
	"""切换进行中重复调用被忽略（防重入）。"""
	_fake_process.pid = 999999
	_fake_process.args = PackedStringArray()
	Connection.backend_args = PackedStringArray()
	Connection._restart_phase = Connection.RestartPhase.WAIT_STOP

	Connection.restart_backend(PackedStringArray(["--world-id", "w2"]))

	assert_eq(_fake_process.stop_count, 0, "切换中不应再停进程")
	assert_eq(_fake_process.start_args.size(), 0, "切换中不应触发拉起")
	assert_eq(Connection.backend_args, PackedStringArray(),
		"切换中不应覆盖目标参数")
	assert_eq(Connection._restart_phase, Connection.RestartPhase.WAIT_STOP)


# ── 完整连接流（进程就绪 → 连接 → 握手 → 收发） ────────────

func test_full_connect_flow() -> void:
	"""事件链：process.ready → connect → connected → worker/handshake 启动。"""
	_fake_process.state = BackendProcessClass.State.READY
	_fake_process.ready.emit()
	assert_eq(_fake_transport.connect_count, 1, "进程就绪应发起连接")
	assert_eq(Connection.status, Connection.Status.CONNECTING)

	_fake_transport.state = TcpTransportClass.State.CONNECTED
	_fake_transport.connected.emit()
	assert_eq(_fake_worker.start_count, 1, "连接建立应启动解码线程")
	assert_eq(_fake_handshake.start_count, 1, "连接建立应发起握手")
	assert_eq(Connection.status, Connection.Status.CONNECTED)


func test_hello_front_when_pending_frames() -> void:
	"""回归（Issue #34 → 审查修复）：重连时 hello 必须恰好位于发送队列队首。

	旧实现 hello 走 _send_queue.push_front；重构若改为队尾 append，重连时
	残留业务帧会先于 hello 落盘，后端在握手前收到非 hello 帧即断开
	（见 backend/ascend/net/client_handler.py），造成握手死循环。

	注意：必须用门面 _ready 真实创建并接线的 handshake（_real_handshake），
	其 _send_frame 即被门面接线到 transport 队首——自己手搓 handshake 会
	绕过门面接线，测试假绿。
	"""
	var t := TcpTransportClass.new("127.0.0.1", 1)
	Connection._set_layers(_fake_process, t, _real_handshake, _fake_worker)

	# 模拟重置队列残留业务帧（send 失败保留的未发部分）
	t.send_frame(_real_handshake._codec.frame_encode({"type": "request", "request_type": "ping"}))

	_real_handshake.start()  # 门面接线：hello 经 send_frame_front 入队
	var frames: Array[PackedByteArray] = Connection._drain_pending_frames()
	assert_eq(frames.size(), 2, "残留帧 + hello")
	var decoded: Dictionary = Connection._codec.frame_decode(frames[0], Config.MAX_MESSAGE_SIZE)
	assert_eq(JsonCodec.decode(decoded["bodies"][0])["type"], "hello",
		"队首帧必须是 hello")
	decode_ok(frames[1])


func decode_ok(frame: PackedByteArray) -> void:
	var d: Dictionary = Connection._codec.frame_decode(frame, Config.MAX_MESSAGE_SIZE)
	assert_eq(d["bodies"].size(), 1, "业务帧应可解码")
	assert_not_null(JsonCodec.decode(d["bodies"][0]))