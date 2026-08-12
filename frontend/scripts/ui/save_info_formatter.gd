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


static func hhmm_string(hour: int, minute: int) -> String:
	"""游戏小时/分钟 → "HH:MM"（事件时间戳等共用格式化）。"""
	return "%02d:%02d" % [hour, minute]


static func game_time_string(ticks: int) -> String:
	"""tick 数 → "第 N 天 HH:MM"。

	天从 1 开始（与后端日历一致）；刻度取整到游戏分钟。
	"""
	if ticks < 0:
		ticks = 0
	# floori(浮点除法) 显式取整，避免 INTEGER_DIVISION 警告（GDScript 无 // 运算符）
	var day: int = floori(ticks / float(Config.GAME_DAY)) + 1
	var day_ticks: int = ticks % Config.GAME_DAY
	var hour: int = floori(day_ticks / float(Config.GAME_HOUR))
	var minute: int = floori((day_ticks % Config.GAME_HOUR) / float(Config.GAME_MINUTE))
	return TranslationServer.tr("ui.format.game_time").format({
		"day": day, "clock": hhmm_string(hour, minute),
	})


static func duration_string(total_sec: float) -> String:
	"""真实秒数 → 人类可读时长。"""
	if total_sec < 0.0:
		total_sec = 0.0
	var sec: int = int(total_sec)
	if sec < 60:
		return TranslationServer.tr("ui.format.seconds").format({"n": sec})
	var minutes: int = floori(sec / 60.0)
	if minutes < 60:
		return TranslationServer.tr("ui.format.minutes").format({"n": minutes})
	var hours: int = floori(minutes / 60.0)
	var mins: int = minutes % 60
	if mins == 0:
		return TranslationServer.tr("ui.format.hours").format({"n": hours})
	return TranslationServer.tr("ui.format.hours_minutes").format({
		"n": hours, "m": mins,
	})


static func datetime_string(unix_sec: float) -> String:
	"""Unix 秒 → "YYYY-MM-DD HH:MM"（本地时区）。"""
	if unix_sec <= 0.0:
		return "—"
	var dt := Time.get_datetime_dict_from_unix_time(int(unix_sec))
	return "%04d-%02d-%02d %02d:%02d" % [
		dt["year"], dt["month"], dt["day"], dt["hour"], dt["minute"],
	]


static func seed_string(world_seed: int) -> String:
	"""种子展示（0 = 随机）。"""
	if world_seed == 0:
		return TranslationServer.tr("ui.common.random")
	return str(world_seed)
