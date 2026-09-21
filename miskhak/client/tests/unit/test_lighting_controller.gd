"""LightingController 单元测试 — 昼夜判据（局部光源开关）。

CanvasModulate 绑定为 null 时 update 静默跳过；is_night 为纯逻辑判据，
无场景依赖，可独立测试。
"""

extends GutTest


func test_is_night_daytime() -> void:
	var lc := LightingController.new()
	assert_false(lc.is_night(12.0, 0, 6.0, 18.0), "正午应白天")


func test_is_night_before_sunrise() -> void:
	var lc := LightingController.new()
	assert_true(lc.is_night(5.0, 59, 6.0, 18.0), "日出前应夜晚")
	assert_true(lc.is_night(0.0, 0, 6.0, 18.0))


func test_is_night_after_sunset() -> void:
	var lc := LightingController.new()
	assert_true(lc.is_night(18.0, 0, 6.0, 18.0), "日落整点起应夜晚")
	assert_true(lc.is_night(23.0, 30, 6.0, 18.0))


func test_is_night_edge_sunrise_exclusive() -> void:
	"""日出时刻起算白天：6:00 整是白天（is_day 含左界）。"""
	var lc := LightingController.new()
	assert_false(lc.is_night(6.0, 0, 6.0, 18.0))


func test_is_night_minutes_are_considered() -> void:
	var lc := LightingController.new()
	assert_false(lc.is_night(5.0, 60, 6.0, 18.0), "5:60 = 6:00，应白天")
	assert_false(lc.is_night(17.0, 59, 6.0, 18.0), "17:59 未到日落，应白天")
	assert_true(lc.is_night(18.0, 1, 6.0, 18.0), "18:01 应夜晚")


func test_is_night_invalid_daylight_is_day() -> void:
	var lc := LightingController.new()
	assert_false(lc.is_night(12.0, 0, 6.0, 6.0), "daylight<=0 保守白天不点灯")


func test_update_without_modulate_skips() -> void:
	var lc := LightingController.new()
	lc.update(12.0, 0, 6.0, 18.0, 1.0)  # 未绑定 CanvasModulate → 静默跳过
	assert_eq(lc.last_sun_altitude(), 0.5, "未绑定时不应更新状态")
