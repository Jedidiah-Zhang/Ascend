extends GutTest

const Handshake = preload("res://scripts/net/handshake.gd")
const FrameCodecClass = preload("res://scripts/utils/frame_codec.gd")
const Config = preload("res://scripts/config.gd")
const TOKEN_PATH: String = "user://test_handshake_token"


# 编码失败注入桩（P2-34）
class StubCodec extends FrameCodecClass:
	var fail_encode: bool = false

	func frame_encode(message: Dictionary) -> PackedByteArray:
		if fail_encode:
			return PackedByteArray()
		return super.frame_encode(message)


# ── 夹具 ───────────────────────────────────────────────────

var _captured: Array = []
var _hs: Handshake = null
var _codec: FrameCodec = null


func _write_token(content: String) -> void:
	FileAccess.open(TOKEN_PATH, FileAccess.WRITE).store_string(content)


func _make_hs(p_token_path: String = TOKEN_PATH) -> Handshake:
	_captured.clear()
	_codec = FrameCodecClass.new()
	_hs = Handshake.new(_codec, func(body): _captured.append(body), p_token_path)
	return _hs


func _last_hello() -> Dictionary:
	var decoded: Dictionary = _codec.frame_decode(_captured[_captured.size() - 1], Config.MAX_MESSAGE_SIZE)
	return JsonCodec.decode(decoded["bodies"][0])


func before_each() -> void:
	_write_token("token-before")


func after_each() -> void:
	if FileAccess.file_exists(TOKEN_PATH):
		DirAccess.remove_absolute(TOKEN_PATH)
	_captured.clear()
	_hs = null


# ── start：hello 发送 ──────────────────────────────────────

func test_start_sends_hello_with_token() -> void:
	var hs := _make_hs()
	hs.start()
	assert_eq(_captured.size(), 1, "start 应发送一条 hello")
	assert_eq(hs.state, Handshake.State.HELLO_SENT)
	var msg: Dictionary = _last_hello()
	assert_eq(msg["type"], "hello")
	assert_gt(int(msg["seq"]), 0)
	assert_eq(msg["payload"]["token"], "token-before")
	assert_eq(msg["payload"]["protocol_version"], Config.PROTOCOL_VERSION)


func test_token_reloaded_each_start() -> void:
	var hs := _make_hs()
	hs.start()
	_write_token("token-after")
	hs.reset()
	hs.start()
	assert_eq(_captured.size(), 2, "二次握手应重发 hello")
	assert_eq(_last_hello()["payload"]["token"], "token-after",
		"token 必须每次重读（进程切换后变化）")
	assert_eq(hs.token, "token-after")


func test_seq_increments_across_starts() -> void:
	var hs := _make_hs()
	hs.start()
	hs.reset()
	hs.start()
	var s1: int = int(JsonCodec.decode(_codec.frame_decode(
		_captured[0], Config.MAX_MESSAGE_SIZE)["bodies"][0])["seq"])
	var s2: int = int(JsonCodec.decode(_codec.frame_decode(
		_captured[1], Config.MAX_MESSAGE_SIZE)["bodies"][0])["seq"])
	assert_eq(s2, s1 + 1, "序号应共享 codec 单调递增")


func test_missing_token_file_sends_empty_token() -> void:
	var hs := _make_hs("user://no_such_token_file")
	hs.start()
	assert_eq(_captured.size(), 1, "token 缺失不应阻止握手")
	assert_eq(_last_hello()["payload"]["token"], "", "缺失时以空 token 发送")
	assert_eq(hs.token, "")
	for err in get_errors():
		err.handled = true


func test_encode_failure_aborts_handshake() -> void:
	"""回归（P2-34）：hello 编码失败不发送空帧，按 ANOMALY 拒绝可重试。"""
	var codec: StubCodec = StubCodec.new()
	codec.fail_encode = true
	var sent: Array = []
	var hs: Handshake = Handshake.new(codec, func(body): sent.append(body), TOKEN_PATH)
	watch_signals(hs)
	hs.start()
	assert_eq(sent.size(), 0, "编码失败不得发送空帧")
	assert_eq(hs.state, Handshake.State.IDLE, "失败后回 IDLE（可重试）")
	assert_signal_emit_count(hs, "rejected", 1)
	for err in get_errors():
		err.handled = true


# ── ack 语义（核心回归） ───────────────────────────────────

func test_ack_consumes_and_signals() -> void:
	var hs := _make_hs()
	watch_signals(hs)
	hs.start()
	var consumed: bool = hs.on_message({"type": "hello_ack"})
	assert_true(consumed, "hello_ack 应被握手消费")
	assert_true(hs.is_acked())
	assert_eq(hs.state, Handshake.State.ACKED)
	assert_signal_emit_count(hs, "acked", 1)


func test_ack_stops_timeout_countdown() -> void:
	"""回归：握手成功后超时计时必须停止，否则连接必然 10s 后误触发重连。"""
	var hs := _make_hs()
	watch_signals(hs)
	hs.start()
	hs.on_message({"type": "hello_ack"})
	hs.tick(11.0)
	assert_eq(hs.state, Handshake.State.ACKED, "ack 后即使超过 HELLO_TIMEOUT 也不得超时")
	assert_signal_emit_count(hs, "timeout", 0)


func test_no_ack_timeout_fires_once() -> void:
	var hs := _make_hs()
	watch_signals(hs)
	hs.start()
	hs.tick(10.0)
	assert_eq(hs.state, Handshake.State.HELLO_SENT, "10s 内不应超时（严格大于判定）")
	assert_signal_emit_count(hs, "timeout", 0)
	hs.tick(0.1)
	assert_eq(hs.state, Handshake.State.IDLE, "超过超时应回到 IDLE")
	assert_signal_emit_count(hs, "timeout", 1)
	hs.tick(11.0)
	assert_signal_emit_count(hs, "timeout", 1, "IDLE 后不再计时")


func test_tick_when_idle_noop() -> void:
	var hs := _make_hs()
	watch_signals(hs)
	hs.tick(30.0)
	assert_eq(hs.state, Handshake.State.IDLE)
	assert_signal_emit_count(hs, "timeout", 0)
	assert_signal_emit_count(hs, "acked", 0)


# ── 拒绝与未消费消息 ───────────────────────────────────────

func test_rejected_error_message() -> void:
	var hs := _make_hs()
	watch_signals(hs)
	hs.start()
	var consumed: bool = hs.on_message({
		"type": "error", "request_type": "hello", "error": "invalid token",
	})
	assert_true(consumed, "hello 拒绝应被消费")
	assert_false(hs.is_acked())
	assert_eq(hs.state, Handshake.State.IDLE, "被拒后回 IDLE（重连重握手）")
	assert_signal_emit_count(hs, "rejected", 1)
	hs.tick(11.0)
	assert_signal_emit_count(hs, "timeout", 0, "被拒后不应再超时")


func test_rejected_carries_kind_and_reason() -> void:
	var hs := _make_hs()
	var captured: Array = []
	hs.rejected.connect(func(k, r): captured.append([k, r]))
	hs.start()
	hs.on_message({"type": "error", "request_type": "hello", "error": "version mismatch"})
	assert_eq(captured.size(), 1)
	assert_eq(captured[0][0], Handshake.RejectKind.VERSION_MISMATCH,
		"服务端 error 帧 = 版本不兼容（永久性失败）")
	assert_eq(captured[0][1], "version mismatch")


func test_unexpected_hello_classified_anomaly() -> void:
	var hs := _make_hs()
	var captured: Array = []
	hs.rejected.connect(func(k, r): captured.append([k, r]))
	hs.start()
	var consumed: bool = hs.on_message({"type": "hello"})
	assert_true(consumed, "服务端主动 hello 应被消费")
	assert_eq(captured.size(), 1)
	assert_eq(captured[0][0], Handshake.RejectKind.ANOMALY,
		"服务端不应主动发 hello，归类为可重试异常")


func test_unknown_pre_ack_message_not_consumed() -> void:
	var hs := _make_hs()
	hs.start()
	var consumed: bool = hs.on_message({"type": "request", "request_type": "ping"})
	assert_false(consumed, "非握手消息不应被消费（调用方丢弃）")
	assert_eq(hs.state, Handshake.State.HELLO_SENT, "状态不应改变")


func test_ack_after_rejection_cycle() -> void:
	var hs := _make_hs()
	hs.start()
	hs.on_message({"type": "error", "request_type": "hello", "error": "bad"})
	assert_false(hs.is_acked())
	hs.start()  # 重连后重新握手
	hs.on_message({"type": "hello_ack"})
	assert_true(hs.is_acked(), "拒绝→重握手→ack 应可恢复")


# ── reset ──────────────────────────────────────────────────

func test_reset_clears_state_keeps_token() -> void:
	var hs := _make_hs()
	hs.start()
	hs.on_message({"type": "hello_ack"})
	hs.reset()
	assert_eq(hs.state, Handshake.State.IDLE)
	assert_false(hs.is_acked())
	assert_eq(hs.token, "token-before", "reset 不重读 token（start 时才重读）")


func test_ack_needs_restart_after_reset() -> void:
	var hs := _make_hs()
	hs.start()
	hs.on_message({"type": "hello_ack"})
	assert_true(hs.is_acked())
	hs.reset()
	assert_false(hs.is_acked())
	hs.tick(11.0)
	assert_eq(hs.state, Handshake.State.IDLE)