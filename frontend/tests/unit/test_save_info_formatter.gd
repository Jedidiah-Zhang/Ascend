"""SaveInfoFormatter 格式化单元测试 — 游戏时间/时长/日期。

覆盖 scripts/ui/save_info_formatter.gd。
"""

extends GutTest

const Config = preload("res://scripts/config.gd")


# ── 游戏内时间 ────────────────────────────────────────────

func test_game_time_zero() -> void:
	assert_eq(SaveInfoFormatter.game_time_string(0), "第 1 天 00:00")


func test_game_time_first_day() -> void:
	# 06:00 = 6 * GAME_HOUR
	var ticks: int = 6 * Config.GAME_HOUR
	assert_eq(SaveInfoFormatter.game_time_string(ticks), "第 1 天 06:00")


func test_game_time_second_day() -> void:
	var ticks: int = Config.GAME_DAY + 2 * Config.GAME_HOUR + Config.GAME_HOUR / 2
	assert_eq(SaveInfoFormatter.game_time_string(ticks), "第 2 天 02:30")


func test_game_time_minute_rounding() -> void:
	# 6 小时 5 分钟
	var ticks: int = 6 * Config.GAME_HOUR + 5 * (Config.GAME_HOUR / 60)
	assert_eq(SaveInfoFormatter.game_time_string(ticks), "第 1 天 06:05")


func test_game_time_negative_clamped() -> void:
	assert_eq(SaveInfoFormatter.game_time_string(-100), "第 1 天 00:00")


# ── 运行时长 ──────────────────────────────────────────────

func test_duration_seconds() -> void:
	assert_eq(SaveInfoFormatter.duration_string(30.0), "30 秒")


func test_duration_minutes() -> void:
	assert_eq(SaveInfoFormatter.duration_string(45 * 60), "45 分钟")


func test_duration_hours() -> void:
	assert_eq(SaveInfoFormatter.duration_string(2 * 3600), "2 小时")


func test_duration_hours_minutes() -> void:
	assert_eq(SaveInfoFormatter.duration_string(2 * 3600 + 5 * 60), "2 小时 5 分")


func test_duration_negative_clamped() -> void:
	assert_eq(SaveInfoFormatter.duration_string(-5.0), "0 秒")


func test_duration_zero() -> void:
	assert_eq(SaveInfoFormatter.duration_string(0.0), "0 秒")


# ── 日期 ──────────────────────────────────────────────────

func test_datetime_zero_is_placeholder() -> void:
	assert_eq(SaveInfoFormatter.datetime_string(0.0), "—")
	assert_eq(SaveInfoFormatter.datetime_string(-1.0), "—")


func test_datetime_format() -> void:
	var s: String = SaveInfoFormatter.datetime_string(1754000000)
	assert_match(s, "????-??-?? ??:??")


# ── 种子 ──────────────────────────────────────────────────

func test_seed_string_random() -> void:
	assert_eq(SaveInfoFormatter.seed_string(0), "随机")


func test_seed_string_value() -> void:
	assert_eq(SaveInfoFormatter.seed_string(12345), "12345")
