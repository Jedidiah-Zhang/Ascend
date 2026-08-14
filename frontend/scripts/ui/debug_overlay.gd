"""调试信息覆盖层 — 类似 Minecraft F3 的半透明调试面板。

渲染所有已注册 DebugSection 的文本行，显示在屏幕左上角。
F3 键切换可见性（自管理，无需世界脚本介入；调试模式关闭时忽略）。
每个 Section 自行管理数据拉取与轮询，
DebugOverlay 仅负责统一调度（process_sections / broadcast_event / broadcast_response）。

用法:
	var overlay := get_node("DebugLayer/DebugOverlay")
    overlay.setup_default_sections(self)  # 世界脚本一行搞定
    overlay.process_sections(delta)       # 每帧调用
    overlay.broadcast_event(...)          # 后端事件到达时调用
"""

extends Control

class_name DebugOverlay


# ── 信号 ────────────────────────────────────────────────────

## F3 切换时发出，供 EventLog 等面板联动
signal toggled(shown: bool)


# ── 常量 ────────────────────────────────────────────────────

const BG_COLOR: Color = Color(0.0, 0.0, 0.0, 0.65)
const LABEL_COLOR: Color = Color(0.55, 1.0, 0.55)
const TEXT_COLOR: Color = Color(0.92, 0.92, 0.96)
const FONT_SIZE: int = 13
const LINE_HEIGHT: int = 16
const PADDING: int = 8
const LABEL_INDENT: int = 4
const SECTION_SPACING: int = 2

## 最低刷新间隔（秒），限制 DebugOverlay 重绘频率避免每帧全量测量
const REFRESH_INTERVAL: float = 0.25


# ── 属性 ────────────────────────────────────────────────────

var _sections: Array[DebugSection] = []
var _shown: bool = false
var _font: Font = null
var _refresh_accum: float = 0.0

## 测试注入的设置门面（Variant：settings.gd 无 class_name，动态派发）
var _settings_override = null


# ── 生命周期 ────────────────────────────────────────────────

## 初始化覆盖层：锚点铺满全屏、忽略鼠标事件、加载等宽字体并默认隐藏。
func _ready() -> void:
	anchor_left = 0.0
	anchor_top = 0.0
	anchor_right = 1.0
	anchor_bottom = 1.0
	mouse_filter = Control.MOUSE_FILTER_IGNORE
	_font = FontUtils.get_mono_font()
	hide()
	if not _settings().debug_mode_changed.is_connected(_on_debug_mode_changed):
		_settings().debug_mode_changed.connect(_on_debug_mode_changed)


## 捕获 F3 键切换覆盖层可见性，并消费该输入事件避免继续传播。
## 调试模式关闭时忽略 F3（不消费事件）。
##
## Args:
##     event: 输入事件，仅响应按键按下且非重复回显的 F3。
func _input(event: InputEvent) -> void:
	if event is InputEventKey and event.pressed and not event.echo:
		if event.keycode == KEY_F3 and _settings().get_debug_mode():
			toggle()
			get_viewport().set_input_as_handled()


## 按 REFRESH_INTERVAL 节流重绘：仅覆盖层可见时累计帧间隔，达到刷新间隔才请求重绘。
##
## Args:
##     delta: 帧间隔（秒）。
func _process(delta: float) -> void:
	if not _shown:
		return
	_refresh_accum += delta
	if _refresh_accum >= REFRESH_INTERVAL:
		_refresh_accum = 0.0
		queue_redraw()


## 绘制调试面板：先测量各分区标签与文本行宽度计算背景尺寸，
## 再绘制半透明背景、分区标签（绿色）与文本行（白色）。
func _draw() -> void:
	if not _shown or _font == null:
		return

	var sections_data: Array[Dictionary] = []
	for section: DebugSection in _sections:
		if not section.enabled:
			continue
		var lines: PackedStringArray = section.get_lines()
		if lines.is_empty():
			continue
		sections_data.append({"label": section.label, "lines": lines})

	if sections_data.is_empty():
		return

	var y: float = PADDING
	var max_w: float = 0.0
	for data in sections_data:
		max_w = max(max_w, _font.get_string_size(data["label"],
				HORIZONTAL_ALIGNMENT_LEFT, -1, FONT_SIZE).x + LABEL_INDENT)
		y += LINE_HEIGHT
		for line: String in data["lines"]:
			max_w = max(max_w, _font.get_string_size(line,
					HORIZONTAL_ALIGNMENT_LEFT, -1, FONT_SIZE).x + LABEL_INDENT * 2)
			y += LINE_HEIGHT
		y += SECTION_SPACING

	var bg_w: float = max_w + PADDING * 2
	var bg_h: float = y + PADDING - SECTION_SPACING
	draw_rect(Rect2(Vector2.ZERO, Vector2(bg_w, bg_h)), BG_COLOR)

	y = PADDING
	for data in sections_data:
		draw_string(_font, Vector2(PADDING + LABEL_INDENT, y + FONT_SIZE),
				data["label"], HORIZONTAL_ALIGNMENT_LEFT, -1, FONT_SIZE, LABEL_COLOR)
		y += LINE_HEIGHT
		for line: String in data["lines"]:
			draw_string(_font, Vector2(PADDING + LABEL_INDENT * 2, y + FONT_SIZE),
					line, HORIZONTAL_ALIGNMENT_LEFT, -1, FONT_SIZE, TEXT_COLOR)
			y += LINE_HEIGHT
		y += SECTION_SPACING


# ── 公共接口 ────────────────────────────────────────────────

## 测试用：注入独立设置门面（须在 add_child 前调用，_ready 取其信号）。
func set_settings_override(override) -> void:
	_settings_override = override


func _settings():
	return _settings_override if _settings_override != null else Settings


## 调试模式关闭时隐藏已打开的信息面板（toggled 信号联动 EventLog 隐藏）。
func _on_debug_mode_changed(enabled: bool) -> void:
	if not enabled and _shown:
		toggle()


## 注册调试分区，加入统一渲染列表。
##
## Args:
##     section: 要注册的 DebugSection 实例。
func add_section(section: DebugSection) -> void:
	_sections.append(section)


## 按标签从渲染列表移除分区。
##
## Args:
##     label: 要移除的分区标签。
func remove_section(label: String) -> void:
	_sections = _sections.filter(func(s: DebugSection): return s.label != label)


## 按标签查找已注册分区。
##
## Args:
##     label: 分区标签。
##
## Returns:
##     匹配的分区实例；未找到返回 null。
func get_section(label: String) -> DebugSection:
	for s: DebugSection in _sections:
		if s.label == label:
			return s
	return null


func setup_default_sections(world: Node) -> void:
	"""创建所有默认调试分区并注入世界脚本引用。

	Args:
		world: 世界脚本节点（MainWorld 或 MainWorld3D）。
	"""
	add_section(FPSSection.new())
	add_section(MemorySection.new())
	add_section(TimeSection.new())
	add_section(CameraSection.new())
	add_section(PlayerSection.new())
	add_section(ClimateSection.new())
	add_section(WeatherSection.new())
	add_section(ChunkSection.new())
	add_section(ElevationSection.new())
	setup_sections(world)


## 向所有分区注入世界脚本引用（调用各分区的 setup）。
##
## Args:
##     world: 世界脚本节点（MainWorld 或 MainWorld3D）。
func setup_sections(world: Node) -> void:
	for section: DebugSection in _sections:
		section.setup(world)


## 统一调度所有启用分区的每帧处理（覆盖层可见时由世界脚本每帧调用）。
##
## Args:
##     delta: 帧间隔（秒）。
func process_sections(delta: float) -> void:
	for section: DebugSection in _sections:
		if section.enabled:
			section.process_section(delta)


## 将后端事件广播给所有启用分区（调用各分区 on_world_event）。
##
## Args:
##     event_type: 事件类型（如 "minute_change"）。
##     payload: 完整事件载荷（含 payload.data）。
func broadcast_event(event_type: String, payload: Dictionary) -> void:
	for section: DebugSection in _sections:
		if section.enabled:
			section.on_world_event(event_type, payload)


## 将后端响应广播给所有启用分区（调用各分区 on_world_response）。
##
## Args:
##     request_type: 请求类型（如 "get_weather"）。
##     payload: 响应载荷。
func broadcast_response(request_type: String, payload: Dictionary) -> void:
	for section: DebugSection in _sections:
		if section.enabled:
			section.on_world_response(request_type, payload)


## 切换覆盖层显示状态，同步 show/hide 并发出 toggled 信号供 EventLog 等面板联动。
func toggle() -> void:
	_shown = not _shown
	if _shown:
		show()
	else:
		hide()
	toggled.emit(_shown)


## 覆盖层当前是否可见。
##
## Returns:
##     true 表示正在显示。
func is_shown() -> bool:
	return _shown
