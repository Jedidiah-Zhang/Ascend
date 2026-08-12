"""后台解码线程层 — JSON 解析移出主线程的队列封装（纯逻辑 RefCounted）。

职责（从 connection.gd 抽离）:
  - start/stop：线程生命周期（幂等；stop 唤醒并 join）
  - push(body)：主线程入队帧体（线程安全）
  - drain()：主线程取出已解码消息（线程安全，空则返回空数组）
  - 坏 JSON 帧体跳过（不崩溃）

依赖方向：connection(门面) → 本层；本层不感知其他子层。
"""

class_name DecodeWorker
extends RefCounted


var _thread: Thread = null
var _mutex: Mutex = null
var _sem: Semaphore = null
var _input: Array[PackedByteArray] = []
var _output: Array[Dictionary] = []
var _running: bool = false


func _init() -> void:
	_mutex = Mutex.new()
	_sem = Semaphore.new()


func start() -> void:
	"""启动后台解码线程（幂等：已启动则忽略）。"""
	if _running:
		return
	_mutex.lock()
	_input.clear()
	_output.clear()
	_mutex.unlock()
	_running = true
	_thread = Thread.new()
	_thread.start(_worker)


func stop() -> void:
	"""停止并清理线程（幂等；未启动调用也无害）。"""
	if _thread == null:
		return
	_running = false
	_sem.post()  # 唤醒可能阻塞在 wait 的 worker
	_thread.wait_to_finish()
	_thread = null
	_mutex.lock()
	_input.clear()
	_output.clear()
	_mutex.unlock()


func push(body: PackedByteArray) -> void:
	"""入队一条待解码帧体（未启动时忽略）。"""
	if not _running:
		return
	_mutex.lock()
	_input.append(body)
	_mutex.unlock()
	_sem.post()


func drain() -> Array[Dictionary]:
	"""取走全部已解码消息（未启动时返回空数组）。"""
	_mutex.lock()
	var out: Array[Dictionary] = _output.duplicate()
	_output.clear()
	_mutex.unlock()
	return out


func _worker() -> void:
	while true:
		_sem.wait()
		if not _running:
			return
		_mutex.lock()
		var batch: Array[PackedByteArray] = _input.duplicate()
		_input.clear()
		_mutex.unlock()
		for body in batch:
			# 直接解析片段（不走 JsonCodec：其失败 push_error 在后台线程
			# 会污染错误收集；JSON.parse_string 会打 engine 条件错误。
			# 实例 API 静默返回错误码，坏帧属预期输入）
			var json := JSON.new()
			if json.parse(body.get_string_from_utf8()) != OK:
				continue  # 坏 JSON 帧体跳过
			var msg: Variant = json.data
			if not (msg is Dictionary) or (msg as Dictionary).is_empty():
				continue
			_mutex.lock()
			_output.append(msg)
			_mutex.unlock()