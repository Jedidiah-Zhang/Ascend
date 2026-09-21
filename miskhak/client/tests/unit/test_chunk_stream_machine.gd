"""ChunkStreamMachine 单元测试 — chunk 流式状态机的纯逻辑验证。

状态转移（结构性无双请求）:
    UNKNOWN → FIELD_REQUESTED → TILE_REQUESTED → RECEIVED → BUILT
异常路径：数据损坏降级 / 字段版异常降级 / 断线降级 / 陈旧响应丢弃。
"""

extends GutTest



func _make() -> ChunkStreamMachine:
	return ChunkStreamMachine.new()


# ── 基础 ──────────────────────────────────────────────────

func test_initial_state_is_unknown() -> void:
	var m := _make()
	assert_eq(m.get_state(Vector2i(0, 0)), ChunkStreamMachine.ChunkState.UNKNOWN)
	assert_eq(m.size(), 0)
	assert_eq(m.counts(), {"loaded": 0, "cached": 0, "pending": 0})


func test_reset_clears_all_states() -> void:
	var m := _make()
	m.mark_built(Vector2i(1, 1))
	m.reset()
	assert_eq(m.size(), 0)
	assert_eq(m.get_state(Vector2i(1, 1)), ChunkStreamMachine.ChunkState.UNKNOWN)


# ── 字段请求收集 ──────────────────────────────────────────

func test_collect_field_requests_marks_and_returns_coords() -> void:
	var m := _make()
	var center := Vector2i(5, 5)
	var coords: Array = m.collect_field_requests(center, 1)
	assert_eq(coords.size(), 9, "半径 1 = 3×3")
	assert_eq(m.size(), 9)
	assert_eq(m.get_state(center), ChunkStreamMachine.ChunkState.FIELD_REQUESTED)


func test_collect_field_requests_skips_registered_keys() -> void:
	var m := _make()
	m.mark_built(Vector2i(0, 0))
	var coords: Array = m.collect_field_requests(Vector2i(0, 0), 1)
	assert_eq(coords.size(), 8, "中心已 BUILT 不再收集")
	assert_false(coords.has([0, 0]))


# ── 完整请求限流 ──────────────────────────────────────────

func test_select_full_requests_requires_field_data() -> void:
	var m := _make()
	var key := Vector2i(0, 0)
	m.collect_field_requests(key, 0)  # → FIELD_REQUESTED
	# 无字段数据：不选中
	var selected: Array = m.select_full_requests(func(k): return false, 10)
	assert_eq(selected.size(), 0)
	assert_eq(m.get_state(key), ChunkStreamMachine.ChunkState.FIELD_REQUESTED)
	# 字段数据到达：选中并 → TILE_REQUESTED
	selected = m.select_full_requests(func(k): return true, 10)
	assert_eq(selected.size(), 1)
	assert_eq(selected[0], key)
	assert_eq(m.get_state(key), ChunkStreamMachine.ChunkState.TILE_REQUESTED)


func test_select_full_requests_respects_max_pending() -> void:
	var m := _make()
	m.collect_field_requests(Vector2i(0, 0), 1)  # 9 个 FIELD_REQUESTED
	var selected: Array = m.select_full_requests(func(k): return true, 3)
	assert_eq(selected.size(), 3, "限流最多 3 个在途")
	for s in selected:
		assert_eq(m.get_state(s), ChunkStreamMachine.ChunkState.TILE_REQUESTED)
	# 已选中的计入在途：再选只能补到上限
	var more: Array = m.select_full_requests(func(k): return true, 3)
	assert_eq(more.size(), 0, "3 个在途已满")


# ── 响应处理 ──────────────────────────────────────────────

func test_field_response_keeps_field_requested() -> void:
	var m := _make()
	var key := Vector2i(0, 0)
	m.collect_field_requests(key, 0)
	m.on_field_response(key)
	assert_eq(m.get_state(key), ChunkStreamMachine.ChunkState.FIELD_REQUESTED)


func test_field_response_downgrades_tile_requested() -> void:
	"""服务器对完整请求回字段版（异常）→ 降级重发完整请求。"""
	var m := _make()
	var key := Vector2i(0, 0)
	m.collect_field_requests(key, 0)
	m.select_full_requests(func(k): return true, 10)  # → TILE_REQUESTED
	m.on_field_response(key)
	assert_eq(m.get_state(key), ChunkStreamMachine.ChunkState.FIELD_REQUESTED)


func test_full_response_valid_marks_received() -> void:
	var m := _make()
	var key := Vector2i(0, 0)
	m.collect_field_requests(key, 0)
	m.on_full_response(key, true)
	assert_eq(m.get_state(key), ChunkStreamMachine.ChunkState.RECEIVED)


func test_full_response_invalid_requeues() -> void:
	"""数据损坏（长度不符）→ 降级 FIELD_REQUESTED 重新入队。"""
	var m := _make()
	var key := Vector2i(0, 0)
	m.collect_field_requests(key, 0)
	m.on_full_response(key, false)
	assert_eq(m.get_state(key), ChunkStreamMachine.ChunkState.FIELD_REQUESTED)


func test_should_drop_response_for_unknown() -> void:
	var m := _make()
	assert_true(m.should_drop_response(Vector2i(9, 9)), "UNKNOWN（已卸载）→ 丢弃")
	m.collect_field_requests(Vector2i(0, 0), 0)
	assert_false(m.should_drop_response(Vector2i(0, 0)), "已登记 → 消费")
	m.forget(Vector2i(0, 0))
	assert_true(m.should_drop_response(Vector2i(0, 0)), "遗忘后 → 丢弃")


# ── 构建 ──────────────────────────────────────────────────

func test_build_candidates_and_mark_built() -> void:
	var m := _make()
	var key := Vector2i(0, 0)
	m.collect_field_requests(key, 0)
	m.on_full_response(key, true)
	var candidates: Array = m.collect_build_candidates()
	assert_eq(candidates.size(), 1)
	assert_eq(candidates[0], key)
	m.mark_built(key)
	assert_eq(m.get_state(key), ChunkStreamMachine.ChunkState.BUILT)
	assert_eq(m.collect_build_candidates().size(), 0, "BUILT 不再待构建")


func test_mark_constructing_excludes_from_candidates() -> void:
	"""CONSTRUCTING（构建任务在飞）不应再被收集提交（防重复提交）。"""
	var m := _make()
	var key := Vector2i(0, 0)
	m.collect_field_requests(key, 0)
	m.on_full_response(key, true)
	m.mark_constructing(key)
	assert_eq(m.get_state(key), ChunkStreamMachine.ChunkState.CONSTRUCTING)
	assert_eq(m.collect_build_candidates().size(), 0, "CONSTRUCTING 不重复提交")
	assert_eq(m.counts(), {"loaded": 0, "cached": 1, "pending": 0},
		"CONSTRUCTING 计入 cached（数据已到，等待挂载）")


func test_disconnect_demotes_constructing_to_received() -> void:
	"""断线：构建在途降级 RECEIVED（数据保留，重连后重新提交构建）。"""
	var m := _make()
	var key := Vector2i(0, 0)
	m.collect_field_requests(key, 0)
	m.on_full_response(key, true)
	m.mark_constructing(key)
	m.on_disconnect(func(k): return false)
	assert_eq(m.get_state(key), ChunkStreamMachine.ChunkState.RECEIVED,
		"构建在途断线后应回 RECEIVED 等待重建")


# ── 断线降级 ──────────────────────────────────────────────

func test_disconnect_demotes_states() -> void:
	var m := _make()
	# 字段在途（无数据）→ 移除（UNKNOWN），返回 dropped
	var inflight := Vector2i(3, 2)
	m.collect_field_requests(inflight, 0)
	# 完整请求在途（有字段数据）→ 降级 FIELD_REQUESTED
	var tile := Vector2i(4, 2)
	m.collect_field_requests(tile, 0)
	m.select_full_requests(func(k): return k == tile, 10)
	# 字段已到（保持 FIELD_REQUESTED）→ 保留
	var field_done := Vector2i(5, 2)
	m.collect_field_requests(field_done, 0)
	# 已接收 / 已构建 → 保留
	m.on_full_response(Vector2i(6, 2), true)
	m.mark_built(Vector2i(7, 2))

	var dropped: Array = m.on_disconnect(func(k): return k == field_done or k == tile)
	assert_eq(dropped, [inflight], "仅无数据的字段在途被作废")
	assert_eq(m.get_state(inflight), ChunkStreamMachine.ChunkState.UNKNOWN)
	assert_eq(m.get_state(tile), ChunkStreamMachine.ChunkState.FIELD_REQUESTED, "完整请求在途降级")
	assert_eq(m.get_state(field_done), ChunkStreamMachine.ChunkState.FIELD_REQUESTED, "有数据的字段请求保留")
	assert_eq(m.get_state(Vector2i(6, 2)), ChunkStreamMachine.ChunkState.RECEIVED)
	assert_eq(m.get_state(Vector2i(7, 2)), ChunkStreamMachine.ChunkState.BUILT)


# ── 就绪判定与统计 ────────────────────────────────────────

func test_all_built_requires_full_neighborhood() -> void:
	var m := _make()
	var center := Vector2i(4, 4)
	assert_false(m.all_built(center, 1), "空状态未就绪")
	m.mark_built(Vector2i(3, 3))
	assert_false(m.all_built(center, 1), "部分邻域未就绪")
	for dx in range(-1, 2):
		for dy in range(-1, 2):
			m.mark_built(center + Vector2i(dx, dy))
	assert_true(m.all_built(center, 1), "3×3 全部 BUILT 就绪")


func test_counts_breakdown() -> void:
	var m := _make()
	m.collect_field_requests(Vector2i(0, 0), 0)  # pending
	m.collect_field_requests(Vector2i(1, 0), 0)
	m.select_full_requests(func(k): return true, 10)  # (1,0) → TILE_REQUESTED
	m.on_full_response(Vector2i(2, 0), true)  # cached
	m.mark_built(Vector2i(3, 0))  # loaded
	var c: Dictionary = m.counts()
	assert_eq(c, {"loaded": 1, "cached": 1, "pending": 2})


# ── 遗忘 ──────────────────────────────────────────────────

func test_forget_removes_state() -> void:
	var m := _make()
	var key := Vector2i(0, 0)
	m.mark_built(key)
	m.forget(key)
	assert_eq(m.get_state(key), ChunkStreamMachine.ChunkState.UNKNOWN)
	assert_eq(m.size(), 0)


func test_keys_iteration() -> void:
	var m := _make()
	m.collect_field_requests(Vector2i(0, 0), 0)
	m.mark_built(Vector2i(1, 1))
	var keys: Array = m.keys()
	assert_eq(keys.size(), 2)
	assert_true(keys.has(Vector2i(0, 0)))
	assert_true(keys.has(Vector2i(1, 1)))
