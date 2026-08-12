extends GutTest

const BackendProcess = preload("res://scripts/net/backend_process.gd")
const Config = preload("res://scripts/config.gd")
const DEV_PY_REL: String = ".venv/bin/python"
const RUN_SERVER_REL: String = "backend/run_server.py"


# ── 测试替身 ──────────────────────────────────────────────

class FakeProbe:
	## open_after_polls < 0: 永不开放（超时判败）；0: 首次 poll 即开放
	var open_after_polls: int = -1
	var _polls: int = -1
	var _open: bool = false
	var host: String = ""
	var port: int = -1

	func connect_to_host(h: String, p: int) -> int:
		host = h
		port = p
		_polls = open_after_polls
		_open = false
		return OK

	func poll() -> void:
		if _polls >= 0:
			if _polls == 0:
				_open = true  # 先判开放再递减：0 = 首次 poll 即开放
			_polls -= 1

	func get_status() -> int:
		return 2 if _open else 1  # CONNECTED / CONNECTING

	func disconnect_from_host() -> void:
		_open = false


var _probe_queue: Array = []
var _probe_calls: int = 0
var _created_probes: Array = []
var _creator_calls: Array = []
var _kill_calls: Array = []
var _untracked_calls: int = 0
var _force_kill_calls: Array = []
var _proc: BackendProcess = null


func _make_probe(polls: int) -> FakeProbe:
	var p := FakeProbe.new()
	p.open_after_polls = polls
	return p


func _factory() -> Callable:
	_probe_calls = 0
	_created_probes.clear()
	return func() -> Object:
		_probe_calls += 1
		var probe: FakeProbe
		if _probe_queue.is_empty():
			probe = _make_probe(-1)  # 兜底：永不开放
		else:
			probe = _probe_queue.pop_front()
		_created_probes.append(probe)
		return probe


func _reset_fakes() -> void:
	_probe_queue.clear()
	_probe_calls = 0
	_created_probes.clear()
	_creator_calls.clear()
	_kill_calls.clear()
	_force_kill_calls.clear()
	_untracked_calls = 0


func _make_proc(p_root: String = "user://t_proj") -> BackendProcess:
	_proc = BackendProcess.new("127.0.0.1", 9081, p_root, p_root + "/data")
	_proc.process_creator = func(_path, args):
		_creator_calls.append([_path, args])
		return 4242
	_proc.probe_factory = _factory()
	_proc.kill_command = func(pid): _kill_calls.append(pid)
	_proc.force_kill_command = func(pid): _force_kill_calls.append(pid)
	_proc.kill_untracked_command = func(): _untracked_calls += 1
	return _proc


# ── 工程目录构造（spawn 路径解析依赖文件存在性） ────────────

func _mkfile(rel: String) -> void:
	FileAccess.open("user://t_proj/" + rel, FileAccess.WRITE).close()


func _setup_project() -> void:
	DirAccess.make_dir_recursive_absolute("user://t_proj/backend")
	DirAccess.make_dir_recursive_absolute("user://t_proj/.venv/bin")
	DirAccess.make_dir_recursive_absolute("user://t_proj/server")
	_mkfile(".venv/bin/python")
	_mkfile("backend/run_server.py")


# Godot 4.7 目录枚举 API 恒跳过隐藏项（.venv 无法枚举），改用 OS 级递归删除
func _rmtree(dir: String) -> void:
	var abs := ProjectSettings.globalize_path(dir)
	if OS.get_name() == "Windows":
		OS.execute("cmd.exe", ["/c", "rmdir", "/s", "/q", abs])
	else:
		OS.execute("rm", ["-rf", abs])


func before_each() -> void:
	_reset_fakes()
	DirAccess.make_dir_recursive_absolute("user://t_proj")


func after_each() -> void:
	_rmtree("user://t_proj")
	_reset_fakes()


# ── 初始化 ─────────────────────────────────────────────────

func test_init_defaults() -> void:
	var p := BackendProcess.new("h", 3, "r", "d")
	assert_eq(p.state, BackendProcess.State.IDLE)
	assert_eq(p.pid, -1)
	assert_eq(p.args, PackedStringArray())
	assert_eq(p.host, "h")
	assert_eq(p.port, 3)
	assert_eq(p.project_root, "r")
	assert_eq(p.data_root, "d")


# ── 启动：预探测分支 ───────────────────────────────────────

func test_pre_spawn_probe_success_no_spawn() -> void:
	_setup_project()
	var p := _make_proc()
	_probe_queue.append(_make_probe(0))  # 首次 poll 即开放
	watch_signals(p)
	p.start([])
	assert_eq(p.state, BackendProcess.State.STARTING)
	for i in 20:
		p.tick(0.1)
		if p.state == BackendProcess.State.READY:
			break
	assert_eq(p.state, BackendProcess.State.READY, "预探测成功应直达 READY")
	assert_eq(_creator_calls.size(), 0, "已运行的后端不应重新拉起")
	assert_signal_emit_count(p, "ready", 1)
	assert_eq(p.pid, -1, "接管外部进程不产生 PID")


func test_pre_spawn_probe_fail_then_spawn_dev_mode() -> void:
	_setup_project()
	var p := _make_proc()
	_probe_queue.append(_make_probe(-1))  # 端口未开放
	watch_signals(p)
	p.start(["--world-id", "w1"])
	# 预探测失败应立即拉起
	p.tick(0.1)
	p.tick(0.1)
	assert_eq(_creator_calls.size(), 1, "探测失败后应拉起后端")
	if _creator_calls.size() == 1:
		assert_eq(_creator_calls[0][0], "user://t_proj/" + DEV_PY_REL, "开发模式应使用 .venv python")
		var full: Array = _creator_calls[0][1]
		assert_eq(full[0], "user://t_proj/" + RUN_SERVER_REL, "首个参数应为 run_server.py")
		assert_eq(Array(full.slice(1)), [ "--world-id", "w1" ], "参数尾随 CLI 参数")
	assert_eq(p.pid, 4242, "spawn 后记录 PID")
	assert_signal_emit_count(p, "started", 1)


func test_spawn_packaged_binary() -> void:
	_setup_project()
	_mkfile("server/server")
	var p := _make_proc()
	_probe_queue.append(_make_probe(-1))
	p.start(["--world-id", "x"])
	p.tick(0.1)
	p.tick(0.1)
	assert_eq(_creator_calls.size(), 1)
	if _creator_calls.size() == 1:
		assert_eq(_creator_calls[0][0], "user://t_proj/server/server", "打包模式应执行随包二进制")
		assert_eq(Array(_creator_calls[0][1]),
			["--project-root", "user://t_proj", "--data-root", "user://t_proj/data", "--world-id", "x"])


func test_spawn_win_binary_preferred_after_posix() -> void:
	_setup_project()
	_mkfile("server/server.exe")
	var p := _make_proc()
	_probe_queue.append(_make_probe(-1))
	p.start([])
	p.tick(0.1)
	p.tick(0.1)
	assert_eq(_creator_calls[0][0], "user://t_proj/server/server.exe")


func test_spawn_python_missing_fails() -> void:
	DirAccess.make_dir_recursive_absolute("user://t_proj/backend")
	_mkfile("backend/run_server.py")
	var p := _make_proc()
	var reasons := _capture_failures(p)
	watch_signals(p)
	p.start([])
	p.tick(0.1)
	p.tick(0.1)
	assert_eq(_creator_calls.size(), 0, "python 缺失不应尝试拉起")
	assert_eq(p.state, BackendProcess.State.FAILED, "缺失依赖应进入 FAILED 终态")
	assert_eq(reasons, ["Python not found at user://t_proj/.venv/bin/python"])
	assert_signal_emit_count(p, "failed", 1)


func test_spawn_script_missing_fails() -> void:
	DirAccess.make_dir_recursive_absolute("user://t_proj/.venv/bin")
	_mkfile(".venv/bin/python")
	var p := _make_proc()
	var reasons := _capture_failures(p)
	p.start([])
	p.tick(0.1)
	p.tick(0.1)
	assert_eq(_creator_calls.size(), 0)
	assert_eq(p.state, BackendProcess.State.FAILED)
	assert_eq(reasons, ["backend script not found at user://t_proj/backend/run_server.py"])


func test_spawn_create_process_fails() -> void:
	_setup_project()
	_proc = BackendProcess.new("127.0.0.1", 9081, "user://t_proj", "user://t_proj/data")
	_proc.process_creator = func(_path, _args): return -1
	_proc.probe_factory = _factory()
	_probe_queue.append(_make_probe(-1))
	var reasons := _capture_failures(_proc)
	watch_signals(_proc)
	_proc.start([])
	_proc.tick(0.1)
	_proc.tick(0.1)
	assert_eq(_proc.state, BackendProcess.State.FAILED)
	assert_eq(reasons, ["failed to start backend process"])
	assert_signal_emit_count(_proc, "failed", 1)


## 在触发前连接 failed 回调，收集失败原因（信号无记忆，事后连接拿不到）
func _capture_failures(p: BackendProcess) -> Array:
	var reasons: Array = []
	p.failed.connect(func(r: String): reasons.append(r))
	return reasons


# ── 启动：等待端口与超时 ───────────────────────────────────

func test_wait_for_port_retries_until_ready() -> void:
	_setup_project()
	var p := _make_proc()
	_probe_queue.append(_make_probe(-1))  # 预探测失败
	_probe_queue.append(_make_probe(-1))  # 第一次等待探测失败
	_probe_queue.append(_make_probe(0))   # 第二次等待探测成功
	watch_signals(p)
	p.start([])
	p.tick(0.1)  # 预探测判败 → spawn
	var waited := 0
	for i in 60:
		p.tick(0.1)
		if p.state == BackendProcess.State.READY:
			break
		waited += 1
	assert_eq(p.state, BackendProcess.State.READY, "重试后应 ready")
	assert_gt(waited, 0)
	assert_eq(_creator_calls.size(), 1, "只有一次拉起")
	assert_signal_emit_count(p, "ready", 1)
	assert_signal_emit_count(p, "started", 1)
	# READY 后继续 tick 不应重复发射
	p.tick(0.1)
	p.tick(0.1)
	assert_signal_emit_count(p, "ready", 1)


func test_startup_timeout_fails_and_kills() -> void:
	_setup_project()
	var p := _make_proc()
	_probe_queue.append(_make_probe(-1))  # 预探测失败 → spawn
	_probe_queue.append(_make_probe(-1))  # 永久等待失败
	var reasons := _capture_failures(p)
	watch_signals(p)
	p.start([])
	p.tick(0.1)
	p.tick(0.1)
	var ticks := 0
	for i in 620:
		p.tick(0.1)
		if p.state == BackendProcess.State.FAILED:
			ticks = i
			break
	assert_eq(p.state, BackendProcess.State.FAILED, "超过启动超时应 FAILED")
	assert_gt(ticks, 590, "应在 60s 阈值附近判定")
	assert_signal_emit_count(p, "failed", 1)
	assert_eq(reasons, ["backend startup timed out (%.0fs)" % Config.BACKEND_STARTUP_TIMEOUT])
	assert_gt(_untracked_calls, 0, "超时应按名清理残留进程")
	assert_eq(_creator_calls.size(), 1, "超时后不应重复拉起")
	# FAILED 是终态：继续 tick 不改变
	p.tick(0.1)
	assert_eq(p.state, BackendProcess.State.FAILED)
	assert_signal_emit_count(p, "failed", 1)


func test_start_while_starting_ignored() -> void:
	_setup_project()
	var p := _make_proc()
	_probe_queue.append(_make_probe(-1))
	p.start([])
	p.tick(0.1)
	p.tick(0.1)  # 预探测在第二个 tick 判败并拉起
	p.start(["--world-id", "w2"])
	assert_eq(_creator_calls.size(), 1, "STARTING 中重复 start 应忽略")
	assert_eq(p.args, PackedStringArray(), "参数不应被覆盖")


# ── 停止 ───────────────────────────────────────────────────

func test_stop_graceful_releases_port() -> void:
	_setup_project()
	var p := _make_proc()
	_probe_queue.append(_make_probe(-1))
	p.start([])
	p.tick(0.1)
	p.tick(0.1)  # 预探测判败 → spawn
	assert_eq(p.pid, 4242)
	p.state = BackendProcess.State.READY  # 跳过等待，直达就绪
	watch_signals(p)
	_probe_queue.append(_make_probe(-1))  # 停止期探测：端口已释放
	p.stop()
	assert_eq(p.state, BackendProcess.State.STOPPING)
	for i in 20:
		p.tick(0.1)
		if p.state != BackendProcess.State.STOPPING:
			break
	assert_eq(p.state, BackendProcess.State.IDLE, "停止完成应回 IDLE")
	assert_eq(p.pid, -1, "PID 应复位")
	assert_eq(_untracked_calls, 1, "应先按名清理（孤儿服务兜底）")
	assert_eq(_kill_calls, [4242], "再按 PID 优雅终止")
	assert_eq(_force_kill_calls.size(), 0, "优雅路径不应强杀")
	assert_signal_emit_count(p, "stopped", 1)


func test_stop_kills_on_port_held_until_timeout() -> void:
	_setup_project()
	var p := _make_proc()
	_probe_queue.append(_make_probe(-1))
	p.start([])
	p.tick(0.1)
	p.tick(0.1)
	p.state = BackendProcess.State.READY
	watch_signals(p)
	# 端口被占用且永不释放：无限返回"开放"的探测
	_created_probes.clear()
	p.probe_factory = func() -> Object:
		_probe_calls += 1
		var probe: FakeProbe = _make_probe(0)
		_created_probes.append(probe)
		return probe
	p.stop()
	assert_eq(p.state, BackendProcess.State.STOPPING)
	# 每枚探测 0.1s tick：3.0s 优雅超时 → 强杀 → 再 3.0s 放弃
	for i in 80:
		p.tick(0.1)
		if p.state != BackendProcess.State.STOPPING:
			break
	assert_eq(p.state, BackendProcess.State.IDLE, "端口始终占用时超时放弃")
	assert_eq(_force_kill_calls.size(), 1, "强杀只执行一次")
	assert_eq(_kill_calls, [4242])
	assert_signal_emit_count(p, "stopped", 1)


func test_stop_when_idle_immediate() -> void:
	var p := _make_proc()
	watch_signals(p)
	p.stop()
	assert_eq(p.state, BackendProcess.State.IDLE)
	assert_signal_emit_count(p, "stopped", 1, "IDLE 停止应即时完成")
	assert_eq(_untracked_calls, 0)


func test_stop_when_stopping_ignored() -> void:
	_setup_project()
	var p := _make_proc()
	_probe_queue.append(_make_probe(-1))
	p.start([])
	p.tick(0.1)
	p.tick(0.1)
	p.state = BackendProcess.State.READY
	_probe_queue.append(_make_probe(-1))
	p.stop()
	p.stop()
	assert_eq(_kill_calls.size(), 1, "STOPPING 中重复 stop 应忽略")


func test_stop_from_starting() -> void:
	_setup_project()
	var p := _make_proc()
	_probe_queue.append(_make_probe(-1))
	watch_signals(p)
	p.start([])  # STARTING PRE_SPAWN_PROBE
	p.stop()     # 未拉起也可停止
	assert_eq(p.state, BackendProcess.State.STOPPING)
	_probe_queue.append(_make_probe(-1))  # 端口未占用 → 立即释放
	for i in 20:
		p.tick(0.1)
		if p.state != BackendProcess.State.STOPPING:
			break
	assert_eq(p.state, BackendProcess.State.IDLE)
	assert_signal_emit_count(p, "stopped", 1)
	assert_eq(_creator_calls.size(), 0, "停止不应触发拉起")


func test_stop_sync_blocking_until_stopped() -> void:
	_setup_project()
	var p := _make_proc()
	_probe_queue.append(_make_probe(-1))
	p.start([])
	p.tick(0.1)
	p.tick(0.1)
	p.state = BackendProcess.State.READY
	_probe_queue.append(_make_probe(-1))
	watch_signals(p)
	p.stop_sync()
	assert_eq(p.state, BackendProcess.State.IDLE)
	assert_signal_emit_count(p, "stopped", 1)


func test_stop_sync_when_noop() -> void:
	var p := _make_proc()
	watch_signals(p)
	p.stop_sync()
	assert_eq(p.state, BackendProcess.State.IDLE)


# ── 参数语义 ───────────────────────────────────────────────

func test_args_equal() -> void:
	var p := _make_proc()
	p.args = PackedStringArray()
	assert_true(p.args_equal(PackedStringArray()), "空数组相等")
	assert_false(p.args_equal(PackedStringArray(["--world-id", "a"])))
	p.args = PackedStringArray(["--world-id", "a"])
	assert_true(p.args_equal(PackedStringArray(["--world-id", "a"])))
	assert_false(p.args_equal(PackedStringArray(["--world-id", "b"])))
	assert_false(p.args_equal(PackedStringArray(["--world-id", "a", "extra"])))


func test_world_id_parsing() -> void:
	var p := _make_proc()
	assert_eq(p.world_id(), "")
	p.args = PackedStringArray(["--world-id", "save1"])
	assert_eq(p.world_id(), "save1")
	p.args = PackedStringArray(["--snapshot", "s.json", "--world-id", "w2"])
	assert_eq(p.world_id(), "w2")


# ── 杂项 ───────────────────────────────────────────────────

func test_is_alive() -> void:
	var p := _make_proc()
	p.pid = 7
	assert_true(p.is_alive())
	p.pid = -1
	assert_false(p.is_alive())


func test_tick_idle_noop() -> void:
	var p := _make_proc()
	watch_signals(p)
	p.tick(0.5)
	p.tick(0.5)
	assert_eq(p.state, BackendProcess.State.IDLE)
	assert_signal_emit_count(p, "ready", 0)
	assert_signal_emit_count(p, "stopped", 0)
	assert_signal_emit_count(p, "failed", 0)


func test_probe_factory_receives_host_port() -> void:
	var p := _make_proc()
	_probe_queue.append(_make_probe(0))
	p.start([])
	p.tick(0.1)
	assert_eq(_created_probes.size(), 1)
	var probe: FakeProbe = _created_probes[0]
	assert_eq(probe.host, "127.0.0.1")
	assert_eq(probe.port, 9081)