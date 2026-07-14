"""调试信息分区基类 — 所有调试分区继承自此 RefCounted 类。

每个分区提供统一的 get_lines() 接口，由 DebugOverlay 统一渲染。
数据更新方式：
  - 自取型：在 get_lines() 中直接从 Engine/Performance 读取
  - 推送型：外部调用 setter 方法更新属性
  - 事件型：外部调用 update_from_backend() 注入后端数据
"""

class_name DebugSection
extends RefCounted


# ── 属性 ────────────────────────────────────────────────────

## 分区标签，显示为彩色标题行
var label: String = ""

## 是否启用，设为 false 时 DebugOverlay 跳过该分区
var enabled: bool = true


# ── 公共接口 ────────────────────────────────────────────────

func get_lines() -> PackedStringArray:
	"""返回要渲染的文本行列表。子类必须覆写此方法。"""
	return PackedStringArray()


func update_from_backend(_data: Dictionary) -> void:
	"""接收后端推送数据。子类按需覆写。"""
	pass
