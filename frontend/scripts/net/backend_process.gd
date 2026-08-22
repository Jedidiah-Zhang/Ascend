"""后端进程层 — 进程生命周期状态机（纯逻辑 RefCounted，测试可注入）。

职责（从 connection.gd 抽离）:
  - 启动序列：端口预探测 → 拉起后端（开发 .venv python / 打包 server 二进制）
    → 等待端口就绪（每 0.5s 探测一次）→ ready 信号
  - 启动超时（BACKEND_STARTUP_TIMEOUT）→ FAILED 终态 + failed 信号
  - 停止序列：SIGTERM（按 PID + 按名清理孤儿）→ 等端口释放
    → 超时强杀（按 PID -9 + 开发/打包按名 pattern）→ 端口仍占用则
    FAILED（不谎报 stopped——重启预探测会连上旧进程旧参数）
  - 参数语义：args 持有当前模式（[] 菜单 / ["--world-id", id, ...] 世界），
    world_id() 唯一解析处

时序全部由 tick(delta) 驱动（不使用 OS.delay_msec 忙等；stop_sync 例外，
仅供应用退出时使用）。测试注入 process_creator / probe_factory / kill 命令
即可确定性驱动。

依赖方向：connection(门面) → 本层；本层不感知其他子层。
"""

class_name BackendProcess
extends RefCounted



# ── 信号（向门面汇报） ─────────────────────────────────────

## 端口就绪（后端可连）：门面据此发起连接
signal ready
## 启动失败（终态）：reason 携带失败原因
signal failed(reason: String)
## 停止完成（端口已释放）：门面据此拉起新进程
signal stopped
## 后端进程已拉起
signal started(pid: int, args: PackedStringArray)


# ── 状态机 ─────────────────────────────────────────────────

enum State { IDLE, STARTING, READY, STOPPING, FAILED }

var state: State = State.IDLE


# ── 常量（唯一事实源 = config.gd） ─────────────────────────

const BACKEND_STARTUP_TIMEOUT: float = Config.BACKEND_STARTUP_TIMEOUT
const BACKEND_CHECK_INTERVAL: float = 0.5
const PROBE_TIMEOUT: float = 0.2
## 优雅停止超时（毫秒）
const BACKEND_STOP_TIMEOUT_MS: int = 3000
## 强杀后等待端口释放的超时（毫秒）
const FORCE_KILL_WAIT_MS: int = 3000
const VENV_PYTHON_REL: String = Config.VENV_PYTHON_REL
const BACKEND_SCRIPT_REL: String = Config.BACKEND_SCRIPT_REL


# ── 配置属性 ───────────────────────────────────────────────

var host: String
var port: int
var project_root: String
var data_root: String

## 当前进程模式参数（对外只读语义，门面经 backend_args 转发）
var args: PackedStringArray = PackedStringArray()
## 后端进程 PID（-1 = 未跟踪/未启动）
var pid: int = -1


# ── 注入点（测试替身） ────────────────────────────────────

## 进程创建器 (python_path, full_args) -> pid；返回 -1 = 拉起失败。
var process_creator: Callable = _create_process_default

## 端口探测流工厂 () -> 具备 connect_to_host/poll/get_status/
## disconnect_from_host 的对象（默认 StreamPeerTCP，测试可注入 fake）。
var probe_factory: Callable = _probe_default

## 优雅终止命令 (pid)，默认按 OS 分支。
var kill_command: Callable = _kill_term_default

## 强杀命令 (pid)，默认按 OS 分支。三路清理：按 PID -9（standalone
## 布局下跟踪 PID 即真实服务进程）+ 开发模式按脚本路径 pattern +
## 打包模式按服务二进制名（onefile fork 子进程等 PID 跟踪不到的场景）。
var force_kill_command: Callable = _force_kill_default

## 按名清理未跟踪进程 ()，默认按 OS 分支。
var kill_untracked_command: Callable = _kill_untracked_default


# ── 内部状态 ───────────────────────────────────────────────

enum Phase { PRE_SPAWN_PROBE, WAIT_FOR_PORT }
var _phase: int = Phase.PRE_SPAWN_PROBE
var _probe: Object = null
var _probe_elapsed: float = 0.0
var _startup_timer: float = 0.0
var _check_timer: float = 0.0
var _stop_elapsed: float = 0.0
var _force_killed: bool = false
var _retry_timer: float = 0.1


# ── 默认实现（分平台） ─────────────────────────────────────

func _is_windows() -> bool:
	return OS.get_name() == "Windows"


func _create_process_default(python_path: String, p_args: PackedStringArray) -> int:
	"""默认进程创建：OS.create_process（非阻塞）。"""
	return OS.create_process(python_path, p_args, false)


func _probe_default() -> Object:
	return StreamPeerTCP.new()


func _kill_term_default(p_pid: int) -> void:
	if _is_windows():
		OS.execute("taskkill", ["/PID", str(p_pid)])
	else:
		OS.execute("kill", ["-TERM", str(p_pid)])


func _force_kill_commands(p_pid: int) -> Array:
	"""强杀命令清单（纯函数，测试可断言内容）。

	三路清理：
	  - 按 PID SIGKILL（standalone 布局下跟踪 PID 即真实服务进程；
	    仅 pid > 0 时加入，防 kill -9 -1 误杀）；
	  - 开发模式按脚本路径（项目根绝对路径正则转义后精确匹配，
	    不误伤其它 python 进程）；
	  - 打包模式按服务二进制名（覆盖 fork 子进程等 PID 跟踪不到
	    的场景）。
	"""
	if _is_windows():
		var wcmds: Array = []
		if p_pid > 0:
			wcmds.append(["taskkill", ["/PID", str(p_pid), "/F"]])
		wcmds.append(["taskkill", ["/IM", "server.exe", "/F"]])
		return wcmds
	var cmds: Array = []
	if p_pid > 0:
		cmds.append(["kill", ["-9", str(p_pid)]])
	cmds.append(["pkill", ["-9", "-f", _dev_backend_pattern()]])
	cmds.append(["pkill", ["-9", "-f", "server/server"]])
	return cmds


func _force_kill_default(p_pid: int) -> void:
	for cmd: Array in _force_kill_commands(p_pid):
		OS.execute(cmd[0], cmd[1])


func _untracked_kill_commands() -> Array:
	"""按名清理命令清单（纯函数，测试可断言内容）。

	覆盖开发模式（脚本路径精确匹配）与打包模式（服务二进制名）
	两类孤儿；TERM 优雅，强杀见 _force_kill_commands。
	"""
	if _is_windows():
		return [["taskkill", ["/IM", "server.exe"]]]
	return [
		["pkill", ["-TERM", "-f", _dev_backend_pattern()]],
		["pkill", ["-TERM", "-f", "server/server"]],
	]


func _kill_untracked_default() -> void:
	for cmd: Array in _untracked_kill_commands():
		OS.execute(cmd[0], cmd[1])


## 开发模式后端的 pkill 匹配 pattern：项目内 run_server.py 绝对路径
## （pkill -f 按正则匹配整条命令行，元字符须转义；绝对路径保证
## 不误伤其它项目的开发后端）。
func _dev_backend_pattern() -> String:
	return _regex_escape(project_root.path_join(BACKEND_SCRIPT_REL))


## 正则元字符转义（pkill -f pattern 用）。
func _regex_escape(text: String) -> String:
	var out := text.replace("\\", "\\\\")
	for ch in [".", "+", "(", ")", "[", "]", "{", "}", "^", "$", "|", "*", "?"]:
		out = out.replace(ch, "\\" + ch)
	return out


func _init(p_host: String, p_port: int, p_project_root: String, p_data_root: String) -> void:
	host = p_host
	port = p_port
	project_root = p_project_root
	data_root = p_data_root


# ── 生命周期入口 ───────────────────────────────────────────

func start(p_args: PackedStringArray = PackedStringArray()) -> void:
	"""启动后端：先非阻塞探测端口（已开放则直接 ready），否则拉起进程。"""
	if state == State.STARTING or state == State.STOPPING:
		return  # 进行中忽略（防重入）
	args = p_args
	pid = -1
	state = State.STARTING
	_phase = Phase.PRE_SPAWN_PROBE
	_startup_timer = 0.0
	_check_timer = BACKEND_CHECK_INTERVAL
	_begin_probe()


func stop() -> void:
	"""优雅停止：SIGTERM + 按名清理 → 等端口释放 → 超时强杀 → stopped。

	由门面在进程切换（restart）时调用；tick 推进，不阻塞主线程。
	"""
	if state == State.STOPPING:
		return
	if state == State.IDLE or state == State.FAILED:
		state = State.IDLE
		stopped.emit()
		return
	kill_untracked_command.call()
	if pid > 0:
		kill_command.call(pid)
	state = State.STOPPING
	_stop_elapsed = 0.0
	_force_killed = false
	_begin_probe()


func stop_sync() -> void:
	"""阻塞版停止（仅应用退出时使用）：内部自驱 tick 直到 stopped。

	退出瞬间不再有帧循环，允许短暂的忙等（总上限 = 优雅 + 强杀超时）。
	"""
	stop()
	var budget: float = (BACKEND_STOP_TIMEOUT_MS + FORCE_KILL_WAIT_MS) / 1000.0 + 0.5
	while state == State.STOPPING and budget > 0.0:
		OS.delay_msec(50)
		tick(0.05)
		budget -= 0.05


func tick(delta: float) -> void:
	"""每帧推进状态机：探测轮询、启动超时、停止超时。"""
	match state:
		State.STARTING:
			_tick_starting(delta)
		State.STOPPING:
			_tick_stopping(delta)
		_:
			pass  # IDLE / READY / FAILED 无每帧动作


func _tick_starting(delta: float) -> void:
	match _phase:
		Phase.PRE_SPAWN_PROBE:
			_poll_probe(delta)
		Phase.WAIT_FOR_PORT:
			_startup_timer += delta
			if _startup_timer > BACKEND_STARTUP_TIMEOUT:
				push_warning("Connection: backend startup timed out after %.0fs" % BACKEND_STARTUP_TIMEOUT)
				_close_probe()
				# 超时收尾：按名 + 按 PID 清理悬挂进程（不等待端口释放：
				# 已进入 FAILED 终态，无连接会命中该端口）
				kill_untracked_command.call()
				if pid > 0:
					kill_command.call(pid)
				pid = -1
				state = State.FAILED
				failed.emit("backend startup timed out (%.0fs)" % BACKEND_STARTUP_TIMEOUT)
				return
			_check_timer -= delta
			if _check_timer <= 0.0:
				_check_timer = BACKEND_CHECK_INTERVAL
				_begin_probe()
			_poll_probe(delta)


func _tick_stopping(delta: float) -> void:
	_stop_elapsed += delta
	if _probe == null:
		# 上一枚探测已结束：间隔 0.1s 后重试探测端口占用
		_retry_timer -= delta
		if _retry_timer <= 0.0:
			_retry_timer = 0.1
			_begin_probe()
		return
	_poll_probe(delta)


# ── 端口探测机制 ───────────────────────────────────────────

## 探测目标语义随状态解释：
##   STARTING → 开放 = 后端就绪；封闭 = 未就绪（拉起/重试）
##   STOPPING → 开放 = 端口仍占用（继续等待/强杀）；封闭 = 已释放（完成）

func _begin_probe() -> void:
	_close_probe()
	var probe: Object = probe_factory.call()
	_probe_elapsed = 0.0
	_retry_timer = 0.1
	if probe.connect_to_host(host, port) != OK:
		_on_probe_failed()
		return
	_probe = probe


func _poll_probe(delta: float) -> void:
	if _probe == null:
		return
	_probe_elapsed += delta
	_probe.poll()
	match _probe.get_status():
		StreamPeerTCP.STATUS_CONNECTED:
			_close_probe()
			_on_probe_succeeded()
		StreamPeerTCP.STATUS_CONNECTING:
			if _probe_elapsed >= PROBE_TIMEOUT:
				_close_probe()
				_on_probe_failed()
		_:
			_close_probe()
			_on_probe_failed()


func _close_probe() -> void:
	if _probe != null:
		_probe.disconnect_from_host()
		_probe = null


func _on_probe_succeeded() -> void:
	match state:
		State.STARTING:
			_on_start_probe_open()
		State.STOPPING:
			_on_stop_probe_open()
		_:
			pass


func _on_probe_failed() -> void:
	match state:
		State.STARTING:
			_on_start_probe_closed()
		State.STOPPING:
			_on_stop_probe_closed()
		_:
			pass


func _on_start_probe_open() -> void:
	if _phase == Phase.PRE_SPAWN_PROBE:
		print("Connection: backend already running on %s:%d" % [host, port])
	else:
		print("Connection: backend ready on %s:%d (waited %.1fs)" % [host, port, _startup_timer])
	state = State.READY
	ready.emit()


func _on_start_probe_closed() -> void:
	if _phase == Phase.PRE_SPAWN_PROBE:
		_phase = Phase.WAIT_FOR_PORT
		_startup_timer = 0.0
		_spawn()
	# WAIT_FOR_PORT：探测失败 = 端口未就绪，交由检查计时器重试


func _on_stop_probe_open() -> void:
	# 端口仍占用：等待优雅超时后强杀，强杀后再等一轮
	if not _force_killed:
		if _stop_elapsed >= BACKEND_STOP_TIMEOUT_MS / 1000.0:
			push_warning("Connection: backend not stopped in time, force killing")
			force_kill_command.call(pid)
			_force_killed = true
			_stop_elapsed = 0.0
	else:
		if _stop_elapsed >= FORCE_KILL_WAIT_MS / 1000.0:
			# 强杀后端口仍未释放：不再谎报 stopped——重启预探测会
			# 命中旧进程并连上旧参数。置 FAILED 终态，由门面中止
			# 重启并通知 UI（须人工介入）。
			_close_probe()
			pid = -1
			_enter_failed_state(
				"backend process could not be killed (port %d still occupied)" % port
			)


func _on_stop_probe_closed() -> void:
	# 端口已释放：后端退出完成
	_finish_stop()


func _finish_stop() -> void:
	_close_probe()
	pid = -1
	state = State.IDLE
	stopped.emit()


# ── 拉起后端进程 ───────────────────────────────────────────

func _spawn() -> void:
	"""拉起后端进程（打包模式随包二进制 / 开发模式 .venv python）。"""
	var backend_binary: String = ""
	for candidate in [
		project_root.path_join("server").path_join("server"),
		project_root.path_join("server").path_join("server.exe"),
	]:
		if FileAccess.file_exists(candidate):
			backend_binary = candidate
			break

	var exec_path: String = ""
	var full_args: PackedStringArray = []
	if backend_binary != "":
		exec_path = backend_binary
		full_args = ["--project-root", project_root, "--data-root", data_root]
		full_args.append_array(args)
	else:
		var python_path: String = project_root.path_join(VENV_PYTHON_REL)
		var script_path: String = project_root.path_join(BACKEND_SCRIPT_REL)
		if not FileAccess.file_exists(python_path):
			push_warning("Connection: Python not found at %s" % python_path)
			_enter_failed_state("Python not found at %s" % python_path)
			return
		if not FileAccess.file_exists(script_path):
			push_warning("Connection: backend script not found at %s" % script_path)
			_enter_failed_state("backend script not found at %s" % script_path)
			return
		exec_path = python_path
		full_args = [script_path]
		full_args.append_array(args)

	var new_pid: int = process_creator.call(exec_path, full_args)
	if new_pid == -1:
		push_warning("Connection: failed to start backend process")
		_enter_failed_state("failed to start backend process")
		return

	pid = new_pid
	started.emit(pid, args)
	print("Connection: backend started (PID: %d, args: %s), waiting for port..." % [pid, str(args)])


func _enter_failed_state(reason: String) -> void:
	state = State.FAILED
	failed.emit(reason)


# ── 参数语义 ───────────────────────────────────────────────

func args_equal(other: PackedStringArray) -> bool:
	"""目标参数与当前参数比较（含空数组语义）。"""
	if args.size() != other.size():
		return false
	for i in args.size():
		if args[i] != other[i]:
			return false
	return true


func world_id() -> String:
	"""当前进程模式的世界 ID（--world-id 参数值；菜单模式返回空）。"""
	for i in args.size() - 1:
		if args[i] == "--world-id":
			return args[i + 1]
	return ""


func is_alive() -> bool:
	"""是否有已拉起的进程（pid > 0）。被门面幂等检查使用。"""
	return pid > 0
