"""握手层 — token 加载/hello 发送/ack 等待/拒绝处理（纯逻辑 RefCounted）。

职责（从 connection.gd 抽离）:
  - start()：每次连接重读 token 文件 → 发送 hello{token, protocol_version}
  - tick()：HELLO_SENT 下累计 ack 等待，超时（HELLO_TIMEOUT）→ timeout 信号
  - on_message()：消费握手完成前的服务器消息：
      hello_ack → acked 信号（门面据此发 connection_established 并放行 send）
      error/hello → rejected(kind, reason)（门面据此重试或终态）
      其他 → 返回 false（调用方丢弃：认证前后端不会发普通消息）
  - 关键回归语义：ack 后停止计时，连接不再误触发 hello 超时重连

拒绝分类（RejectKind）：
  - VERSION_MISMATCH：服务端发 error 帧（协议版本不兼容，唯一会发的握手拒绝）
    ——永久性失败，重试无意义，策略应直接终态
  - ANOMALY：服务端行为异常（不应主动发 hello）——可重试

注入点：send_frame（门面注入 transport.send_frame）、token_path、codec。
依赖方向：connection(门面) → 本层；本层不感知其他子层。
"""

class_name Handshake
extends RefCounted

const Config = preload("res://scripts/config.gd")


# ── 信号（向门面汇报） ─────────────────────────────────────

signal acked
signal rejected(kind: RejectKind, reason: String)
signal timeout


# ── 状态机 ─────────────────────────────────────────────────

enum State { IDLE, HELLO_SENT, ACKED }

var state: State = State.IDLE


# ── 拒绝分类 ───────────────────────────────────────────────

enum RejectKind { VERSION_MISMATCH, ANOMALY }


# ── 常量（唯一事实源 = config.gd） ─────────────────────────

const HELLO_TIMEOUT: float = Config.HELLO_TIMEOUT
const PROTOCOL_VERSION: int = Config.PROTOCOL_VERSION


# ── 配置属性 ───────────────────────────────────────────────

var token: String = ""

## 服务端协商的 tile 数据 BLOB 版本（hello_ack 携带；默认 0 = 未知）。
var blob_version: int = 0


# ── 注入点 ─────────────────────────────────────────────────

var _codec: FrameCodec
var _send_frame: Callable
var _token_path: String
var _elapsed: float = 0.0


func _init(p_codec: FrameCodec, p_send_frame: Callable, p_token_path: String) -> void:
	_codec = p_codec
	_send_frame = p_send_frame
	_token_path = p_token_path


# ── 公共接口 ───────────────────────────────────────────────

func start() -> void:
	"""开始握手：重读 token（每次连接都重读，不缓存）→ 发送 hello 帧。"""
	token = _load_token()
	_elapsed = 0.0
	state = State.HELLO_SENT
	var msg: Dictionary = {
		"type": "hello",
		"seq": _codec.next_seq(),
		"payload": {
			"token": token,
			"protocol_version": PROTOCOL_VERSION,
			"tile_blob_version": Config.TILE_BLOB_VERSION,
		},
	}
	var frame: PackedByteArray = _codec.frame_encode(msg)
	if frame.is_empty():
		# 编码失败（消息含不可序列化值）→ 不发送空帧，按可重试异常交还门面
		push_error("Handshake: hello encode failed")
		rejected.emit(RejectKind.ANOMALY, "hello encode failed")
		state = State.IDLE
		_elapsed = 0.0
		return
	_send_frame.call(frame)


func reset() -> void:
	"""回到 IDLE（断线/切换时调用）；token 保留，下次 start 重读。"""
	state = State.IDLE
	_elapsed = 0.0


func tick(delta: float) -> void:
	"""仅 HELLO_SENT 下累计等待；超过 HELLO_TIMEOUT → timeout 信号。"""
	if state != State.HELLO_SENT:
		return
	_elapsed += delta
	if _elapsed > HELLO_TIMEOUT:  # 严格大于（等于时继续累计，防边界抖动）
		state = State.IDLE
		_elapsed = 0.0
		timeout.emit()


func is_acked() -> bool:
	return state == State.ACKED


## 消费握手完成前的服务器消息。返回 true = 已消费（不再广播）。
func on_message(msg: Dictionary) -> bool:
	if msg.get("type", "") == "hello_ack":
		state = State.ACKED
		_elapsed = 0.0  # 回归：ack 后停止计时，防 10s 后误超时
		# 服务端 BLOB 版本协商结果：以此作为 tile 数据解码基准
		# （前端本地上报的 Config.TILE_BLOB_VERSION 仅作握手前的客户端声明）
		blob_version = int(msg.get("payload", {}).get("blob_version", 0))
		acked.emit()
		return true
	if msg.get("type", "") == "error" and msg.get("request_type", "") == "hello":
		rejected.emit(RejectKind.VERSION_MISMATCH, str(msg.get("error", "")))
		state = State.IDLE
		_elapsed = 0.0
		return true
	if msg.get("type", "") == "hello":
		# 服务端不应主动发 hello；收到视为握手异常，交给门面重试
		rejected.emit(RejectKind.ANOMALY, "unexpected hello from server")
		state = State.IDLE
		_elapsed = 0.0
		return true
	return false


# ── 内部 ───────────────────────────────────────────────────

func _load_token() -> String:
	"""从 token_path 读取认证令牌（后端启动时写入）；缺失时以空 token 继续。"""
	if not FileAccess.file_exists(_token_path):
		push_error("Handshake: token file missing at %s" % _token_path)
		return ""
	var f: FileAccess = FileAccess.open(_token_path, FileAccess.READ)
	if f == null:
		push_error("Handshake: cannot open token file at %s" % _token_path)
		return ""
	var content: String = f.get_line().strip_edges()
	f.close()
	return content