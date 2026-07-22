"""调试信息分区基类 — 所有调试分区继承自此 RefCounted 类。

每个分区提供统一的 get_lines() 接口，由 DebugOverlay 统一渲染。
分区自行管理数据拉取、轮询计时器、tile 变化检测，与具体世界脚本解耦。

生命周期:
  1. 构造 → DebugOverlay.add_section()
  2. setup(world) — 世界初始化时调用，缓存 world 引用
  3. process_section(delta) — 每帧（仅 overlay 可见时）自行拉取数据
  4. on_world_event(event_type, payload) — 后端事件广播
  5. on_world_response(request_type, payload) — 后端响应广播

世界脚本通过 get_debug_*() 系列方法提供数据，section 自行按需调用。
"""

class_name DebugSection
extends RefCounted


# ── 属性 ────────────────────────────────────────────────────

## 分区标签，显示为彩色标题行
var label: String = ""

## 是否启用，设为 false 时 DebugOverlay 跳过该分区
var enabled: bool = true


# ── 生命周期 ────────────────────────────────────────────────

func setup(_world: Node) -> void:
	"""世界初始化时调用一次，子类可缓存 world 引用或 NodePath。

	Args:
		_world: 世界脚本节点（MainWorld 或 MainWorld3D）。
	"""
	pass


func process_section(_delta: float) -> void:
	"""每帧（仅 overlay 可见时）由 DebugOverlay 调用，子类自行拉取数据。

	Args:
		_delta: 帧间隔（秒）。
	"""
	pass


func on_world_event(_event_type: String, _payload: Dictionary) -> void:
	"""后端推送事件时广播，子类自行判断是否处理。

	Args:
		_event_type: 事件类型（如 "minute_change"）。
		_payload: 完整事件载荷（含 payload.data）。
	"""
	pass


func on_world_response(_request_type: String, _payload: Dictionary) -> void:
	"""后端响应到达时广播，子类自行判断是否处理。

	Args:
		_request_type: 请求类型（如 "get_weather"）。
		_payload: 响应载荷。
	"""
	pass


# ── 公共接口 ────────────────────────────────────────────────

func get_lines() -> PackedStringArray:
	"""返回要渲染的文本行列表。子类必须覆写此方法。"""
	return PackedStringArray()


func update_from_backend(_data: Dictionary) -> void:
	"""接收后端推送数据。子类按需覆写。"""
	pass
