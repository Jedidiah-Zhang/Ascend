"""调试信息覆盖层 — 类似 Minecraft F3 的半透明调试面板。

渲染所有已注册 DebugSection 的文本行，显示在屏幕左上角。
F3 键切换可见性（自管理，无需世界脚本介入）。
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


# ── 生命周期 ────────────────────────────────────────────────

func _ready() -> void:
	anchor_left = 0.0
	anchor_top = 0.0
	anchor_right = 1.0
	anchor_bottom = 1.0
	mouse_filter = Control.MOUSE_FILTER_IGNORE
	_font = FontUtils.get_mono_font()
	hide()


func _input(event: InputEvent) -> void:
	if event is InputEventKey and event.pressed and not event.echo:
		if event.keycode == KEY_F3:
			toggle()
			get_viewport().set_input_as_handled()


func _process(delta: float) -> void:
	if not _shown:
		return
	_refresh_accum += delta
	if _refresh_accum >= REFRESH_INTERVAL:
		_refresh_accum = 0.0
		queue_redraw()


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

func add_section(section: DebugSection) -> void:
	_sections.append(section)


func remove_section(label: String) -> void:
	_sections = _sections.filter(func(s: DebugSection): return s.label != label)


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


func setup_sections(world: Node) -> void:
	for section: DebugSection in _sections:
		section.setup(world)


func process_sections(delta: float) -> void:
	for section: DebugSection in _sections:
		if section.enabled:
			section.process_section(delta)


func broadcast_event(event_type: String, payload: Dictionary) -> void:
	for section: DebugSection in _sections:
		if section.enabled:
			section.on_world_event(event_type, payload)


func broadcast_response(request_type: String, payload: Dictionary) -> void:
	for section: DebugSection in _sections:
		if section.enabled:
			section.on_world_response(request_type, payload)


func toggle() -> void:
	_shown = not _shown
	if _shown:
		show()
	else:
		hide()
	toggled.emit(_shown)


func is_shown() -> bool:
	return _shown
