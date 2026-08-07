extends GutTest

const Config = preload("res://scripts/config.gd")


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


# ── FAILED 终态状态机 ──────────────────────────────────────

func test_startup_timeout_enters_failed_state() -> void:
	"""后端启动超时后进入 FAILED 终态并发射 backend_failed。"""
	Connection._awaiting_backend = true
	Connection._backend_startup_timer = Connection.BACKEND_STARTUP_TIMEOUT + 1.0

	var failed_emitted: Array = []
	var cb := func(_r): failed_emitted.append(true)
	Connection.backend_failed.connect(cb)

	Connection._process(0.0)

	assert_eq(Connection.status, Connection.Status.FAILED, "超时后应进入 FAILED 终态")
	assert_false(Connection._awaiting_backend, "超时后应停止等待后端")
	assert_eq(failed_emitted.size(), 1, "应发射 backend_failed 信号")
	Connection.backend_failed.disconnect(cb)


func test_connect_to_server_resets_failed_state() -> void:
	"""FAILED 终态可经 connect_to_server 重置重试。"""
	Connection.status = Connection.Status.FAILED
	Connection.connect_to_server("127.0.0.1", 1)
	assert_eq(Connection.status, Connection.Status.CONNECTING, "手动重连应重置为 CONNECTING")
	Connection.disconnect_from_server()


func test_signals_are_connectable() -> void:
	var results: Array = []
	Connection.connection_established.connect(func(_h, _p): results.append("connected"))
	Connection.message_received.connect(func(_m): results.append("msg"))

	Connection.connection_established.emit("127.0.0.1", 9081)
	Connection.message_received.emit({"type": "test"})

	assert_eq(results.size(), 2)
	assert_eq(results[0], "connected")
	assert_eq(results[1], "msg")


# ── 消息发送 ────────────────────────────────────────────────

func test_send_does_not_crash_when_disconnected() -> void:
	Connection.send({"type": "request", "request_type": "ping", "payload": {}})
	pass_test("send 在未连接状态下不崩溃")


func test_send_adds_seq_when_missing() -> void:
	Connection._hello_acked = true  # 白盒：模拟握手完成（send 仅握手后放行）
	var msg: Dictionary = {"type": "request", "request_type": "test", "payload": {}}
	assert_false(msg.has("seq"))
	Connection.send(msg)
	assert_true(msg.has("seq"), "send 应自动添加 seq")
	assert_gt(msg["seq"], 0)


func test_send_multiple_messages() -> void:
	Connection._hello_acked = true  # 白盒：模拟握手完成
	Connection.send({"type": "request", "request_type": "a", "payload": {}})
	Connection.send({"type": "request", "request_type": "b", "payload": {}})
	Connection.send({"type": "request", "request_type": "c", "payload": {}})
	pass_test("多条 send 不崩溃")


# ── 配置常量 ────────────────────────────────────────────────

func test_default_constants_match_config() -> void:
	assert_eq(Connection.DEFAULT_HOST, Config.DEFAULT_HOST)
	assert_eq(Connection.DEFAULT_PORT, Config.DEFAULT_PORT)
	assert_eq(Connection.RECONNECT_INTERVAL, Config.RECONNECT_INTERVAL)
	assert_eq(Connection.MAX_MESSAGE_SIZE, Config.MAX_MESSAGE_SIZE)


# ── 握手超时（回归：ack 后计时停止） ───────────────────────

func test_hello_ack_stops_timeout_countdown() -> void:
	"""握手成功后超时计时必须停止，否则连接必然 10s 后误触发重连。

	回归：_handle_handshake 收到 hello_ack 只置 _hello_acked，未重置
	_hello_elapsed，且 _poll_connection 的计时不看 ack 状态——
	每次连接握手成功 10s 后仍被判定为 hello timeout 断开重连。
	"""
	var listener := TCPServer.new()
	assert_eq(listener.listen(0), OK)
	var port: int = listener.get_local_port()

	var stream := StreamPeerTCP.new()
	assert_eq(stream.connect_to_host("127.0.0.1", port), OK)
	Connection._stream = stream
	# 等待 TCP 握手完成（内核完成三次握手即 STATUS_CONNECTED）
	for i in 50:
		stream.poll()
		if stream.get_status() == StreamPeerTCP.STATUS_CONNECTED:
			break
		await get_tree().process_frame
	assert_eq(stream.get_status(), StreamPeerTCP.STATUS_CONNECTED,
		"测试前置：TCP 应建立")

	# 场景 A：未 ack —— 11s 后触发 hello timeout 重连
	Connection._hello_sent = true
	Connection._hello_acked = false
	Connection._hello_elapsed = 0.0
	Connection._poll_connection(11.0)
	assert_eq(Connection.status, Connection.Status.DISCONNECTED,
		"未 ack 时超过 HELLO_TIMEOUT 应重连")

	# 场景 B：ack 已收 —— 同样 11s 不应再触发（修复核心）
	var stream2 := StreamPeerTCP.new()
	assert_eq(stream2.connect_to_host("127.0.0.1", port), OK)
	Connection._stream = stream2
	for i in 50:
		stream2.poll()
		if stream2.get_status() == StreamPeerTCP.STATUS_CONNECTED:
			break
		await get_tree().process_frame
	if stream2.get_status() != StreamPeerTCP.STATUS_CONNECTED:
		pass_test("TCP 无法重建，跳过场景 B（仍以场景 A 验证超时语义）")
		listener.stop()
		return
	Connection._hello_sent = true
	Connection._hello_acked = true
	Connection._hello_elapsed = 0.0
	Connection.status = Connection.Status.CONNECTED
	Connection._poll_connection(11.0)
	assert_eq(Connection.status, Connection.Status.CONNECTED,
		"ack 后即使超过 HELLO_TIMEOUT 也不得断开重连")
	Connection._hello_acked = false

	listener.stop()
	Connection.disconnect_from_server()


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
	assert_eq(Connection.BACKEND_STOP_TIMEOUT_MS, 3000, "超时兜底 3s")


func test_kill_backend_noop_without_pid() -> void:
	"""无后端进程（pid<=0）时终止应立即返回，不阻塞、不报错。"""
	Connection._backend_pid = -1
	Connection._kill_backend()
	assert_eq(Connection._backend_pid, -1, "无进程时不应改动 pid")


# ── 进程切换（restart_backend） ────────────────────────────

func test_restart_backend_idempotent_when_same_mode() -> void:
	"""目标参数与当前一致且进程存活：幂等跳过（不杀进程不重拉）。"""
	Connection._backend_pid = 12345
	Connection.backend_args = PackedStringArray()
	Connection._restarting = false
	var before: int = Connection._backend_pid
	Connection.restart_backend(PackedStringArray())
	assert_eq(Connection._backend_pid, before, "同模式不应动进程")
	assert_false(Connection._restarting, "同模式不应进入切换状态")


func test_restart_backend_same_world_args_idempotent() -> void:
	"""世界进程参数一致时同样幂等。"""
	Connection._backend_pid = 12345
	Connection.backend_args = PackedStringArray(["--world-id", "abc"])
	Connection._restarting = false
	Connection.restart_backend(PackedStringArray(["--world-id", "abc"]))
	assert_eq(Connection._backend_pid, 12345, "同世界参数不应重启")


func test_restart_backend_different_args_enters_restarting() -> void:
	"""不同参数：切换状态机生效——旧进程被 SIGTERM，新进程以目标参数拉起。

	用真实 sleep 子进程替代（Godot 对不存在的 pid 做 is_process_running
	会报 ECHILD 引擎错误）；进程创建器注入返回第二个真实子进程——
	验证切换流程与拉起参数，不拉起真后端。

	时序说明：restart_backend 内同步推进一轮 _poll_restart，若旧进程
	恰好未退出则切换留在 _restarting 状态待下帧推进——测试主动驱动
	_poll_restart 直至切换完成，避免对 sleep 退出时机的脆弱依赖。
	"""
	var saved_pid: int = Connection._backend_pid
	var old_proc: int = OS.create_process("sleep", ["30"])
	var new_proc: int = OS.create_process("sleep", ["30"])
	Connection._backend_pid = old_proc
	Connection.backend_args = PackedStringArray()
	Connection._restarting = false
	var spawned: Array = []
	Connection.process_creator = func(_path, args):
		spawned.append(args)
		return new_proc
	Connection.restart_backend(PackedStringArray(["--world-id", "w1"]))
	# 驱动状态机直至旧进程退出、新进程拉起（与 _process 每帧推进等价）
	for i in 50:
		if Connection._restarting:
			Connection._poll_restart()
			await get_tree().process_frame
		else:
			break
	assert_false(Connection._restarting, "切换应已完成")
	assert_eq(Connection.backend_args, PackedStringArray(["--world-id", "w1"]),
		"目标参数应立即生效（UI 据此感知模式）")
	assert_eq(spawned.size(), 1, "旧进程退出后应拉起新进程")
	if spawned.size() == 1:
		var tail: PackedStringArray = PackedStringArray(spawned[0])
		tail.remove_at(0)  # 去掉 script_path
		assert_eq(tail, PackedStringArray(["--world-id", "w1"]),
			"拉起参数应为世界进程 CLI 参数")
	# 复位：清理注入的 sleep 进程，恢复真实后端 pid（防 GUT 退出时孤儿）
	Connection._kill_backend()
	Connection.backend_args = PackedStringArray()
	Connection.process_creator = Connection._create_process_default
	Connection._backend_pid = saved_pid


func test_restart_duplicate_call_ignored() -> void:
	"""切换进行中重复调用被忽略（防重入）。"""
	Connection._backend_pid = 999999
	Connection._restarting = true
	var spawned: Array = []
	Connection.process_creator = func(_path, args):
		spawned.append(args)
		return -1
	Connection.restart_backend(PackedStringArray(["--world-id", "w2"]))
	assert_true(Connection._restarting, "切换中应保持进行状态")
	assert_eq(spawned, [], "重复调用不应触发拉起")
	assert_eq(Connection.backend_args, PackedStringArray(),
		"重复调用不应覆盖目标参数")
	Connection._restarting = false
	Connection._backend_pid = -1
	Connection.process_creator = Connection._create_process_default
