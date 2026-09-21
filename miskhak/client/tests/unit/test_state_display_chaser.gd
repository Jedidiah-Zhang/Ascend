"""StateDisplayChaser 单元测试 — 显示值追赶真值（纯逻辑 RefCounted）。

覆盖：注册初始格子、逐帧抽样收敛、档位量化/擦除、加速衰减与天气事件
映射、卸载/重置、随机种子确定性。
"""

extends GutTest

const Chaser = preload("res://scripts/world/state_display_chaser.gd")

const CS: int = 40  # 测试用缩小边长（40×40=1600 tile；真实 chunk 为 200×200）


func _make_chaser() -> Chaser:
	var chaser: Chaser = Chaser.new()
	chaser.seed_rng(12345)
	return chaser


## 构造指定大小的三状态真值（moisture/snow/ice 顺序 = 后端 STATE_TYPES）。
func _make_states(snow_value: int, moisture_value: int = 0,
		ice_value: int = 0) -> Dictionary:
	var n: int = CS * CS
	var moisture := PackedByteArray()
	var snow := PackedByteArray()
	var ice := PackedByteArray()
	moisture.resize(n)
	snow.resize(n)
	ice.resize(n)
	moisture.fill(moisture_value)
	snow.fill(snow_value)
	ice.fill(ice_value)
	return {"moisture": moisture, "snow": snow, "ice": ice}


func test_set_truth_seeds_display_and_returns_initial_cells() -> void:
	var chaser: Chaser = _make_chaser()
	var states := _make_states(200)  # snow 200 → 档 3
	var cells: Array = chaser.set_truth(Vector2i(0, 0), states)
	assert_eq(chaser.chunk_count(), 1, "注册后 chunk 数应为 1")
	assert_eq(cells.size(), CS * CS, "snow 全量非零档 → 全 tile 初始格子")
	var sample: Array = cells[0]
	assert_eq(sample[0], Vector2i(0, 0), "首个格子应位于 (0,0)")
	assert_eq(sample[1], 4 + 3, "snow 档 3 → atlas 列 7（序 1 × 4 + 3）")
	assert_eq(chaser.display_value(Vector2i(0, 0), "snow", 0), 200,
		"显示值应以真值初始化")


func test_set_truth_level_zero_no_initial_cells() -> void:
	var chaser: Chaser = _make_chaser()
	var cells: Array = chaser.set_truth(Vector2i(0, 0), _make_states(0))
	assert_eq(cells.size(), 0, "全零真值 → 无初始格子")


func test_set_truth_update_keeps_display() -> void:
	var chaser: Chaser = _make_chaser()
	chaser.set_truth(Vector2i(0, 0), _make_states(0))
	var cells: Array = chaser.set_truth(Vector2i(0, 0), _make_states(255))
	assert_eq(cells.size(), 0, "更新真值不产生初始格子（显示值逐帧收敛）")
	assert_eq(chaser.display_value(Vector2i(0, 0), "snow", 5), 0,
		"显示值保持旧值，等待 advance 收敛")


func test_advance_converges_display_toward_truth() -> void:
	var chaser: Chaser = _make_chaser()
	chaser.set_truth(Vector2i(0, 0), _make_states(0))
	chaser.set_truth(Vector2i(0, 0), _make_states(255))  # 雪落满后真值升满
	for i in 6000:
		chaser.advance(1.0 / 60.0)
	for idx in CS * CS:
		assert_eq(chaser.display_value(Vector2i(0, 0), "snow", idx), 255,
			"长时间推进后显示值应全部收敛到真值")
	assert_eq(chaser.display_value(Vector2i(0, 0), "moisture", 0), 0,
		"真值为 0 的状态应收敛到 0（不越界）")


func test_advance_returns_changed_cells_with_column() -> void:
	var chaser: Chaser = _make_chaser()
	chaser.set_truth(Vector2i(0, 0), _make_states(0))
	chaser.set_truth(Vector2i(0, 0), _make_states(255))  # 真值升满，开始追赶
	chaser.add_boost(Chaser.STORM_BOOST_MULT, 20.0)
	var changed: Dictionary = {}
	for i in 600:
		var frame: Dictionary = chaser.advance(1.0 / 60.0)
		for key in frame:
			if not changed.has(key):
				changed[key] = []
			changed[key].append_array(frame[key])
	var cells: Array = changed.get(Vector2i(0, 0), [])
	assert_true(cells.size() > 0, "追赶期间应有跨档变化格子")
	for cell in cells:
		var col: int = cell[1]
		assert_between(col, 4, 7, "变化格子应落在 snow 列（4..7，档 1..3）")
		var pos: Vector2i = cell[0]
		assert_true(
			chaser.display_value(Vector2i(0, 0), "snow", pos.y * CS + pos.x) > 0,
			"变化格子的显示值应已被推进")


func test_advance_noop_without_chunks() -> void:
	var chaser: Chaser = _make_chaser()
	assert_eq(chaser.advance(1.0 / 60.0), {}, "无 chunk 时 advance 应为空")


func test_advance_erases_when_level_drops_to_zero() -> void:
	var chaser: Chaser = _make_chaser()
	chaser.set_truth(Vector2i(0, 0), _make_states(200))  # 显示 = 真值 = 200
	# 真值降为 0（解冻/雪化）：显示值应逐帧下降，档位归零 → 擦除（col = -1）
	chaser.set_truth(Vector2i(0, 0), _make_states(0))
	chaser.add_boost(Chaser.STORM_BOOST_MULT, 20.0)
	var saw_erase: bool = false
	for i in 4000:
		var changed: Dictionary = chaser.advance(1.0 / 60.0)
		for key in changed:
			for cell in changed[key]:
				if cell[1] < 0:
					saw_erase = true
	assert_true(saw_erase, "显示值降到档 0 应产生擦除格（col = -1）")
	for idx in CS * CS:
		assert_eq(chaser.display_value(Vector2i(0, 0), "snow", idx), 0,
			"长时间推进后显示值应全部归零")


func test_advance_skips_converged_chunks() -> void:
	var chaser: Chaser = _make_chaser()
	chaser.set_truth(Vector2i(0, 0), _make_states(200))  # 显示 ≡ 真值（收敛）
	chaser.advance(1.0)
	assert_eq(chaser.samples_taken(), 0, "已收敛 chunk 应跳过抽样（常态零开销）")


func test_advance_budget_capped() -> void:
	var chaser: Chaser = _make_chaser()
	for k in 4:
		chaser.set_truth(Vector2i(k, 0), _make_states(0))
		chaser.set_truth(Vector2i(k, 0), _make_states(255))  # 全部进入追赶
	chaser.advance(1.0)  # 单帧 delta=1s：预算 = 6400×0.6×1 = 3840 < 上限
	assert_eq(chaser.samples_taken(), 3840, "预算 = 总 tile × 速率 × delta（未触顶）")
	chaser.add_boost(Chaser.STORM_BOOST_MULT, 20.0)
	var boosted: int = chaser.samples_taken()
	chaser.advance(1.0)
	var diff: int = chaser.samples_taken() - boosted
	assert_lte(diff, Chaser.MAX_SAMPLES_PER_FRAME, "暴雪预算不超上限")
	assert_eq(diff, 11520, "6400×0.6×3 = 11520（低于上限，不截断）")
	# 更多 chunk（25 块，1.6 万 tile 级）：预算触顶截断
	for k in range(4, 25):
		chaser.set_truth(Vector2i(k % 5, k / 5), _make_states(0))
		chaser.set_truth(Vector2i(k % 5, k / 5), _make_states(255))
	var before: int = chaser.samples_taken()
	chaser.advance(1.0)
	var capped: int = chaser.samples_taken() - before
	assert_eq(capped, Chaser.MAX_SAMPLES_PER_FRAME,
		"总预算 40000×0.6×3 = 72000 → 截断到上限")


func test_add_boost_and_natural_decay() -> void:
	var chaser: Chaser = _make_chaser()
	assert_eq(chaser.boost_mult(), 1.0)
	chaser.add_boost(2.0, 30.0)
	assert_eq(chaser.boost_mult(), 2.0)
	assert_eq(chaser.boost_remaining(), 30.0)
	chaser.add_boost(3.0, 20.0)
	assert_eq(chaser.boost_mult(), 3.0, "更大倍率覆盖")
	assert_eq(chaser.boost_remaining(), 30.0, "时长取剩余较长者")
	chaser.add_boost(1.5, 100.0)
	assert_eq(chaser.boost_mult(), 3.0, "较小倍率不覆盖")
	assert_eq(chaser.boost_remaining(), 30.0, "较小倍率也不延长时长")
	chaser.advance(25.0)
	assert_eq(chaser.boost_remaining(), 5.0, "推进消耗时长")
	chaser.advance(10.0)
	assert_eq(chaser.boost_mult(), 1.0, "时长耗尽后倍率回落")
	assert_eq(chaser.boost_remaining(), 0.0)


func test_weather_events_map_to_boost() -> void:
	var chaser: Chaser = _make_chaser()
	chaser.on_weather_event("precipitation_start", {"precip_type": "snow"})
	assert_eq(chaser.boost_mult(), Chaser.SNOW_BOOST_MULT, "初雪事件 → 抽样加速")
	chaser.reset()
	chaser.on_weather_event("precipitation_start", {"precip_type": "rain"})
	assert_eq(chaser.boost_mult(), 1.0, "降雨不加速（仅降雪）")
	chaser.on_weather_event("storm_start", {})
	assert_eq(chaser.boost_mult(), Chaser.STORM_BOOST_MULT, "暴雪事件 → 更强加速")
	chaser.on_weather_event("precipitation_stop", {})
	assert_eq(chaser.boost_mult(), Chaser.STORM_BOOST_MULT, "停雨事件不取消加速（时长自然衰减）")
	chaser.on_weather_event("unknown_event", {})
	assert_eq(chaser.boost_mult(), Chaser.STORM_BOOST_MULT, "未知事件无副作用")


func test_weather_event_payload_data_nested() -> void:
	var chaser: Chaser = _make_chaser()
	chaser.on_weather_event("precipitation_start", {"data": {"precip_type": "snow"}})
	assert_eq(chaser.boost_mult(), Chaser.SNOW_BOOST_MULT, "事件字段嵌套 payload.data 也能识别")


func test_forget_removes_chunk() -> void:
	var chaser: Chaser = _make_chaser()
	var key := Vector2i(0, 0)
	chaser.set_truth(key, _make_states(200))
	chaser.forget(key)
	assert_eq(chaser.chunk_count(), 0, "遗忘后 chunk 数归零")
	assert_eq(chaser.advance(1.0), {}, "遗忘后 advance 不再触碰该 chunk")
	assert_eq(chaser.display_value(key, "snow", 0), -1, "显示值查询应失效")


func test_reset_clears_all() -> void:
	var chaser: Chaser = _make_chaser()
	chaser.set_truth(Vector2i(0, 0), _make_states(200))
	chaser.add_boost(2.0, 30.0)
	chaser.reset()
	assert_eq(chaser.chunk_count(), 0)
	assert_eq(chaser.boost_mult(), 1.0)
	assert_eq(chaser.boost_remaining(), 0.0)
	assert_eq(chaser.advance(1.0), {})
	# 重置后重新注册：状态序重新捕获（如新世界 BLOB 布局）
	chaser.set_truth(Vector2i(1, 1), _make_states(100))
	assert_eq(chaser.chunk_count(), 1, "重置后可重新注册")


func test_state_order_follows_first_truth_keys() -> void:
	var chaser: Chaser = _make_chaser()
	# 反序键 + ice 非零（否则 ice 无初始格子，首格被 snow 占据）
	var states := _make_states(0, 0, 200)
	var reordered: Dictionary = {
		"ice": states["ice"],
		"snow": states["snow"],
		"moisture": states["moisture"],
	}
	var cells: Array = chaser.set_truth(Vector2i(0, 0), reordered)
	assert_eq(cells[0][1], 3, "ice 为键序首位 → 列 0 + 档 3 = 3")


func test_level_quantization() -> void:
	assert_eq(Chaser._level(0), 0)
	assert_eq(Chaser._level(63), 0, "63 → 档 0（不渲染）")
	assert_eq(Chaser._level(64), 1, "64 → 档 1")
	assert_eq(Chaser._level(127), 1)
	assert_eq(Chaser._level(128), 2, "128 → 档 2")
	assert_eq(Chaser._level(255), 3, "255 → 档 3")


func test_boost_increases_sample_throughput() -> void:
	var chaser: Chaser = _make_chaser()
	chaser.set_truth(Vector2i(0, 0), _make_states(0))
	chaser.set_truth(Vector2i(0, 0), _make_states(255))
	for i in 60:
		chaser.advance(1.0 / 60.0)
	var base_samples: int = chaser.samples_taken()
	chaser.reset()
	chaser.seed_rng(12345)
	chaser.set_truth(Vector2i(0, 0), _make_states(0))
	chaser.set_truth(Vector2i(0, 0), _make_states(255))
	chaser.add_boost(Chaser.STORM_BOOST_MULT, 20.0)
	for i in 60:
		chaser.advance(1.0 / 60.0)
	var boosted_samples: int = chaser.samples_taken()
	assert_true(boosted_samples > base_samples * 2,
		"加速期抽样次数应约为基准 ×3（%d vs %d）" % [boosted_samples, base_samples])