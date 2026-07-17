"""调试终端 UI 组件 — Godot 前端内置半透明控制台。

支持本地指令（register_command 注册）和远程指令转发。
所有内容通过 _draw() 渲染，无 RichTextLabel 等子节点。

本地指令模仿 DebugSection 的注册模式：
	term.register_command("tp", _cmd_teleport, "tp <x> <y> - 传送玩家")
handler 签名: func(args: PackedStringArray) -> String（返回输出文本，空串无输出）。

用法:
    var term = TerminalWidget.new()
    term.remote_command_submitted.connect(_on_terminal_command)
	term.register_command("tp", _cmd_teleport, "tp <x> <y> - 传送玩家")
    add_child(term)
    term.open()
"""

extends Control

class_name TerminalWidget

const Config = preload("res://scripts/config.gd")


# ── 信号 ────────────────────────────────────────────────────

signal remote_command_submitted(command: String)


# ── 常量 ────────────────────────────────────────────────────

const BG_COLOR: Color = Color(0.05, 0.05, 0.10, 0.75)
const TEXT_COLOR: Color = Color(0.90, 0.90, 0.95)
const PROMPT_COLOR: Color = Color(0.60, 0.90, 0.60)
const INFO_COLOR: Color = Color(0.60, 0.80, 0.60)
const CURSOR_COLOR: Color = Color(0.80, 0.85, 0.95)
const FONT_SIZE: int = Config.TERMINAL_FONT_SIZE
const LINE_HEIGHT: int = 20
const PADDING: int = 10
const BOTTOM_INPUT_HEIGHT: int = 30

const OUTPUT_LINE_LIMIT: int = Config.TERMINAL_OUTPUT_LINE_LIMIT
const HISTORY_LIMIT: int = Config.TERMINAL_HISTORY_LIMIT
const PROMPT_STR: String = Config.TERMINAL_PROMPT

## 光标闪烁周期（秒）
const CURSOR_BLINK_INTERVAL: float = 0.5


# ── 属性 ────────────────────────────────────────────────────

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

## 本地指令注册表: name -> {"handler": Callable, "help": String}
var _local_commands: Dictionary = {}


# ── 生命周期 ────────────────────────────────────────────────

func _ready() -> void:
	"""初始化终端组件：设置锚点、字体、内置指令、初始输出。"""
	anchor_left = 0.0
	anchor_top = 0.0
	anchor_right = 1.0
	anchor_bottom = 1.0
	mouse_filter = Control.MOUSE_FILTER_IGNORE
	_font = _get_mono_font()
	_font_height = FONT_SIZE + 2
	hide()
	register_command("clear", _cmd_clear, "clear - 清空终端输出")
	_write_output("Ascend 调试终端")
	_write_output("输入 help 查看指令列表，/ 切换终端")


func _notification(what: int) -> void:
	"""处理大小变化等通知。"""
	if what == NOTIFICATION_RESIZED:
		queue_redraw()


func _process(delta: float) -> void:
	"""每帧：更新光标闪烁。"""
	if not _is_open:
		return

	_blink_timer += delta
	if _blink_timer >= CURSOR_BLINK_INTERVAL:
		_blink_timer = 0.0
		_cursor_visible = not _cursor_visible
		queue_redraw()


func _input(event: InputEvent) -> void:
	"""当终端打开时捕获所有键盘事件。"""
	if not _is_open:
		return

	if event is InputEventKey and event.pressed and not event.echo:
		var key: Key = event.keycode
		var ctrl: bool = event.ctrl_pressed

		if key == Key.KEY_SLASH and not event.shift_pressed and not ctrl and not event.alt_pressed:
			close()
			get_viewport().set_input_as_handled()
			return

		if key == Key.KEY_ENTER or key == Key.KEY_KP_ENTER:
			_execute_input()
			get_viewport().set_input_as_handled()
			return

		if key == Key.KEY_BACKSPACE:
			_do_backspace()
			get_viewport().set_input_as_handled()
			return

		if key == Key.KEY_DELETE:
			_do_delete()
			get_viewport().set_input_as_handled()
			return

		if key == Key.KEY_LEFT:
			if _cursor_pos > 0:
				_cursor_pos -= 1
			queue_redraw()
			get_viewport().set_input_as_handled()
			return

		if key == Key.KEY_RIGHT:
			if _cursor_pos < _input_text.length():
				_cursor_pos += 1
			queue_redraw()
			get_viewport().set_input_as_handled()
			return

		if key == Key.KEY_HOME:
			_cursor_pos = 0
			queue_redraw()
			get_viewport().set_input_as_handled()
			return

		if key == Key.KEY_END:
			_cursor_pos = _input_text.length()
			queue_redraw()
			get_viewport().set_input_as_handled()
			return

		if key == Key.KEY_UP:
			_history_up()
			get_viewport().set_input_as_handled()
			return

		if key == Key.KEY_DOWN:
			_history_down()
			get_viewport().set_input_as_handled()
			return

		if key == Key.KEY_PAGEUP:
			_scroll_offset = min(_scroll_offset + 20, _visible_line_count())
			queue_redraw()
			get_viewport().set_input_as_handled()
			return

		if key == Key.KEY_PAGEDOWN:
			_scroll_offset = max(0, _scroll_offset - 20)
			queue_redraw()
			get_viewport().set_input_as_handled()
			return

		if not ctrl and event.unicode > 31 and event.unicode < 0x10FFFF:
			_insert_char(char(event.unicode))
			get_viewport().set_input_as_handled()
			return


func _draw() -> void:
	"""绘制终端背景和所有文本行。"""
	if not _is_open or _font == null:
		return

	draw_rect(Rect2(Vector2.ZERO, size), BG_COLOR)

	var usable_w: float = size.x - PADDING * 2
	var input_area_top: float = size.y - PADDING - BOTTOM_INPUT_HEIGHT

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

	var input_base_y: float = size.y - PADDING - _font_height
	draw_string(_font, Vector2(PADDING, input_base_y), PROMPT_STR + _input_text,
			HORIZONTAL_ALIGNMENT_LEFT, usable_w, FONT_SIZE, PROMPT_COLOR)

	if _cursor_visible:
		var cursor_x: float = PADDING + _font.get_string_size(PROMPT_STR + _input_text.left(_cursor_pos),
				HORIZONTAL_ALIGNMENT_LEFT, usable_w).x
		var cursor_rect := Rect2(cursor_x, input_base_y, 2, _font_height)
		draw_rect(cursor_rect, CURSOR_COLOR)


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
	"""终端是否已打开。"""
	return _is_open


func write(text: String) -> void:
	"""向终端输出文本（来自后端响应）。"""
	if text.is_empty():
		return
	_write_output(text)


func register_command(cmd_name: String, handler: Callable, help_text: String = "") -> void:
	"""注册本地指令（模仿 DebugSection 的注册模式）。

	Args:
		cmd_name: 指令名（首个空格前的词，不区分大小写）。
		handler: func(args: PackedStringArray) -> String，
			返回要写入终端的输出文本（空串表示无输出）。
		help_text: help 指令中显示的说明行。
	"""
	_local_commands[cmd_name.to_lower()] = {
		"handler": handler,
		"help": help_text,
	}


func unregister_command(cmd_name: String) -> void:
	"""注销本地指令。

	Args:
		cmd_name: 已注册的指令名。
	"""
	_local_commands.erase(cmd_name.to_lower())


# ── 输入编辑 ────────────────────────────────────────────────

func _insert_char(ch: String) -> void:
	"""在光标位置插入字符。"""
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

	_history.append(input)
	if _history.size() > HISTORY_LIMIT:
		_history = _history.slice(1)
	_history_idx = -1

	_write_output(PROMPT_STR + input)

	_input_text = ""
	_cursor_pos = 0
	_scroll_offset = 0

	var cmd: String = input.get_slice(" ", 0).to_lower()

	if cmd == "help" or cmd == "?":
		_show_local_help()
		# 已连接后端时继续转发，追加远程指令帮助
		remote_command_submitted.emit(input)
		return

	if _local_commands.has(cmd):
		var entry: Dictionary = _local_commands[cmd]
		var args: PackedStringArray = input.split(" ", false).slice(1)
		var handler: Callable = entry["handler"]
		var output: Variant = handler.call(args)
		if output is String and not (output as String).is_empty():
			_write_output(output)
		return

	remote_command_submitted.emit(input)


func _cmd_clear(_args: PackedStringArray) -> String:
	"""内置指令：清空终端输出。"""
	_output_lines.clear()
	queue_redraw()
	return ""


func _show_local_help() -> void:
	"""显示本地指令帮助，远程指令帮助由后端响应追加。"""
	_write_output("--- 本地指令 ---")
	var names: Array = _local_commands.keys()
	names.sort()
	for cmd_name: String in names:
		var help_text: String = _local_commands[cmd_name]["help"]
		if help_text.is_empty():
			help_text = cmd_name
		_write_output("  " + help_text)
	_write_output("--- 远程指令 ---")


# ── 辅助函数 ────────────────────────────────────────────────

func _write_output(text: String) -> void:
	"""添加一行输出并触发重绘。"""
	var lines: PackedStringArray = text.split("\n", false)
	for line in lines:
		if line.is_empty():
			continue
		_output_lines.append(line)

	if _output_lines.size() > OUTPUT_LINE_LIMIT:
		_output_lines = _output_lines.slice(_output_lines.size() - OUTPUT_LINE_LIMIT)

	queue_redraw()


func _visible_line_count() -> int:
	"""计算当前可见区域可容纳的输出行数。"""
	var usable_h: float = size.y - PADDING * 2 - BOTTOM_INPUT_HEIGHT - LINE_HEIGHT
	return max(1, int(usable_h / LINE_HEIGHT))


func _get_mono_font() -> Font:
	"""获取等宽字体，回退到主题默认字体。"""
	var project_theme: Theme = ThemeDB.get_project_theme()
	if project_theme and project_theme.default_font:
		return project_theme.default_font
	return get_theme_default_font()
