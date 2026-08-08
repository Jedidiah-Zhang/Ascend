"""世界加载进度页集成测试（Issue #8）。

覆盖 scripts/ui/world_loading.gd：
  - 生成阶段进度文案（world_progress 事件）
  - world_initialized → 跨场景转发事件（Relay）+ 路由主世界
  - 失败兜底：连接中断 / 后端失败 / 超时 → 错误态
  - 重试：复位并重新拉起后端
"""

extends GutTest

const WORLD_LOADING_SCRIPT: String = "res://scripts/ui/world_loading.gd"


## 构造页面实例（注入 router/launcher 桩，隔离真实后端与场景切换）。
## 返回 [page, routed, launched]。
func _make_page() -> Array:
	var page: Control = load(WORLD_LOADING_SCRIPT).new()
	page.size = Vector2(1280, 720)
	autoqfree(page)
	add_child(page)
	# 测试隔离：断开 _ready 中建立的真实订阅
	if Connection.message_received.is_connected(page._on_message):
		Connection.message_received.disconnect(page._on_message)
	if Connection.connection_lost.is_connected(page._on_connection_lost):
		Connection.connection_lost.disconnect(page._on_connection_lost)
	if Connection.backend_failed.is_connected(page._on_backend_failed):
		Connection.backend_failed.disconnect(page._on_backend_failed)
	# 归一化状态（_ready 可能因真实连接就绪直接进入 LOADING）
	page._state = page.State.LAUNCHING
	page._stage_text = "正在启动世界进程..."
	page._elapsed = 0.0
	page._stage_index = -1
	page._lerp.reset()
	page._handed_off = false

	var routed: Array = []
	var launched: Array = []
	page.scene_router = func() -> void:
		routed.append(true)
	page.backend_launcher = func(args: PackedStringArray) -> void:
		launched.append(Array(args))
	return [page, routed, launched]


## 构造与后端真实协议一致的 event 消息（payload.data 层级）。
func _event(event_type: String, data: Dictionary = {}) -> Dictionary:
	return {"type": "event", "event_type": event_type, "payload": {"data": data}}


# ── 生成阶段进度 ──────────────────────────────────────────

func test_progress_updates_stage_text() -> void:
	var pair: Array = _make_page()
	var page: Control = pair[0]
	page._on_message(_event("world_progress", {"stage": "elevation"}))
	assert_eq(page._state, page.State.LOADING, "收到进度应离开启动态")
	assert_eq(page._stage_text, "正在生成地形...")


func test_progress_unknown_stage_fallback() -> void:
	var pair: Array = _make_page()
	var page: Control = pair[0]
	page._on_message(_event("world_progress", {"stage": "mystery"}))
	assert_eq(page._stage_text, "正在生成世界...", "未知阶段用兜底文案")


func test_non_world_events_ignored() -> void:
	var pair: Array = _make_page()
	var page: Control = pair[0]
	page._on_message({"type": "response", "request_type": "save_list", "payload": {}})
	page._on_message(_event("minute_change", {"data": {"day": 1}}))
	assert_eq(page._state, page.State.LAUNCHING, "无关消息不得改变状态")
	assert_eq(page._stage_text, "正在启动世界进程...")


# ── world_initialized → 转发 + 路由 ────────────────────────

func test_initialized_routes_and_marks_handoff() -> void:
	var pair: Array = _make_page()
	var page: Control = pair[0]
	var routed: Array = pair[1]
	var msg: Dictionary = _event("world_initialized", {"world_id": "w-1", "birth_chunk": [0, 0]})
	page._on_message(msg)
	assert_true(page._handed_off)
	assert_eq(routed.size(), 1, "就绪后应路由到主世界场景")


func test_initialized_event_relayed_next_frame() -> void:
	"""关键保障：切场景后 main_world 订阅就位时，world_initialized
	经 Relay（SceneTree process_frame ONE_SHOT）转发，事件不丢失。"""
	var pair: Array = _make_page()
	var page: Control = pair[0]
	var received: Array = []
	Connection.message_received.connect(func(m: Dictionary) -> void: received.append(m))
	var msg: Dictionary = _event("world_initialized", {"world_id": "w-1"})
	page._on_message(msg)
	await get_tree().process_frame
	assert_eq(received.size(), 1, "下一帧应重放 world_initialized")
	assert_eq(received[0]["event_type"], "world_initialized")
	assert_eq(received[0]["payload"]["data"]["world_id"], "w-1")


func test_after_handoff_events_ignored() -> void:
	var pair: Array = _make_page()
	var page: Control = pair[0]
	page._on_message(_event("world_initialized", {}))
	var stage_before: String = page._stage_text
	page._on_message(_event("world_progress", {"stage": "elevation"}))
	assert_eq(page._stage_text, stage_before, "交接后不再响应进度事件")


# ── 失败兜底 ──────────────────────────────────────────────

func test_connection_lost_enters_error() -> void:
	var pair: Array = _make_page()
	var page: Control = pair[0]
	page._on_connection_lost()
	assert_eq(page._state, page.State.ERROR)
	assert_string_contains(page._error_reason, "连接中断")


func test_backend_failed_shows_reason() -> void:
	var pair: Array = _make_page()
	var page: Control = pair[0]
	page._on_backend_failed("端口被占用")
	assert_eq(page._state, page.State.ERROR)
	assert_string_contains(page._error_reason, "端口被占用")


func test_backend_failed_after_handoff_ignored() -> void:
	"""已交接（即将进入主世界）时后端失败信号不再污染本页状态。"""
	var pair: Array = _make_page()
	var page: Control = pair[0]
	page._on_message(_event("world_initialized", {}))
	page._on_backend_failed("超时")
	assert_true(page._handed_off, "交接标记已置位")
	assert_ne(page._state, page.State.ERROR, "交接后忽略失败信号")


func test_timeout_enters_error() -> void:
	var pair: Array = _make_page()
	var page: Control = pair[0]
	page._elapsed = page.LOAD_TIMEOUT_SEC - 0.5
	page._process(1.0)
	assert_eq(page._state, page.State.ERROR)
	assert_string_contains(page._error_reason, "超时")


# ── 进度条 ──────────────────────────────────────────────────

func test_progress_bar_timeout_fallback_without_stages() -> void:
	"""未收到阶段事件时目标按超时比例推进（兜底，不假走满）。"""
	var pair: Array = _make_page()
	var page: Control = pair[0]
	assert_eq(page._progress_ratio(), 0.0, "初始无阶段、无耗时 → 0")
	assert_eq(page._progress_target(), 0.0)
	page._elapsed = page.LOAD_TIMEOUT_SEC * 0.5
	assert_almost_eq(page._progress_target(), 0.5, 0.01, "时间兜底目标过半")
	page._elapsed = page.LOAD_TIMEOUT_SEC * 10.0
	assert_eq(page._progress_target(), 0.9, "封顶 90%，不假走满")


func test_progress_bar_advances_per_stage() -> void:
	"""阶段事件逐格推进：elevation → 1/7 格，chunks（最后）→ 封顶。"""
	var pair: Array = _make_page()
	var page: Control = pair[0]
	page._on_message(_event("world_progress", {"stage": "elevation"}))
	assert_almost_eq(page._progress_target(), 1.0 / 7.0, 0.01, "首阶段目标 1/7")
	for stage in WorldStageLabels.ORDER.slice(1):
		page._on_message(_event("world_progress", {"stage": stage}))
	assert_eq(page._progress_target(), 0.9, "末阶段（chunks）目标封顶 90%")
	assert_eq(page._stage_index, WorldStageLabels.ORDER.size() - 1)


func test_progress_bar_unknown_stage_does_not_advance() -> void:
	var pair: Array = _make_page()
	var page: Control = pair[0]
	page._on_message(_event("world_progress", {"stage": "mystery"}))
	assert_eq(page._stage_index, -1, "未知阶段不推进刻度")
	assert_eq(page._progress_target(), 0.0)


func test_progress_bar_stage_beats_timeout_and_monotonic() -> void:
	"""阶段刻度与时间兜底取大；乱序/重复阶段不倒退。"""
	var pair: Array = _make_page()
	var page: Control = pair[0]
	page._elapsed = page.LOAD_TIMEOUT_SEC * 0.5  # 时间兜底 0.5
	page._on_message(_event("world_progress", {"stage": "done"}))
	assert_eq(page._stage_index, 5, "done = 第 6 格")
	assert_almost_eq(page._progress_target(), 6.0 / 7.0, 0.01, "阶段刻度胜过时间兜底")
	page._on_message(_event("world_progress", {"stage": "elevation"}))
	assert_eq(page._stage_index, 5, "乱序旧阶段不倒退")


func test_progress_bar_smooth_catch_up_within_window() -> void:
	"""阶段切换后连续爬升，不跳变：快速窗口内到位，且全程不越格。"""
	var pair: Array = _make_page()
	var page: Control = pair[0]
	page._on_message(_event("world_progress", {"stage": "elevation"}))
	var target: float = page._progress_target()
	var prev: float = 0.0
	for i in range(60):  # 0.96s，覆盖 0.6s 快速窗口
		page._process(0.016)
		assert_true(page._progress_ratio() >= prev, "进度连续不减")
		assert_true(page._progress_ratio() <= target, "不越过阶段刻度")
		prev = page._progress_ratio()
	assert_almost_eq(page._progress_ratio(), target, 0.01, "快速窗口内到位")


func test_progress_bar_drift_slow_after_catch_up() -> void:
	"""快速窗口过后转入缓慢爬升（不匀速）：1 秒增量远小于快速期。"""
	var pair: Array = _make_page()
	var page: Control = pair[0]
	page._on_message(_event("world_progress", {"stage": "elevation"}))
	for i in range(60):  # 0.96s，快速窗口已过且已到位
		page._process(0.016)
	var at_target: float = page._progress_ratio()
	assert_almost_eq(at_target, page._progress_target(), 0.01, "快速窗口内到位")
	for i in range(60):  # 慢速期 0.96s
		page._process(0.016)
	assert_true(page._progress_ratio() >= at_target, "慢速期持续缓爬")
	var gain: float = page._progress_ratio() - at_target
	assert_true(gain < 0.02, "慢速期 1 秒增量小（非匀速大跳，实际 ~0.01）")


func test_retry_resets_stage_progress() -> void:
	var pair: Array = _make_page()
	var page: Control = pair[0]
	page._on_message(_event("world_progress", {"stage": "width"}))
	assert_eq(page._stage_index, 4)
	page._on_connection_lost()
	page._retry()
	assert_eq(page._stage_index, -1, "重试复位阶段刻度")
	assert_eq(page._progress_ratio(), 0.0, "显示进度一并复位")


# ── 重试 ──────────────────────────────────────────────────

func test_retry_resets_and_relaunches() -> void:
	var pair: Array = _make_page()
	var page: Control = pair[0]
	var launched: Array = pair[2]
	page._on_connection_lost()
	page._retry()
	assert_eq(page._state, page.State.LAUNCHING)
	assert_eq(page._stage_text, "正在启动世界进程...")
	assert_eq(page._elapsed, 0.0)
	assert_eq(launched.size(), 1, "重试应重新拉起后端进程")
	assert_eq(launched[0], Array(Connection.backend_args))
