"""调试终端 UI 组件 — Godot 前端内置半透明控制台。

支持本地指令（clear/help/map）和远程指令转发。
所有内容通过 _draw() 渲染，无 RichTextLabel 等子节点。

用法:
    var term = TerminalWidget.new()
    term.remote_command.connect(_on_terminal_command)
    add_child(term)
    term.open()
"""

extends Control

class_name TerminalWidget

# ── 样式常量 ────────────────────────────────────────────────

const BG_COLOR: Color = Color(0.05, 0.05, 0.10, 0.75)
const TEXT_COLOR: Color = Color(0.90, 0.90, 0.95)
const PROMPT_COLOR: Color = Color(0.60, 0.90, 0.60)
const INFO_COLOR: Color = Color(0.60, 0.80, 0.60)   # 提示信息颜色（绿色调）
const CURSOR_COLOR: Color = Color(0.80, 0.85, 0.95)
const FONT_SIZE: int = 15
const LINE_HEIGHT: int = 20
const PADDING: int = 10
const BOTTOM_INPUT_HEIGHT: int = 30

const OUTPUT_LINE_LIMIT: int = 500
const HISTORY_LIMIT: int = 100
const PROMPT_STR: String = "$ "

## 光标闪烁周期（秒）
const CURSOR_BLINK_INTERVAL: float = 0.5

# ── 状态 ────────────────────────────────────────────────────

## 输出历史行（纯文本，不含颜色标签）
var _output_lines: PackedStringArray = PackedStringArray()
## 滚动偏移（行数，0=最新）
var _scroll_offset: int = 0

## 当前输入文本
var _input_text: String = ""
## 光标位置（字符索引）
var _cursor_pos: int = 0

## 指令历史
var _history: PackedStringArray = PackedStringArray()
## 历史浏览索引（-1 = 新输入）
var _history_idx: int = -1

## 终端是否打开
var _is_open: bool = false
## 光标可见性
var _cursor_visible: bool = true
## 光标闪烁计时
var _blink_timer: float = 0.0
## 字体引用
var _font: Font = null
## 字体高度缓存
var _font_height: int = FONT_SIZE

## 向后端发送远程指令
signal remote_command(command: String)


# ── 生命周期 ────────────────────────────────────────────────


func _ready() -> void:
	"""初始化终端组件：设置锚点、字体、初始输出。"""
	anchor_left = 0.0
	anchor_top = 0.0
	anchor_right = 1.0
	anchor_bottom = 1.0
	mouse_filter = Control.MOUSE_FILTER_IGNORE
	# 显式设置大小为视口大小（兜底锚点失效）
	var vp := get_viewport()
	if vp:
		set_deferred("size", vp.get_visible_rect().size)
	_font = _get_mono_font()
	_font_height = FONT_SIZE + 2
	hide()
	_write_output("Ascend 调试终端")
	_write_output("输入 help 查看指令列表，/? 切换终端")


# ── 公开接口 ────────────────────────────────────────────────


func open() -> void:
	"""打开终端，占据全屏并捕获输入焦点。"""
	_is_open = true
	_blink_timer = 0.0
	_cursor_visible = true
	_scroll_offset = 0
	show()
	queue_redraw()


func close() -> void:
	"""关闭终端并释放输入。"""
	_is_open = false
	hide()


func toggle() -> void:
	"""切换终端开关状态。"""
	if _is_open:
		close()
	else:
		open()


func is_open() -> bool:
	"""终端是否已打开。

	Returns:
		True 表示终端面板可见。
	"""
	return _is_open


func write(text: String) -> void:
	"""向终端输出文本（来自后端响应）。

	Args:
		text: 要追加的输出文本。
	"""
	if text.is_empty():
		return
	_write_output(text)


# ── 输入处理 ────────────────────────────────────────────────


func _input(event: InputEvent) -> void:
	"""当终端打开时捕获所有键盘事件。

	Args:
		event: 输入事件。
	"""
	if not _is_open:
		return

	if event is InputEventKey and event.pressed and not event.echo:
		var key: Key = event.keycode
		var ctrl: bool = event.ctrl_pressed

		# '/' 键关闭终端（单独的 / 键，非组合键）
		if key == Key.KEY_SLASH and not event.shift_pressed and not ctrl and not event.alt_pressed:
			close()
			get_viewport().set_input_as_handled()
			return

		# Enter
		if key == Key.KEY_ENTER or key == Key.KEY_KP_ENTER:
			_execute_input()
			get_viewport().set_input_as_handled()
			return

		# Backspace
		if key == Key.KEY_BACKSPACE:
			_do_backspace()
			get_viewport().set_input_as_handled()
			return

		# Delete
		if key == Key.KEY_DELETE:
			_do_delete()
			get_viewport().set_input_as_handled()
			return

		# Left
		if key == Key.KEY_LEFT:
			if _cursor_pos > 0:
				_cursor_pos -= 1
			queue_redraw()
			get_viewport().set_input_as_handled()
			return

		# Right
		if key == Key.KEY_RIGHT:
			if _cursor_pos < _input_text.length():
				_cursor_pos += 1
			queue_redraw()
			get_viewport().set_input_as_handled()
			return

		# Home
		if key == Key.KEY_HOME:
			_cursor_pos = 0
			queue_redraw()
			get_viewport().set_input_as_handled()
			return

		# End
		if key == Key.KEY_END:
			_cursor_pos = _input_text.length()
			queue_redraw()
			get_viewport().set_input_as_handled()
			return

		# Up (history back)
		if key == Key.KEY_UP:
			_history_up()
			get_viewport().set_input_as_handled()
			return

		# Down (history forward)
		if key == Key.KEY_DOWN:
			_history_down()
			get_viewport().set_input_as_handled()
			return

		# PageUp (scroll up)
		if key == Key.KEY_PAGEUP:
			_scroll_offset = min(_scroll_offset + 20, _visible_line_count())
			queue_redraw()
			get_viewport().set_input_as_handled()
			return

		# PageDown (scroll down)
		if key == Key.KEY_PAGEDOWN:
			_scroll_offset = max(0, _scroll_offset - 20)
			queue_redraw()
			get_viewport().set_input_as_handled()
			return

		# Printable characters (via unicode)
		if not ctrl and event.unicode > 31 and event.unicode < 0x10FFFF:
			_insert_char(char(event.unicode))
			get_viewport().set_input_as_handled()
			return


func _process(delta: float) -> void:
	"""每帧：更新光标闪烁。

	Args:
		delta: 帧间隔时间。
	"""
	if not _is_open:
		return

	_blink_timer += delta
	if _blink_timer >= CURSOR_BLINK_INTERVAL:
		_blink_timer = 0.0
		_cursor_visible = not _cursor_visible
		queue_redraw()


# ── 渲染 ────────────────────────────────────────────────────


func _draw() -> void:
	"""绘制终端背景和所有文本行。"""
	if not _is_open or _font == null:
		return

	# 背景
	draw_rect(Rect2(Vector2.ZERO, size), BG_COLOR)

	var usable_w: float = size.x - PADDING * 2
	var input_area_top: float = size.y - PADDING - BOTTOM_INPUT_HEIGHT

	# 输出行（从底部往上画）
	var draw_y: float = input_area_top - PADDING
	var visible_lines: int = _visible_line_count()
	var start_idx: int = max(0, _output_lines.size() - visible_lines - _scroll_offset)

	for i in range(_output_lines.size() - 1, start_idx - 1, -1):
		if i < 0 or i >= _output_lines.size():
			continue
		draw_y -= LINE_HEIGHT
		if draw_y < PADDING:
			break
		var line: String = _output_lines[i]
		draw_string(_font, Vector2(PADDING, draw_y + _font_height), line,
				HORIZONTAL_ALIGNMENT_LEFT, usable_w, FONT_SIZE, TEXT_COLOR)

	# 输入行（底部）
	var input_base_y: float = size.y - PADDING - _font_height
	draw_string(_font, Vector2(PADDING, input_base_y), PROMPT_STR + _input_text,
			HORIZONTAL_ALIGNMENT_LEFT, usable_w, FONT_SIZE, PROMPT_COLOR)

	# 光标（闪烁）
	if _cursor_visible:
		var cursor_x: float = PADDING + _font.get_string_size(PROMPT_STR + _input_text.left(_cursor_pos),
				HORIZONTAL_ALIGNMENT_LEFT, usable_w).x
		var cursor_rect := Rect2(cursor_x, input_base_y, 2, _font_height)
		draw_rect(cursor_rect, CURSOR_COLOR)


# ── 输入编辑 ────────────────────────────────────────────────


func _insert_char(ch: String) -> void:
	"""在光标位置插入字符。

	Args:
		ch: 要插入的单个字符。
	"""
	if _input_text.length() >= 200:
		return
	_input_text = _input_text.left(_cursor_pos) + ch + _input_text.substr(_cursor_pos)
	_cursor_pos += 1
	queue_redraw()


func _do_backspace() -> void:
	"""退格删除光标前的字符。"""
	if _cursor_pos <= 0:
		return
	_input_text = _input_text.left(_cursor_pos - 1) + _input_text.substr(_cursor_pos)
	_cursor_pos -= 1
	queue_redraw()


func _do_delete() -> void:
	"""删除光标处的字符。"""
	if _cursor_pos >= _input_text.length():
		return
	_input_text = _input_text.left(_cursor_pos) + _input_text.substr(_cursor_pos + 1)
	queue_redraw()


func _history_up() -> void:
	"""上翻历史。"""
	if _history.is_empty():
		return
	if _history_idx == -1:
		_history_idx = _history.size() - 1
	else:
		_history_idx = max(0, _history_idx - 1)

	_input_text = _history[_history_idx]
	_cursor_pos = _input_text.length()
	queue_redraw()


func _history_down() -> void:
	"""下翻历史。"""
	if _history_idx == -1:
		return

	if _history_idx >= _history.size() - 1:
		_history_idx = -1
		_input_text = ""
	else:
		_history_idx += 1
		_input_text = _history[_history_idx]

	_cursor_pos = _input_text.length()
	queue_redraw()


# ── 指令执行 ────────────────────────────────────────────────


func _execute_input() -> void:
	"""执行当前输入行。"""
	var input: String = _input_text.strip_edges()
	if input.is_empty():
		return

	# 加入历史
	_history.append(input)
	if _history.size() > HISTORY_LIMIT:
		_history = _history.slice(1)
	_history_idx = -1

	# 显示输入回显
	_write_output(PROMPT_STR + input)

	_input_text = ""
	_cursor_pos = 0
	_scroll_offset = 0

	# 判断是否为本地指令
	var cmd: String = input.get_slice(" ", 0).to_lower()

	if cmd == "clear":
		_output_lines.clear()
		queue_redraw()
	elif cmd == "help":
		_show_help()
	elif cmd == "map":
		_handle_local_map(input)
	else:
		# 远程指令
		remote_command.emit(input)


func _show_help() -> void:
	"""显示帮助信息。"""
	_write_output("--- " + tr("terminal.local_commands") + " ---")
	_write_output("  clear - " + tr("terminal.help_clear"))
	_write_output("  help - " + tr("terminal.help_help"))
	_write_output("  map <mode> - " + tr("terminal.help_map"))
	_write_output("--- " + tr("terminal.remote_commands") + " ---")
	_write_output("  " + tr("terminal.help_help"))


func _handle_local_map(input: String) -> void:
	_write_output("Map: tile-level terrain view active")


# ── 辅助函数 ────────────────────────────────────────────────


func _write_output(text: String) -> void:
	"""添加一行输出并触发重绘。

	Args:
		text: 输出文本（纯文本，不含颜色标签）。
	"""
	var lines: PackedStringArray = text.split("\n", false)
	for line in lines:
		if line.is_empty():
			continue
		_output_lines.append(line)

	if _output_lines.size() > OUTPUT_LINE_LIMIT:
		_output_lines = _output_lines.slice(_output_lines.size() - OUTPUT_LINE_LIMIT)

	queue_redraw()


func _visible_line_count() -> int:
	"""计算当前可见区域可容纳的输出行数。

	Returns:
		可见行数。
	"""
	var usable_h: float = size.y - PADDING * 2 - BOTTOM_INPUT_HEIGHT - LINE_HEIGHT
	return max(1, int(usable_h / LINE_HEIGHT))


func _get_mono_font() -> Font:
	"""获取等宽字体，回退到主题默认字体。

	Returns:
		Font 或 null。
	"""
	var theme: Theme = ThemeDB.get_project_theme()
	if theme and theme.default_font:
		return theme.default_font
	# 回退：使用稍大一号的默认主题字体
	return get_theme_default_font()


func _notification(what: int) -> void:
	"""处理大小变化等通知。

	Args:
		what: 通知类型。
	"""
	if what == NOTIFICATION_RESIZED:
		queue_redraw()
