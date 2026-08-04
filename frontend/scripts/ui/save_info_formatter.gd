"""存档信息格式化 — 游戏时间/时长/日期的展示文本（纯逻辑）。

供存档选择页（Issue #14）展示：
  - 游戏内时间: "第 3 天 06:12"（tick → 天/时/分）
  - 运行时长:   "2 小时 5 分" / "45 分钟" / "30 秒"
  - 最后游玩:   "2026-08-04 14:32"

时间换算常量来自 scripts/config.gd（与后端同步）。
"""

class_name SaveInfoFormatter
extends RefCounted

const Config = preload("res://scripts/config.gd")


static func game_time_string(ticks: int) -> String:
	"""tick 数 → "第 N 天 HH:MM"。

	天从 1 开始（与后端日历一致）；刻度取整到游戏分钟。
	"""
	if ticks < 0:
		ticks = 0
	var day: int = ticks / Config.GAME_DAY + 1
	var day_ticks: int = ticks % Config.GAME_DAY
	var hour: int = day_ticks / Config.GAME_HOUR
	var minute: int = (day_ticks % Config.GAME_HOUR) / (Config.GAME_HOUR / 60)
	return "第 %d 天 %02d:%02d" % [day, hour, minute]


static func duration_string(total_sec: float) -> String:
	"""真实秒数 → 人类可读时长。"""
	if total_sec < 0.0:
		total_sec = 0.0
	var sec: int = int(total_sec)
	if sec < 60:
		return "%d 秒" % sec
	var minutes: int = sec / 60
	if minutes < 60:
		return "%d 分钟" % minutes
	var hours: int = minutes / 60
	var mins: int = minutes % 60
	if mins == 0:
		return "%d 小时" % hours
	return "%d 小时 %d 分" % [hours, mins]


static func datetime_string(unix_sec: float) -> String:
	"""Unix 秒 → "YYYY-MM-DD HH:MM"（本地时区）。"""
	if unix_sec <= 0.0:
		return "—"
	return Time.get_datetime_string_from_unix_time(int(unix_sec), false)


static func seed_string(seed: int) -> String:
	"""种子展示（0 = 随机）。"""
	if seed == 0:
		return "随机"
	return str(seed)
