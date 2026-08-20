"""WorldLoadingFlow 单元测试 — 地形就绪/补满收尾的纯逻辑计时状态机。

无场景依赖：计时累积/幂等闸/兜底倒计时/进度计算均为纯逻辑。
"""

extends GutTest

const WorldLoadingFlow = preload("res://scripts/world/world_loading_flow.gd")


func test_tick_accumulates_until_ready_timeout() -> void:
	var flow := WorldLoadingFlow.new()
	var act: Dictionary = flow.tick(1.0)
	assert_false(act["force_ready"], "未超时前不强判就绪")
	act = flow.tick(WorldLoadingFlow.TERRAIN_READY_TIMEOUT)
	assert_true(act["force_ready"], "达到超时阈值应触发强判")
	assert_false(act["force_finish"], "未开始补满收尾不触发强收尾")


func test_tick_stops_accumulating_after_completion_started() -> void:
	"""补满收尾开始后不再累积就绪计时（避免重复触发强判）。"""
	var flow := WorldLoadingFlow.new()
	assert_true(flow.begin_completion())
	assert_false(flow.begin_completion(), "幂等：重复开始返回 false")
	var act: Dictionary = flow.tick(999.0)
	assert_false(act["force_ready"], "补满已开始后不再强判就绪")
	assert_true(act["force_finish"], "补满兜底倒计时归零应强制收尾")


func test_tick_fallback_decrements_toward_force_finish() -> void:
	var flow := WorldLoadingFlow.new()
	flow.begin_completion()
	assert_eq(flow._completion_fallback_sec, WorldLoadingFlow.COMPLETION_FALLBACK_SEC)
	var act: Dictionary = flow.tick(WorldLoadingFlow.COMPLETION_FALLBACK_SEC)
	assert_true(act["force_finish"], "兜底计时器归零应触发强制收尾")


func test_finish_stops_timers() -> void:
	var flow := WorldLoadingFlow.new()
	flow.begin_completion()
	flow.finish()
	assert_eq(flow._completion_fallback_sec, 0.0)
	assert_eq(flow._terrain_ready_timer, 0.0)
	var act: Dictionary = flow.tick(100.0)
	assert_false(act["force_ready"], "收尾后不再触发强判")
	assert_false(act["force_finish"], "收尾后不再触发强收尾")


func test_reset_clears_state() -> void:
	var flow := WorldLoadingFlow.new()
	flow.begin_completion()
	flow.reset()
	assert_false(flow._loading_completed)
	assert_eq(flow._terrain_ready_timer, 0.0)
	assert_eq(flow._completion_fallback_sec, 0.0)


func test_terrain_progress_clamped() -> void:
	assert_eq(WorldLoadingFlow.terrain_progress(0, 9), 0.0)
	assert_eq(WorldLoadingFlow.terrain_progress(9, 9), 1.0)
	assert_eq(WorldLoadingFlow.terrain_progress(12, 9), 1.0, "超出圈总数钳制到 1")
	assert_eq(WorldLoadingFlow.terrain_progress(4, 9), 4.0 / 9.0)
	assert_eq(WorldLoadingFlow.terrain_progress(3, 0), 0.0, "total<=0 返回 0 防除零")