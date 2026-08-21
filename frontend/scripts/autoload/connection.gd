"""Connection 门面 — 组合四个子层的连接编排（进程/传输/握手/解码）。

进程模型（一进程一模式）: 菜单进程（无参，服务模式）/ 世界进程
（--world-id 等）。进入世界/回滚/返回菜单 = restart_backend()：优雅停
旧进程 → 以目标参数拉起（异步 tick 状态机，_process 轮询推进）。

组合接线:
  process.ready  → transport.reset + handshake.reset + connect_to_host
  transport.connected → worker.start + handshake.start（发 hello）
  handshake.acked → connection_established + 放行 send
  handshake.rejected/timeout → handshake_policy 判定（RETRY = 重连重握手，
    FAIL = FAILED 终态，不再自动重试）
  transport.disconnected → worker.stop + handshake.reset + 去重 connection_lost；
    握手进行中断开（token 失效/后端重启）同样计入握手失败预算
  worker.drain 逐帧 → 未 ack 走 handshake.on_message；已 ack 的
  response/error 先按 seq 命中挂起请求表（send 回调），未命中退回广播；
  event 一律广播

Status 为门面枚举（0-3，FAILED=3 测试锁定），在事件边界由子层状态
派生（传输层优先，其次进程 FAILED / 握手失败终态，否则 DISCONNECTED），
非每帧派生。

挂起请求（pending-request）: send(message, on_response) 登记
seq → 回调；响应/错误按 seq 精确配对（过期响应命中失败退回广播，
不会被错误消费）。清理策略：请求超时（REQUEST_TIMEOUT）投本地错误；
连接失效/主动切换/终态统一 _flush_pending 投"连接失效"错误——UI 忙
状态由回调复位，不再依赖广播必然到达。

握手失败策略（handshake_policy.gd）: 版本不兼容（后端 error 帧）立即
FAILED；token 失效/超时/异常计入重试预算（HANDSHAKE_MAX_RETRIES 次），
耗尽即 FAILED。重连间隔退避归 TcpTransport 所有（连续失败翻倍封顶，
成功复位）。FAILED 终态可经 connect_to_server() 重置重试。

restart_backend 为异步状态机（WAIT_STOP → RESET → SPAWN），不阻塞
主线程（不得用 OS.delay_msec 忙等）。
"""

extends Node

const Config = preload("res://scripts/config.gd")
const FrameCodecClass = preload("res://scripts/utils/frame_codec.gd")
const BackendProcessClass = preload("res://scripts/net/backend_process.gd")
const TcpTransportClass = preload("res://scripts/net/tcp_transport.gd")
const HandshakeClass = preload("res://scripts/net/handshake.gd")
const HandshakePolicyClass = preload("res://scripts/net/handshake_policy.gd")
const DecodeWorkerClass = preload("res://scripts/net/decode_worker.gd")


# ── 信号（对外契约不变） ──────────────────────────────────

signal connection_established(host: String, port: int)
signal connection_lost()
signal backend_failed(reason: String)
signal message_received(message: Dictionary)


# ── 枚举 ──────────────────────────────────────────────────

enum Status { DISCONNECTED, CONNECTING, CONNECTED, FAILED }
var status: Status = Status.DISCONNECTED


# ── 常量（唯一事实源 = config.gd；仅门面自用，协议/进程参数归各子层） ─

const DEFAULT_HOST: String = Config.DEFAULT_HOST
const DEFAULT_PORT: int = Config.DEFAULT_PORT
const TOKEN_FILE_REL: String = Config.TOKEN_FILE_REL


# ── 属性 ──────────────────────────────────────────────────

## 帧编解码器（send 与测试白盒共用）
var _codec: FrameCodec = FrameCodecClass.new()

## 当前进程模式参数（[] = 菜单，["--world-id", id, ...] = 世界）
var backend_args: PackedStringArray = PackedStringArray()

## 上帧 _process 耗时（微秒），供调试面板读取
var last_process_us: int = 0

## 主动进程切换（restart_backend）状态机相位
enum RestartPhase { NONE, WAIT_STOP, RESET, SPAWN }
var _restart_phase: RestartPhase = RestartPhase.NONE
var _restart_args: PackedStringArray = PackedStringArray()

## 当前断线事件是否已广播（断线期只广播一次，防失败重连刷屏）
var _outage_emitted: bool = false
var _suppress_disconnect: bool = false

## 握手失败终态原因（非空 = 连接层永久失败，Status 派生优先 FAILED；
## 经 connect_to_server/restart_backend 复位）
var _fatal_reason: String = ""

## 挂起请求表：seq → {request_type, callback, deadline}（send 带回调登记，
## 响应/错误按 seq 命中；超时/断线统一清理，见类文档）
var _pending_requests: Dictionary = {}


# ── 子层（组合对象，测试可经 _set_layers 注入） ───────────

var _process_layer: BackendProcess
var _transport: TcpTransport
var _handshake: Handshake
var _worker: DecodeWorker
var _policy: HandshakePolicy = HandshakePolicyClass.new()


# ── 生命周期 ──────────────────────────────────────────────

func _ready() -> void:
	"""自动加载初始化（编辑器跳过；运行时自动启动后端）。"""
	# 网络层必须免疫暂停：暂停菜单打开期间仍需收发消息（否则「正在保存...」卡死）
	process_mode = Node.PROCESS_MODE_ALWAYS
	if Engine.is_editor_hint():
		set_process(false)
		return
	_process_layer = BackendProcessClass.new(DEFAULT_HOST, DEFAULT_PORT, _project_root(), _data_root())
	_transport = TcpTransportClass.new(DEFAULT_HOST, DEFAULT_PORT)
	_handshake = HandshakeClass.new(_codec,
		func(body: PackedByteArray) -> void: _transport.send_frame_front(body),
		_data_root().path_join(TOKEN_FILE_REL))
	_worker = DecodeWorkerClass.new()
	_wire_layers()
	_process_layer.start([])  # 预探测已运行端口 → ready；否则拉起后端


func _notification(what: int) -> void:
	"""场景树通知：进程退出前同步停掉后端（阻塞版仅退出使用）。"""
	if what == NOTIFICATION_PREDELETE:
		if _worker != null:
			_worker.stop()
		if _process_layer != null:
			_process_layer.stop_sync()


func _process(delta: float) -> void:
	"""每帧推进：进程切换状态机 + 三个子层 tick + 解码消息收集。"""
	var t0: int = Time.get_ticks_usec()
	_advance_restart()
	_process_layer.tick(delta)
	_transport.tick(delta)
	_handshake.tick(delta)
	_collect_decoded()
	_tick_pending_timeouts()
	last_process_us = Time.get_ticks_usec() - t0


# ── 子层接线 ──────────────────────────────────────────────

func _signal_connect(p_signal: Signal, p_callable: Callable) -> void:
	if not p_signal.is_connected(p_callable):
		p_signal.connect(p_callable)


func _signal_disconnect(p_signal: Signal, p_callable: Callable) -> void:
	if p_signal.is_connected(p_callable):
		p_signal.disconnect(p_callable)


func _wire_layers() -> void:
	_signal_connect(_process_layer.ready, _on_process_ready)
	_signal_connect(_process_layer.failed, _on_process_failed)
	_signal_connect(_process_layer.stopped, _on_process_stopped)
	_signal_connect(_process_layer.started, _on_process_started)
	_signal_connect(_transport.connected, _on_transport_connected)
	_signal_connect(_transport.disconnected, _on_transport_disconnected)
	_signal_connect(_transport.frame_received, _on_frame_received)
	_signal_connect(_handshake.acked, _on_handshake_acked)
	_signal_connect(_handshake.rejected, _on_handshake_rejected)
	_signal_connect(_handshake.timeout, _on_handshake_timeout)


## 解绑当前子层（注入前清理旧连接，防替换后旧层事件误触门面）。
## 层未初始化（null）时跳过——测试钩子需支持在 _ready 前注入。
func _unwire_layers() -> void:
	if _process_layer == null or _transport == null or _handshake == null:
		return
	_signal_disconnect(_process_layer.ready, _on_process_ready)
	_signal_disconnect(_process_layer.failed, _on_process_failed)
	_signal_disconnect(_process_layer.stopped, _on_process_stopped)
	_signal_disconnect(_process_layer.started, _on_process_started)
	_signal_disconnect(_transport.connected, _on_transport_connected)
	_signal_disconnect(_transport.disconnected, _on_transport_disconnected)
	_signal_disconnect(_transport.frame_received, _on_frame_received)
	_signal_disconnect(_handshake.acked, _on_handshake_acked)
	_signal_disconnect(_handshake.rejected, _on_handshake_rejected)
	_signal_disconnect(_handshake.timeout, _on_handshake_timeout)


# ── 公共接口 ──────────────────────────────────────────────

func connect_to_server(host: String = DEFAULT_HOST, port: int = DEFAULT_PORT) -> void:
	"""重连（FAILED 终态可经此重置并重新尝试）。"""
	_transport.host = host
	_transport.port = port
	_flush_pending(tr("ui.common.connection_lost_retry"))
	_policy.reset()
	_fatal_reason = ""
	if _process_layer.state == BackendProcessClass.State.FAILED:
		# 终态重置：重新走完整启动流程
		_restart_args = backend_args
		_restart_phase = RestartPhase.RESET
		return
	_transport.reset()
	_handshake.reset()
	_transport.connect_to_host()
	_sync_status()


func disconnect_from_server() -> void:
	"""断开连接（静默，不广播 connection_lost；由调用方决定语义）。"""
	_flush_pending(tr("ui.common.connection_lost_retry"))
	_transport.disconnect_from_host()
	_worker.stop()
	_handshake.reset()
	_outage_emitted = true
	_sync_status()


## 发送一条消息（握手完成前后端未认证，普通帧会被直接断开）。
##
## on_response 非空时登记挂起请求：响应/错误按 seq 精确投递该回调
## （回调内用 msg.type 区分 response/error）。请求超时或连接失效时
## 投本地 error 消息（回调收到后应复位忙状态）。不登记回调的消息
## 走原广播路径（message_received 按 request_type 分发）。
func send(message: Dictionary, on_response: Callable = Callable()) -> void:
	if not _handshake.is_acked():
		push_warning("Connection: send before handshake ack ignored")
		return
	if not message.has("seq"):
		message["seq"] = _codec.next_seq()
	if on_response.is_valid():
		_pending_requests[int(message["seq"])] = {
			"request_type": str(message.get("request_type", "")),
			"callback": on_response,
			"deadline": Time.get_ticks_msec() + int(Config.REQUEST_TIMEOUT * 1000.0),
		}
	var framed: PackedByteArray = _codec.frame_encode(message)
	if framed.is_empty():
		return
	_transport.send_frame(framed)


## 逐帧推进挂起请求：超时（REQUEST_TIMEOUT）未响应 → 投本地错误并移除。
func _tick_pending_timeouts() -> void:
	if _pending_requests.is_empty():
		return
	var now: int = Time.get_ticks_msec()
	var expired: Array = []
	for seq: Variant in _pending_requests:
		if int(_pending_requests[seq]["deadline"]) <= now:
			expired.append(seq)
	for seq: Variant in expired:
		_deliver_pending(seq, _local_error(int(seq),
			_pending_requests[seq]["request_type"],
			tr("ui.common.request_timeout")))


## 按 seq 命中挂起请求：投递回调（响应或错误）并从表移除。命中失败返回 false。
func _deliver_pending(seq: Variant, msg: Dictionary) -> bool:
	# 键统一为 int：Godot JSON.parse 将数字解析为 float（如 2.0），
	# send() 登记时已 int 化，查表前同样归一化避免类型不匹配。
	seq = int(seq)
	if not _pending_requests.has(seq):
		return false
	var callback: Callable = _pending_requests[seq]["callback"]
	_pending_requests.erase(seq)
	if callback.is_valid():
		callback.call(msg)
	return true


## 全部挂起请求作废：投"连接失效"本地错误（UI 忙状态随之复位）并清表。
## 调用时机：连接断开（非握手预算路径）、主动切换、FAILED 终态。
func _flush_pending(reason: String) -> void:
	for seq: Variant in _pending_requests.keys():
		_deliver_pending(seq, _local_error(
			int(seq), _pending_requests[seq]["request_type"], reason))


## 本地构造的 error 消息（与后端 error 消息同构，回调消费路径一致）。
func _local_error(seq: int, request_type: String, reason: String) -> Dictionary:
	return {
		"type": "error",
		"seq": seq,
		"request_type": request_type,
		"error": reason,
	}


## 主动切换后端进程模式（进程模型：菜单 ⇄ 世界）。异步状态机：
##   WAIT_STOP: 优雅停旧进程（等端口释放，层内超时强杀兜底）
##   RESET:     清理旧连接残留
##   SPAWN:     以新参数拉起 → 预探测 → 重连 → 握手
func restart_backend(args: PackedStringArray = PackedStringArray()) -> void:
	if _restart_phase != RestartPhase.NONE:
		return  # 切换进行中忽略（防重入）
	if _process_layer.args_equal(args) and _process_layer.is_alive():
		return  # 已是目标模式
	backend_args = args  # 目标参数立即生效（UI 据此感知模式）
	_restart_args = args
	_flush_pending(tr("ui.common.connection_lost_retry"))
	_policy.reset()
	_fatal_reason = ""
	_transport.reset()
	_handshake.reset()
	_worker.stop()
	_outage_emitted = true
	_restart_phase = RestartPhase.WAIT_STOP
	_process_layer.stop()  # IDLE/FAILED 时同步发射 stopped → 相位推进
	_sync_status()


## 当前后端进程模式的世界 ID（--world-id 参数值；菜单模式返回空）。
## 参数语义只在此处解析，调用方不依赖参数布局。
func backend_world_id() -> String:
	for i in backend_args.size() - 1:
		if backend_args[i] == "--world-id":
			return backend_args[i + 1]
	return ""


## 底层进程创建器转发（测试可注入；默认 OS.create_process 真拉起）。
## 签名: (python_path, full_args) -> pid；返回 -1 = 拉起失败。
var process_creator: Callable:
	set(v):
		if _process_layer != null:
			_process_layer.process_creator = v
	get:
		return _process_layer.process_creator if _process_layer != null else Callable()


# ── 进程切换推进 ──────────────────────────────────────────

func _advance_restart() -> void:
	match _restart_phase:
		RestartPhase.RESET:
			_restart_phase = RestartPhase.SPAWN
			_transport.reset()
			_handshake.reset()
			_worker.stop()
		RestartPhase.SPAWN:
			_restart_phase = RestartPhase.NONE
			_process_layer.start(_restart_args)
		_:
			pass


# ── 子层事件处理 ──────────────────────────────────────────

func _on_process_ready() -> void:
	"""端口就绪（后端可连）：重置并发起连接。"""
	print("Connection: backend ready on %s:%d" % [_transport.host, _transport.port])
	_transport.reset()
	_handshake.reset()
	_transport.connect_to_host()
	_sync_status()


func _on_process_failed(reason: String) -> void:
	"""启动/停止失败（终态）：通知 UI，不再自动重试。

	切换中处于等待停止阶段时同步中止——旧进程未退出（强杀失败），
	不得拉起新进程连上旧参数的后端。
	"""
	if _restart_phase == RestartPhase.WAIT_STOP:
		_restart_phase = RestartPhase.NONE
	_flush_pending(tr("ui.common.connection_lost_retry"))
	_sync_status()
	backend_failed.emit(reason)


func _on_process_stopped() -> void:
	"""旧进程已退出：推进切换状态机（RESET → 下一帧 SPAWN）。"""
	if _restart_phase == RestartPhase.WAIT_STOP:
		_restart_phase = RestartPhase.RESET
	_sync_status()


func _on_process_started(pid: int, args: PackedStringArray) -> void:
	"""后端进程已拉起：等待端口就绪（READY 信号推进）。"""
	print("Connection: backend started (PID: %d, args: %s), waiting for port..." % [pid, str(args)])


func _on_transport_connected() -> void:
	"""TCP 建立：启动解码线程并发起握手。"""
	_outage_emitted = false
	_worker.start()
	_handshake.start()
	_sync_status()


func _on_transport_disconnected() -> void:
	"""连接失效：停解码线程、重置握手；握手进行中断开计入失败预算。"""
	if _suppress_disconnect:
		_suppress_disconnect = false
		return
	_worker.stop()
	var was_handshaking: bool = _handshake.state == HandshakeClass.State.HELLO_SENT
	_handshake.reset()
	if _restart_phase != RestartPhase.NONE:
		_sync_status()
		return
	if was_handshaking:
		# 已连上但握手未完成即被断开：token 失效/后端重启。
		# token 每次握手重读，后端重启后可能恢复 → 走预算重试。
		_handle_handshake_verdict(_policy.on_disconnect(), tr("ui.menu.handshake_auth_failed"))
		return
	if not _outage_emitted:
		_outage_emitted = true
		push_warning("Connection: connection lost")
		_flush_pending(tr("ui.common.connection_lost_retry"))
		connection_lost.emit()
	_sync_status()


func _on_handshake_acked() -> void:
	"""握手完成：预算清零、广播已连接，此后 send 放行。"""
	print("Connection: handshake ok")
	_policy.on_ack()
	_outage_emitted = false
	connection_established.emit(_transport.host, _transport.port)
	_sync_status()


func _on_handshake_rejected(kind: HandshakeClass.RejectKind, reason: String) -> void:
	"""认证/版本被拒：版本不兼容立即终态，其余计入预算重试。"""
	push_warning("Connection: handshake rejected: %s" % reason)
	var reason_key: String = "ui.menu.handshake_anomaly"
	if kind == HandshakeClass.RejectKind.VERSION_MISMATCH:
		reason_key = "ui.menu.handshake_version_mismatch"
	_handle_handshake_verdict(_policy.on_rejected(kind), tr(reason_key))


func _on_handshake_timeout() -> void:
	"""等待 ack 超时：后端挂起，计入预算重试。"""
	push_warning("Connection: hello timeout (%.1fs)" % Config.HELLO_TIMEOUT)
	_handle_handshake_verdict(_policy.on_timeout(), tr("ui.menu.handshake_timeout"))


## 握手失败统一裁定：策略判定重试（复位重连）或终态（不再自动重试）。
func _handle_handshake_verdict(verdict: HandshakePolicyClass.Verdict, reason: String) -> void:
	if verdict == HandshakePolicyClass.Verdict.FAIL:
		_transport.disconnect_from_host()
		_enter_failed(reason)
		return
	# 断线路径下 transport 已自行进入重连等待；rejected/timeout 路径
	# 连接仍挂着，需主动 reset（其 disconnected 信号被抑制，避免重试期广播）。
	if _transport.state != TcpTransportClass.State.DISCONNECTED:
		_suppress_disconnect = true
		_transport.reset_for_reconnect()
	_sync_status()


## 进入连接层终态：不再自动重试，通知 UI（可经 connect_to_server 重置）。
func _enter_failed(reason: String) -> void:
	_fatal_reason = reason
	_flush_pending(tr("ui.common.connection_lost_retry"))
	_sync_status()
	backend_failed.emit(reason)


func _on_frame_received(body: PackedByteArray) -> void:
	"""传输层切出的完整帧体 → 后台解码线程。"""
	_worker.push(body)


# ── 解码消息收集 ──────────────────────────────────────────

func _collect_decoded() -> void:
	"""主线程收集已解码消息：握手完成前走握手消费；已 ack 的
	response/error 按 seq 命中挂起请求回调，未命中退回广播；event 广播。"""
	for msg: Dictionary in _worker.drain():
		if not _handshake.is_acked():
			if _handshake.on_message(msg):
				continue
			push_warning("Connection: message before handshake ack dropped")
			continue
		var msg_type: String = msg.get("type", "")
		if (msg_type == "response" or msg_type == "error") \
				and _deliver_pending(msg.get("seq", -1), msg):
			continue
		message_received.emit(msg)


# ── 状态派生（事件边界） ──────────────────────────────────

func _sync_status() -> void:
	"""由子层状态派生门面 Status（事件边界调用，非每帧）。

	注意：主世界测试手动覆写 status（白盒 gate），事件边界派生不会碾平。
	"""
	match _transport.state:
		TcpTransportClass.State.CONNECTING:
			status = Status.CONNECTING
		TcpTransportClass.State.CONNECTED:
			status = Status.CONNECTED
		_:
			if _process_layer.state == BackendProcessClass.State.FAILED \
					or not _fatal_reason.is_empty():
				status = Status.FAILED
			else:
				status = Status.DISCONNECTED


# ── 路径解析 ──────────────────────────────────────────────

func _project_root() -> String:
	"""项目根（后端/令牌所在目录）。编辑器 = res:// 上级；发行 = 可执行文件目录。"""
	if OS.has_feature("editor"):
		return ProjectSettings.globalize_path("res://..")
	return OS.get_executable_path().get_base_dir()


func _data_root() -> String:
	"""数据根（token/日志落点）。编辑器 = 项目根；发行 = user://（可写）。"""
	if OS.has_feature("editor"):
		return _project_root()
	return ProjectSettings.globalize_path("user://")


# ── 测试钩子 ──────────────────────────────────────────────

## 注入子层实例（单元/集成测试替身）；重接线，防重复连接同一组。
func _set_layers(p_process: BackendProcess, p_transport: TcpTransport,
		p_handshake: Handshake, p_worker: DecodeWorker) -> void:
	_unwire_layers()
	_process_layer = p_process
	_transport = p_transport
	_handshake = p_handshake
	_worker = p_worker
	_wire_layers()


## 强制握手完成（等价旧测试白盒 _hello_acked = true）
func _force_handshake_acked() -> void:
	if _handshake != null:
		_handshake.state = HandshakeClass.State.ACKED


## 取走并清空传输层未发送帧（等价旧测试对 _send_queue 的读取）
func _drain_pending_frames() -> Array[PackedByteArray]:
	if _transport == null:
		return []
	return _transport.drain_pending_frames()
