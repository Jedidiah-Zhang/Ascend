"""连接状态分区 — 自取型，数据来自 Connection 单例。
"""

class_name ConnectionSection
extends "res://scripts/ui/debug_section.gd"


func _init() -> void:
	label = "连接"


func get_lines() -> PackedStringArray:
	var status_str: String
	match Connection.status:
		Connection.Status.CONNECTED:
			status_str = "已连接"
		Connection.Status.CONNECTING:
			status_str = "连接中..."
		_:
			status_str = "未连接"
	return PackedStringArray([
		"%s  (127.0.0.1:9081)" % status_str,
	])
