"""状态追赶分区 — 显示值追赶（StateDisplayChaser）调试信息。

展示当前抽样密度倍率（初雪/暴雪加速期 > 1）、加速剩余时长与
显示值收敛中的 chunk 数——验证"快下快铺"事件加速是否生效。
"""

class_name StateSection
extends "res://scripts/ui/debug_section.gd"


var _world: Node = null

var boost_mult: float = 1.0
var boost_remaining: float = 0.0
var chase_chunks: int = 0


## 构造函数：设置分区标签翻译键。
func _init() -> void:
	label_key = "debug.section.state"


## 缓存世界脚本引用，供 process_section 拉取追赶统计。
##
## Args:
##     world: 世界脚本节点（MainWorld2D）。
func setup(world: Node) -> void:
	_world = world


## 每帧从世界脚本 get_debug_state_chase_info 拉取追赶统计。
##
## Args:
##     _delta: 帧间隔（秒），本分区不使用。
func process_section(_delta: float) -> void:
	if _world == null or not _world.has_method("get_debug_state_chase_info"):
		return
	var info: Dictionary = _world.get_debug_state_chase_info()
	boost_mult = info.get("boost_mult", 1.0)
	boost_remaining = info.get("boost_remaining", 0.0)
	chase_chunks = info.get("chunks", 0)


## 生成状态追赶文本行：加速状态（含剩余秒数）与收敛中 chunk 数。
##
## Returns:
##     两行 PackedStringArray。
func get_lines() -> PackedStringArray:
	var boost_line: String
	if boost_mult > 1.0:
		boost_line = TranslationServer.tr("debug.state_boost").format({
			"mult": "%.1f" % boost_mult, "seconds": "%.0f" % boost_remaining})
	else:
		boost_line = TranslationServer.tr("debug.state_boost_idle")
	return PackedStringArray([
		boost_line,
		TranslationServer.tr("debug.state_chasing").format({"count": chase_chunks}),
	])