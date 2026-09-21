extends GutTest

const DecodeWorker = preload("res://scripts/net/decode_worker.gd")


var _worker: DecodeWorker = null


func _enc(msg: Dictionary) -> PackedByteArray:
	return JsonCodec.encode(msg)


func _make_worker() -> DecodeWorker:
	_worker = DecodeWorker.new()
	return _worker


## 轮询直到取到 expect 条消息（线程异步，限界等待）
func _pump(expect: int) -> Array[Dictionary]:
	var out: Array[Dictionary] = []
	for i in 200:
		for m in _worker.drain():
			out.append(m)
		if out.size() >= expect:
			break
		await get_tree().process_frame
	return out


func before_each() -> void:
	_make_worker()


func after_each() -> void:
	if _worker != null:
		_worker.stop()
	_worker = null


# ── 基本流转 ───────────────────────────────────────────────

func test_drain_empty_after_start() -> void:
	_worker.start()
	assert_eq(_worker.drain(), [], "启动后无输入应返回空")


func test_push_and_drain_roundtrip_ordered() -> void:
	_worker.start()
	_worker.push(_enc({"a": 1.0}))
	_worker.push(_enc({"b": 2.0}))
	_worker.push(_enc({"c": 3.0}))
	var out: Array[Dictionary] = await _pump(3)
	assert_eq(out.size(), 3)
	assert_eq_deep(out[0], {"a": 1.0})
	assert_eq_deep(out[1], {"b": 2.0})
	assert_eq_deep(out[2], {"c": 3.0})


func test_drain_returns_all_pending() -> void:
	_worker.start()
	_worker.push(_enc({"x": 1.0}))
	_worker.push(_enc({"y": 2.0}))
	var out: Array[Dictionary] = await _pump(2)
	assert_eq(out.size(), 2, "drain 应一次取走全部")


func test_bad_json_body_skipped() -> void:
	_worker.start()
	_worker.push("not-json".to_utf8_buffer())
	_worker.push(_enc({"ok": true}))
	var out: Array[Dictionary] = await _pump(1)
	assert_eq(out.size(), 1, "坏 JSON 应被跳过不阻塞后续")
	assert_eq_deep(out[0], {"ok": true})


# ── 生命周期边界 ───────────────────────────────────────────

func test_push_before_start_ignored() -> void:
	_worker.push(_enc({"a": 1.0}))
	assert_eq(_worker.drain(), [], "未启动 push 应静默忽略")
	_worker.start()
	assert_eq(_worker.drain(), [], "被忽略的输入不应延迟出现")


func test_stop_before_start_safe() -> void:
	DecodeWorker.new().stop()
	pass_test("未启动 stop 不崩溃")


func test_start_idempotent() -> void:
	_worker.start()
	_worker.start()
	_worker.push(_enc({"a": 1.0}))
	var out: Array[Dictionary] = await _pump(1)
	assert_eq(out.size(), 1, "重复 start 不应破坏队列")
	_worker.stop()
	_worker.stop()
	pass_test("重复 stop 不崩溃")


func test_restart_cycle_works() -> void:
	_worker.start()
	_worker.push(_enc({"first": 1.0}))
	var out1: Array[Dictionary] = await _pump(1)
	assert_eq(out1.size(), 1)
	_worker.stop()
	_worker.start()
	_worker.push(_enc({"second": 2.0}))
	var out2: Array[Dictionary] = await _pump(1)
	assert_eq(out2.size(), 1)
	assert_eq_deep(out2[0], {"second": 2.0})


func test_stop_with_pending_input_no_hang() -> void:
	_worker.start()
	for i in 50:
		_worker.push(_enc({"bulk": float(i)}))
	# 输入尚在被消费时停止：须无阻塞返回（sem 唤醒 + join）
	_worker.stop()
	assert_eq(_worker.drain(), [], "停止后队列清空")
	_worker.push(_enc({"after": 1.0}))
	assert_eq(_worker.drain(), [], "停止后 push 应被忽略")


func test_drain_after_stop_empty() -> void:
	_worker.start()
	_worker.push(_enc({"a": 1.0}))
	await _pump(1)
	_worker.stop()
	assert_eq(_worker.drain(), [], "stop 后 drain 返回空")