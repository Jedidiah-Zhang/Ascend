"""Connection 门面 — 组合四个子层的连接编排（进程/传输/握手/解码）。

进程模型（一进程一模式）: 菜单进程（无参，服务模式）/ 世界进程
（--world-id 等）。进入世界/回滚/返回菜单 = restart_backend()：优雅停
旧进程 → 以目标参数拉起（异步 tick 状态机，_process 轮询推进）。

组合接线:
  process.ready  → transport.reset + handshake.reset + connect_to_host
  transport.connected → worker.start + handshake.start（发 hello）
  handshake.acked → connection_established + 放行 send
  handshake.rejected/timeout → transport.reset_for_reconnect（重连重握手）
  transport.disconnected → worker.stop + handshake.reset + 去重 connection_lost
  worker.drain 逐帧 → 未 ack 走 handshake.on_message；已 ack 广播

Status 为门面枚举（0-3，FAILED=3 测试锁定），在事件边界由子层状态
派生（传输层优先，其次进程 FAILED，否则 DISCONNECTED），非每帧派生。

restart_backend 为异步状态机（WAIT_STOP → RESET → SPAWN），不阻塞
主线程（旧实现 OS.delay_msec 忙等已移除）。
"""

extends Node

const Config = preload("res://scripts/config.gd")
const FrameCodecClass = preload("res://scripts/utils/frame_codec.gd")
const BackendProcessClass = preload("res://scripts/net/backend_process.gd")
const TcpTransportClass = preload("res://scripts/net/tcp_transport.gd")
const HandshakeClass = preload("res://scripts/net/handshake.gd")
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


# ── 子层（组合对象，测试可经 _set_layers 注入） ───────────

var _process_layer: BackendProcess
var _transport: TcpTransport
var _handshake: Handshake
var _worker: DecodeWorker


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
	_transport.disconnect_from_host()
	_worker.stop()
	_handshake.reset()
	_outage_emitted = true
	_sync_status()


func send(message: Dictionary) -> void:
	"""发送一条消息（握手完成前后端未认证，普通帧会被直接断开）。"""
	if not _handshake.is_acked():
		push_warning("Connection: send before handshake ack ignored")
		return
	if not message.has("seq"):
		message["seq"] = _codec.next_seq()
	var framed: PackedByteArray = _codec.frame_encode(message)
	if framed.is_empty():
		return
	_transport.send_frame(framed)


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
	"""启动失败（终态）：通知 UI，不再自动重试。"""
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
	"""连接失效：停解码线程、重置握手；主动切换中不广播断线。"""
	_worker.stop()
	_handshake.reset()
	if _restart_phase != RestartPhase.NONE:
		_sync_status()
		return
	if not _outage_emitted:
		_outage_emitted = true
		push_warning("Connection: connection lost")
		connection_lost.emit()
	_sync_status()


func _on_handshake_acked() -> void:
	"""握手完成：广播已连接，此后 send 放行。"""
	print("Connection: handshake ok")
	_outage_emitted = false
	connection_established.emit(_transport.host, _transport.port)
	_sync_status()


func _on_handshake_rejected(reason: String) -> void:
	"""认证/版本被拒：重连重握手（token 每次 start 重读）。"""
	push_warning("Connection: handshake rejected: %s, retrying" % reason)
	_transport.reset_for_reconnect()
	_sync_status()


func _on_handshake_timeout() -> void:
	"""等待 ack 超时：断开重连。"""
	push_warning("Connection: hello timeout (%.1fs), reconnecting" % Config.HELLO_TIMEOUT)
	_transport.reset_for_reconnect()
	_sync_status()


func _on_frame_received(body: PackedByteArray) -> void:
	"""传输层切出的完整帧体 → 后台解码线程。"""
	_worker.push(body)


# ── 解码消息收集 ──────────────────────────────────────────

func _collect_decoded() -> void:
	"""主线程收集已解码消息：握手完成前走握手消费，否则广播。"""
	for msg: Dictionary in _worker.drain():
		if not _handshake.is_acked():
			if _handshake.on_message(msg):
				continue
			push_warning("Connection: message before handshake ack dropped")
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
			if _process_layer.state == BackendProcessClass.State.FAILED:
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