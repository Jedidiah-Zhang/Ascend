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
## 固定面板宽度（避免每帧 get_string_size 测量所有行）
const PANEL_WIDTH: float = 280.0

## 最低刷新间隔（秒），限制高频率事件涌入时的绘制开销
const REFRESH_INTERVAL: float = 0.5


# ── 属性 ────────────────────────────────────────────────────

var _lines: PackedStringArray = PackedStringArray()
var _font: Font = null
var _pending_redraw: bool = false
var _refresh_accum: float = 0.0


# ── 生命周期 ────────────────────────────────────────────────

func _ready() -> void:
	anchor_left = 0.0
	anchor_top = 0.0
	anchor_right = 1.0
	anchor_bottom = 1.0
	mouse_filter = Control.MOUSE_FILTER_IGNORE
	_font = _get_mono_font()
	hide()


func _process(delta: float) -> void:
	if not _pending_redraw:
		return
	_refresh_accum += delta
	if _refresh_accum >= REFRESH_INTERVAL:
		_refresh_accum = 0.0
		_pending_redraw = false
		queue_redraw()


func _draw() -> void:
	if _font == null or _lines.is_empty():
		return

	var header_text := "── 事件日志 ──"
	var bg_x: float = size.x - PANEL_WIDTH
	var bg_h: float = (_lines.size() + 1) * LINE_HEIGHT + PADDING * 2

	draw_rect(Rect2(Vector2(bg_x, 0.0), Vector2(PANEL_WIDTH, bg_h)), BG_COLOR)

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
	_pending_redraw = true


# ── 内部实现 ────────────────────────────────────────────────

func _get_mono_font() -> Font:
	var project_theme: Theme = ThemeDB.get_project_theme()
	if project_theme and project_theme.default_font:
		return project_theme.default_font
	return get_theme_default_font()
