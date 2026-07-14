"""事件日志面板 — 右侧实时事件流，最近 20 条。

数据由 MainWorld 每收到一个事件时 push_event() 推送。
显示在屏幕右侧，与左侧 DebugOverlay 对称。
"""

extends Control

class_name EventLog


# ── 常量 ────────────────────────────────────────────────────

const BG_COLOR: Color = Color(0.0, 0.0, 0.0, 0.55)
const HEADER_COLOR: Color = Color(0.55, 1.0, 0.55)
const TEXT_COLOR: Color = Color(0.85, 0.85, 0.90)
const FONT_SIZE: int = 12
const LINE_HEIGHT: int = 15
const PADDING: int = 8


# ── 属性 ────────────────────────────────────────────────────

var _lines: PackedStringArray = PackedStringArray()
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


func _draw() -> void:
	if _font == null or _lines.is_empty():
		return

	# 计算最大行宽
	var max_w: float = 0.0
	for line in _lines:
		max_w = max(max_w, _font.get_string_size(line,
				HORIZONTAL_ALIGNMENT_LEFT, -1, FONT_SIZE).x)
	var header_text := "── 事件日志 ──"
	max_w = max(max_w, _font.get_string_size(header_text,
			HORIZONTAL_ALIGNMENT_LEFT, -1, FONT_SIZE).x)

	var bg_w: float = max_w + PADDING * 2
	var bg_h: float = (_lines.size() + 1) * LINE_HEIGHT + PADDING * 2
	var bg_x: float = size.x - bg_w

	draw_rect(Rect2(Vector2(bg_x, 0.0), Vector2(bg_w, bg_h)), BG_COLOR)

	var y: float = PADDING
	var x: float = bg_x + PADDING
	draw_string(_font, Vector2(x, y + FONT_SIZE), header_text,
			HORIZONTAL_ALIGNMENT_LEFT, -1, FONT_SIZE, HEADER_COLOR)
	y += LINE_HEIGHT
	for line in _lines:
		draw_string(_font, Vector2(x, y + FONT_SIZE), line,
				HORIZONTAL_ALIGNMENT_LEFT, -1, FONT_SIZE, TEXT_COLOR)
		y += LINE_HEIGHT


# ── 公共接口 ────────────────────────────────────────────────

func push_event(line: String) -> void:
	"""推入一条事件行到日志顶部。

	Args:
		line: 已格式化的单行事件文本。
	"""
	var arr: Array[String] = []
	arr.append(line)
	arr.append_array(_lines)
	_lines = arr
	if _lines.size() > 20:
		_lines.resize(20)
	queue_redraw()


# ── 内部实现 ────────────────────────────────────────────────

func _get_mono_font() -> Font:
	var project_theme: Theme = ThemeDB.get_project_theme()
	if project_theme and project_theme.default_font:
		return project_theme.default_font
	return get_theme_default_font()
