extends GutTest

const TcpTransport = preload("res://scripts/net/tcp_transport.gd")
const FrameCodecClass = preload("res://scripts/utils/frame_codec.gd")
const Config = preload("res://scripts/config.gd")


# ── 测试替身（StreamPeerTCP 接口子集） ─────────────────────

class FakeSocket:
	var status: int = 1                  # 1=CONNECTING 2=CONNECTED 3=ERROR 0=NONE
	var received: PackedByteArray = PackedByteArray()   # put_data 累积
	var available: int = 0
	var data: PackedByteArray = PackedByteArray()
	var fail_after: int = -1             # 前 N 次 put 成功，之后失败
	var host: String = ""
	var port: int = -1
	var closed: bool = false

	func connect_to_host(h: String, p: int) -> int:
		host = h
		port = p
		return OK

	func poll() -> void:
		pass

	func get_status() -> int:
		if closed:
			return 0
		return status

	func disconnect_from_host() -> void:
		closed = true

	func get_available_bytes() -> int:
		return available

	func get_partial_data(_max_bytes: int) -> Array:
		var chunk: PackedByteArray = data
		data = PackedByteArray()
		available = 0
		return [OK, chunk]

	func put_data(frame: PackedByteArray) -> Error:
		if fail_after >= 0:
			if fail_after == 0:
				return ERR_CONNECTION_ERROR
			fail_after -= 1
		received.append_array(frame)
		return OK


var _sock_queue: Array = []
var _factory_calls: int = 0
var _transport: TcpTransport = null
var _received_frames: Array = []


func _make_fake(status: int = 1) -> FakeSocket:
	var s := FakeSocket.new()
	s.status = status
	return s


func _reset_socks() -> void:
	_sock_queue.clear()
	_factory_calls = 0


func _factory() -> Callable:
	_factory_calls = 0
	return func() -> Object:
		_factory_calls += 1
		if _sock_queue.is_empty():
			return _make_fake()  # 兜底：CONNECTING 态
		return _sock_queue.pop_front() as Object


func _make_transport(p_host: String = "127.0.0.1", p_port: int = 9081) -> TcpTransport:
	_transport = TcpTransport.new(p_host, p_port)
	_transport.socket_factory = _factory()
	return _transport


func before_each() -> void:
	_reset_socks()


func after_each() -> void:
	_reset_socks()
	_transport = null


# ── 连接 ───────────────────────────────────────────────────

func test_connect_success() -> void:
	var t := _make_transport()
	var s := _make_fake(2)
	_sock_queue.append(s)
	watch_signals(t)
	t.connect_to_host()
	assert_eq(t.state, TcpTransport.State.CONNECTING)
	t.tick(0.016)
	assert_eq(t.state, TcpTransport.State.CONNECTED, "poll 后应转为 CONNECTED")
	assert_signal_emit_count(t, "connected", 1)
	assert_eq(s.host, "127.0.0.1")
	assert_eq(s.port, 9081)


func test_connect_emits_only_once() -> void:
	var t := _make_transport()
	_sock_queue.append(_make_fake(2))
	watch_signals(t)
	t.connect_to_host()
	for i in 5:
		t.tick(0.016)
	assert_signal_emit_count(t, "connected", 1, "CONNECTED 常驻不应重复发射")


func test_connect_error_status_resets() -> void:
	var t := _make_transport()
	_sock_queue.append(_make_fake(3))  # STATUS_ERROR
	watch_signals(t)
	t.connect_to_host()
	t.tick(0.016)
	assert_eq(t.state, TcpTransport.State.DISCONNECTED, "连接被拒应回到 DISCONNECTED")
	assert_signal_emit_count(t, "disconnected", 1)
	_sock_queue.append(_make_fake(1))
	t.tick(Config.RECONNECT_INTERVAL - 0.1)
	assert_eq(_factory_calls, 1, "重置后应按重连间隔等待")
	t.tick(0.2)
	assert_eq(_factory_calls, 2, "间隔满后应自动重连")


func test_connect_timeout_resets() -> void:
	var t := _make_transport()
	_sock_queue.append(_make_fake(1))  # 永久 CONNECTING
	watch_signals(t)
	t.connect_to_host()
	# 0.1s x 105 = 10.5s > CONNECTING_TIMEOUT
	for i in 105:
		t.tick(0.1)
		if t.state != TcpTransport.State.CONNECTING:
			break
	assert_eq(t.state, TcpTransport.State.DISCONNECTED, "超过连接超时应重连")
	assert_signal_emit_count(t, "disconnected", 1)


func test_auto_reconnect_after_interval() -> void:
	var t := _make_transport()
	_sock_queue.append(_make_fake(3))  # 首次连接被拒
	t.connect_to_host()
	t.tick(0.016)  # ERROR → DISCONNECTED
	assert_eq(_factory_calls, 1)
	_sock_queue.append(_make_fake(1))  # 重连：CONNECTING → 保持
	t.tick(Config.RECONNECT_INTERVAL - 0.1)
	assert_eq(_factory_calls, 1, "重连倒计时未满不应重连")
	t.tick(0.2)
	assert_eq(_factory_calls, 2, "倒计时满应自动重连")
	assert_eq(t.state, TcpTransport.State.CONNECTING)


func test_retarget_before_connect() -> void:
	var t := _make_transport("127.0.0.1", 1)
	t.host = "127.0.0.1"
	t.port = 9999
	var s := _make_fake(2)
	_sock_queue.append(s)
	t.connect_to_host()
	t.tick(0.016)
	assert_eq(s.port, 9999, "retarget 后应连新端口")


# ── 发送队列 ───────────────────────────────────────────────

func test_send_queued_and_flushed_when_connected() -> void:
	var t := _make_transport()
	var s := _make_fake(2)
	_sock_queue.append(s)
	t.connect_to_host()
	t.send_frame(PackedByteArray([1, 2, 3]))
	t.send_frame(PackedByteArray([4, 5]))
	t.tick(0.016)  # CONNECTED
	assert_eq(s.received, PackedByteArray([1, 2, 3, 4, 5]), "连接后应逐帧发送")
	assert_eq(t.drain_pending_frames().size(), 0, "发送后队列应清空")


func test_send_queued_before_connect_then_flushed_after() -> void:
	var t := _make_transport()
	var s1 := _make_fake(3)  # 首次连接被拒
	var s2 := _make_fake(2)
	_sock_queue.append(s1)
	t.connect_to_host()
	t.send_frame(PackedByteArray([9]))
	assert_eq(s1.received.size(), 0, "未连接不应发送")
	t.tick(0.016)  # 拒绝 → 断开
	_sock_queue.append(s2)
	t.tick(Config.RECONNECT_INTERVAL)  # 重连
	t.tick(0.016)
	assert_eq(s2.received, PackedByteArray([9]),
		"重连后队列中的消息应补发")


func test_send_flush_partial_failure_keeps_unsent() -> void:
	var t := _make_transport()
	var s := _make_fake(2)
	s.fail_after = 2  # 前 2 帧成功，第 3 帧失败
	_sock_queue.append(s)
	t.connect_to_host()
	t.send_frame(PackedByteArray([1]))
	t.send_frame(PackedByteArray([2]))
	t.send_frame(PackedByteArray([3]))
	t.tick(0.016)
	t.tick(0.016)
	assert_eq(s.received, PackedByteArray([1, 2]), "失败前已发送的不重发")
	var rest: Array = t.drain_pending_frames()
	assert_eq(rest.size(), 1, "仅保留未发送部分")
	assert_eq(rest[0], PackedByteArray([3]))
	for err in get_errors():
		err.handled = true


func test_send_front_flushed_before_queued() -> void:
	"""防护（Issue #34）：握手帧必须先于残留业务帧发送（push_front 会把
握手压到队尾）。"""
	var t := _make_transport()
	var s := _make_fake(2)
	_sock_queue.append(s)
	t.connect_to_host()
	t.send_frame(PackedByteArray([9, 9]))            # 残留业务帧
	t.send_frame_front(PackedByteArray([1]))          # hello 帧
	t.tick(0.016)
	assert_eq(s.received, PackedByteArray([1, 9, 9]),
		"队首帧必须先于已入队业务帧发出")


func test_send_front_empty_queue() -> void:
	var t := _make_transport()
	var s := _make_fake(2)
	_sock_queue.append(s)
	t.connect_to_host()
	t.send_frame_front(PackedByteArray([1]))
	t.tick(0.016)
	assert_eq(s.received, PackedByteArray([1]), "空队列队首入队等效正常发送")


# ── 接收 / 帧切分 ──────────────────────────────────────────

func test_frame_received_splits_and_joins_partial() -> void:
	var listener := TCPServer.new()
	assert_eq(listener.listen(0), OK)
	var t := _make_transport("127.0.0.1", listener.get_local_port())
	t.socket_factory = _socket_default_factory
	_received_frames = []
	t.frame_received.connect(func(b): _received_frames.append(b))
	watch_signals(t)
	t.connect_to_host()
	# helper：直到 TCP 建立并服务端 accept
	var server_conn: StreamPeerTCP = null
	for i in 100:
		t.tick(0.016)
		if listener.is_connection_available():
			server_conn = listener.take_connection()
			break
		await get_tree().process_frame
	assert_not_null(server_conn, "服务端应接受到连接")
	assert_eq(t.state, TcpTransport.State.CONNECTED)
	var codec := FrameCodecClass.new()
	var f1: PackedByteArray = codec.frame_encode({"n": 1.0})
	var f2: PackedByteArray = codec.frame_encode({"n": 2.0})
	var f3: PackedByteArray = codec.frame_encode({"n": 3.0})
	# 两帧 + 第三帧截半
	var partial: PackedByteArray = PackedByteArray()
	partial.append_array(f1)
	partial.append_array(f2)
	partial.append_array(f3.slice(0, 2))
	assert_eq(server_conn.put_data(partial), OK)
	for i in 100:
		t.tick(0.016)
		if _received_frames.size() >= 2:
			break
		await get_tree().process_frame
	assert_eq(_received_frames.size(), 2, "完整两帧应切出")
	var b1: PackedByteArray = _received_frames[0]
	assert_eq(JsonCodec.decode(b1), {"n": 1.0})
	# 补发剩余字节 → 第三帧
	server_conn.put_data(f3.slice(2))
	for i in 100:
		t.tick(0.016)
		if _received_frames.size() >= 3:
			break
		await get_tree().process_frame
	assert_eq(_received_frames.size(), 3, "补齐半帧应拼接还原")
	assert_eq(JsonCodec.decode(_received_frames[2]), {"n": 3.0})
	listener.stop()


func _socket_default_factory() -> StreamPeerTCP:
	return StreamPeerTCP.new()


func test_disconnected_emitted_when_socket_dies() -> void:
	var listener := TCPServer.new()
	assert_eq(listener.listen(0), OK)
	var t := _make_transport("127.0.0.1", listener.get_local_port())
	t.socket_factory = _socket_default_factory
	watch_signals(t)
	t.connect_to_host()
	var server_conn: StreamPeerTCP = null
	for i in 100:
		t.tick(0.016)
		if listener.is_connection_available():
			server_conn = listener.take_connection()
			break
		await get_tree().process_frame
	assert_eq(t.state, TcpTransport.State.CONNECTED)
	listener.stop()
	server_conn.disconnect_from_host()
	for i in 100:
		t.tick(0.016)
		if t.state != TcpTransport.State.CONNECTED:
			break
		await get_tree().process_frame
	assert_eq(t.state, TcpTransport.State.DISCONNECTED, "对端断开应进入重连")
	assert_signal_emit_count(t, "disconnected", 1)
	listener.stop()


# ── 收包超时 ───────────────────────────────────────────────

func test_receive_timeout_resets_and_data_resets_counter() -> void:
	var t := _make_transport()
	var s := _make_fake(2)
	_sock_queue.append(s)
	watch_signals(t)
	t.connect_to_host()
	t.tick(0.016)
	assert_eq(t.state, TcpTransport.State.CONNECTED)
	# 收到一次数据 → 空闲计数归零
	s.available = 1
	s.data = PackedByteArray([0x01])
	t.tick(0.016)
	# 59.9s 空闲不触发
	for i in 599:
		t.tick(0.1)
	assert_eq(t.state, TcpTransport.State.CONNECTED, "60s 内不应触发收包超时")
	t.tick(0.2)  # 总计 60.1s
	assert_eq(t.state, TcpTransport.State.DISCONNECTED, "超过收包超时应重连")
	assert_signal_emit_count(t, "disconnected", 1)


# ── 重连退避 ───────────────────────────────────────────────

func test_reconnect_interval_backs_off_and_caps() -> void:
	"""连续失败间隔指数翻倍并封顶（2→4→8→16→32→32…）。"""
	var t := _make_transport()
	_sock_queue.append(_make_fake(3))
	t.connect_to_host()
	t.tick(0.016)  # 连接被拒 → 首次失败
	assert_eq(t.reconnect_interval, Config.RECONNECT_INTERVAL * 2.0, "首次失败后间隔 4s")
	var expected_calls: int = 1
	for i in 5:  # 每轮：间隔满自动重连 → 新连接再失败 → 退避翻倍
		_sock_queue.append(_make_fake(3))
		t.tick(t.reconnect_interval + 0.1)
		expected_calls += 1
		assert_eq(_factory_calls, expected_calls, "间隔满应自动重连")
		t.tick(0.016)
	assert_eq(t.reconnect_interval, Config.RECONNECT_MAX_INTERVAL, "应封顶 32s")


func test_reconnect_interval_resets_on_success() -> void:
	"""任何一次连接成功即复位基础间隔。"""
	var t := _make_transport()
	_sock_queue.append(_make_fake(3))
	t.connect_to_host()
	t.tick(0.016)  # 失败 → 间隔 4s
	assert_eq(t.reconnect_interval, Config.RECONNECT_INTERVAL * 2.0)
	_sock_queue.append(_make_fake(3))
	t.tick(Config.RECONNECT_INTERVAL * 2.0 + 0.1)  # 4s 后重连
	t.tick(0.016)  # 再失败 → 间隔 8s
	assert_eq(t.reconnect_interval, Config.RECONNECT_INTERVAL * 4.0, "连续失败翻倍到 8s")
	_sock_queue.append(_make_fake(2))
	t.tick(Config.RECONNECT_INTERVAL * 4.0 + 0.1)  # 8s 后重连
	t.tick(0.016)  # CONNECTED
	assert_eq(t.reconnect_interval, Config.RECONNECT_INTERVAL, "成功后复位基础间隔")


func test_reconnect_interval_resets_on_manual_reset() -> void:
	"""reset/disconnect 复位退避（主动操作后回基础间隔，退避从头计）。"""
	var t := _make_transport()
	_sock_queue.append(_make_fake(3))
	t.connect_to_host()
	t.tick(0.016)  # 失败 → 间隔 4s
	assert_eq(t.reconnect_interval, Config.RECONNECT_INTERVAL * 2.0)
	t.reset()
	assert_eq(t.reconnect_interval, Config.RECONNECT_INTERVAL)
	_sock_queue.append(_make_fake(3))
	t.connect_to_host()
	t.tick(0.016)  # reset 后再失败 → 退避从头计
	assert_eq(t.reconnect_interval, Config.RECONNECT_INTERVAL * 2.0, "reset 后退避从头计")
	t.disconnect_from_host()
	assert_eq(t.reconnect_interval, Config.RECONNECT_INTERVAL, "手动断开也应复位")


# ── reset / disconnect ─────────────────────────────────────

func test_reset_clears_all_silently() -> void:
	var t := _make_transport()
	var s := _make_fake(2)
	_sock_queue.append(s)
	t.connect_to_host()
	t.send_frame(PackedByteArray([1]))
	t.tick(0.016)
	watch_signals(t)
	t.reset()
	assert_eq(t.state, TcpTransport.State.DISCONNECTED)
	assert_eq(t.drain_pending_frames().size(), 0, "reset 应清空发送队列")
	assert_true(s.closed, "reset 应断开 socket")
	assert_signal_emit_count(t, "disconnected", 0, "reset 不应广播断线")


func test_disconnect_silent_and_no_reconnect() -> void:
	var t := _make_transport()
	_sock_queue.append(_make_fake(2))
	t.connect_to_host()
	t.tick(0.016)
	watch_signals(t)
	t.disconnect_from_host()
	assert_eq(t.state, TcpTransport.State.DISCONNECTED)
	assert_signal_emit_count(t, "disconnected", 0)
	t.tick(Config.RECONNECT_INTERVAL + 1.0)
	assert_eq(_factory_calls, 1, "disconnect 后不应自动重连")


func test_reset_for_reconnect_keeps_send_queue() -> void:
	var t := _make_transport()
	_sock_queue.append(_make_fake(3))
	t.connect_to_host()
	t.send_frame(PackedByteArray([7]))
	t.tick(0.016)  # ERROR → reset_for_reconnect
	assert_eq(t.state, TcpTransport.State.DISCONNECTED)
	assert_eq(t.drain_pending_frames().size(), 1, "断线不应丢弃未发送请求")


func test_tick_idle_noop() -> void:
	var t := _make_transport()
	watch_signals(t)
	t.tick(0.5)
	t.tick(0.5)
	assert_eq(t.state, TcpTransport.State.DISCONNECTED)
	assert_signal_emit_count(t, "connected", 0)
	assert_signal_emit_count(t, "disconnected", 0)