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
	var msg: Dictionary = {"type": "request", "request_type": "test", "payload": {}}
	assert_false(msg.has("seq"))
	Connection.send(msg)
	assert_true(msg.has("seq"), "send 应自动添加 seq")
	assert_gt(msg["seq"], 0)


func test_send_multiple_messages() -> void:
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


# ── 后端进程终止（优雅关闭） ────────────────────────────────

func test_graceful_stop_constants() -> void:
	"""优雅终止常量：等待后端最终落盘的超时兜底。"""
	assert_eq(Connection.BACKEND_STOP_TIMEOUT_MS, 3000, "超时兜底 3s")


func test_kill_backend_noop_without_pid() -> void:
	"""无后端进程（pid<=0）时终止应立即返回，不阻塞、不报错。"""
	Connection._backend_pid = -1
	Connection._kill_backend()
	assert_eq(Connection._backend_pid, -1, "无进程时不应改动 pid")
