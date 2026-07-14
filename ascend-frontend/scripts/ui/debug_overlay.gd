"""调试信息覆盖层 — 类似 Minecraft F3 的半透明调试面板。

渲染所有已注册 DebugSection 的文本行，显示在屏幕左上角。
F3 键切换可见性，数据由 MainWorld 每帧推送。

用法:
    var overlay := DebugOverlay.new()
    overlay.add_section(FPSSection.new())
    overlay.add_section(MemorySection.new())
    add_child(overlay)
    overlay.toggle()
"""

extends Control

class_name DebugOverlay


# ── 常量 ────────────────────────────────────────────────────

const BG_COLOR: Color = Color(0.0, 0.0, 0.0, 0.65)
const LABEL_COLOR: Color = Color(0.55, 1.0, 0.55)
const TEXT_COLOR: Color = Color(0.92, 0.92, 0.96)
const FONT_SIZE: int = 13
const LINE_HEIGHT: int = 16
const PADDING: int = 8
const LABEL_INDENT: int = 4
const SECTION_SPACING: int = 2


# ── 属性 ────────────────────────────────────────────────────

var _sections: Array[DebugSection] = []
var _shown: bool = false
var _font: Font = null


# ── 生命周期 ────────────────────────────────────────────────

func _ready() -> void:
	anchor_left = 0.0
	anchor_top = 0.0
	anchor_right = 1.0
	anchor_bottom = 1.0
	mouse_filter = Control.MOUSE_FILTER_IGNORE
	_font = _get_mono_font()
	hide()


func _process(_delta: float) -> void:
	if _shown:
		queue_redraw()


func _draw() -> void:
	if not _shown or _font == null:
		return

	var y: float = PADDING
	var max_w: float = 0.0

	for section: DebugSection in _sections:
		if not section.enabled:
			continue
		var lines: PackedStringArray = section.get_lines()
		if lines.is_empty():
			continue
		max_w = max(max_w, _font.get_string_size(section.label,
				HORIZONTAL_ALIGNMENT_LEFT, -1, FONT_SIZE).x + LABEL_INDENT)
		y += LINE_HEIGHT
		for line in lines:
			max_w = max(max_w, _font.get_string_size(line,
					HORIZONTAL_ALIGNMENT_LEFT, -1, FONT_SIZE).x + LABEL_INDENT * 2)
			y += LINE_HEIGHT
		y += SECTION_SPACING

	if y <= PADDING:
		return

	var bg_w: float = max_w + PADDING * 2
	var bg_h: float = y + PADDING - SECTION_SPACING
	draw_rect(Rect2(Vector2.ZERO, Vector2(bg_w, bg_h)), BG_COLOR)

	y = PADDING
	for section: DebugSection in _sections:
		if not section.enabled:
			continue
		var lines: PackedStringArray = section.get_lines()
		if lines.is_empty():
			continue

		draw_string(_font, Vector2(PADDING + LABEL_INDENT, y + FONT_SIZE),
				section.label, HORIZONTAL_ALIGNMENT_LEFT, -1, FONT_SIZE, LABEL_COLOR)
		y += LINE_HEIGHT
		for line in lines:
			draw_string(_font, Vector2(PADDING + LABEL_INDENT * 2, y + FONT_SIZE),
					line, HORIZONTAL_ALIGNMENT_LEFT, -1, FONT_SIZE, TEXT_COLOR)
			y += LINE_HEIGHT
		y += SECTION_SPACING


# ── 公共接口 ────────────────────────────────────────────────

func add_section(section: DebugSection) -> void:
	"""注册一个调试分区。

	Args:
		section: DebugSection 子类实例。
	"""
	_sections.append(section)


func remove_section(label: String) -> void:
	"""按标签移除调试分区。

	Args:
		label: 分区的 label 值。
	"""
	_sections = _sections.filter(func(s: DebugSection): return s.label != label)


func get_section(label: String) -> DebugSection:
	"""按标签查找调试分区。

	Args:
		label: 分区的 label 值。

	Returns:
		匹配的分区实例，未找到返回 null。
	"""
	for s: DebugSection in _sections:
		if s.label == label:
			return s
	return null


func toggle() -> void:
	"""切换调试覆盖层的可见性。"""
	_shown = not _shown
	if _shown:
		show()
	else:
		hide()


func is_shown() -> bool:
	"""调试覆盖层是否可见。"""
	return _shown


# ── 内部实现 ────────────────────────────────────────────────

func _get_mono_font() -> Font:
	var project_theme: Theme = ThemeDB.get_project_theme()
	if project_theme and project_theme.default_font:
		return project_theme.default_font
	return get_theme_default_font()
