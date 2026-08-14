"""设置界面 — 通用 / 显示 / 按键 / 音频 四页覆盖层。

标准控件 + 傍晚营地主题（assets/ui/settings_theme.tres）；逻辑全部经
Settings 自动加载门面，本壳只装配控件与转发。主菜单与暂停菜单两处
入口共用（CanvasLayer layer=400，process_mode ALWAYS 保证游戏暂停
期间可交互）。

按键捕获：点击「＋ 添加」进入捕获态，下一个按键/鼠标键成为新绑定；
ESC 取消捕获（ESC 保留为取消键，不可绑定）；冲突/重复经状态行提示。
点击键帽删除该绑定。
"""

class_name SettingsScreen
extends CanvasLayer

signal closed

const THEME_PATH: String = "res://assets/ui/settings_theme.tres"
const PANEL_MIN_SIZE := Vector2(760, 520)
const DIM_COLOR: Color = Color(0.043, 0.055, 0.078, 0.6)
const DANGER_COLOR: Color = Color(0.878, 0.471, 0.337)

var _theme: Theme = null
## 测试注入的设置门面（Variant：settings.gd 无 class_name，动态派发）
var _settings_override = null

var _tab: TabContainer = null
var _title_label: Label = null
var _close_button: Button = null
var _done_button: Button = null
var _language_label: Label = null
var _language_option: OptionButton = null
var _debug_mode_label: Label = null
var _debug_mode_check: CheckBox = null
var _debug_mode_hint: Label = null
var _resolution_label: Label = null
var _resolution_option: OptionButton = null
var _resolution_hint: Label = null
var _mode_label: Label = null
var _mode_option: OptionButton = null
var _keys_list: VBoxContainer = null
var _keys_status: Label = null
var _reset_binds_button: Button = null
var _audio_note: Label = null

## 捕获态：等待新按键的动作名（空 = 非捕获态）
var _capturing_action: String = ""
var _capture_button: Button = null


func _ready() -> void:
	layer = 400
	# ALWAYS：暂停菜单打开时游戏 paused，设置界面仍需响应输入
	process_mode = Node.PROCESS_MODE_ALWAYS
	_theme = _load_theme()
	_build_ui()
	_settings().locale_changed.connect(_on_locale_changed)
	hide()


## 主题运行时加载（不入库资源）：缺失时回退引擎默认主题，避免编译期
## 硬依赖导致无该文件的机器无法启动（与 FontUtils 的回退约定一致）。
func _load_theme() -> Theme:
	if ResourceLoader.exists(THEME_PATH):
		var theme := load(THEME_PATH) as Theme
		if theme != null:
			return theme
	push_warning("设置主题缺失（%s），使用默认主题" % THEME_PATH)
	return null


# ── 公开接口 ──────────────────────────────────────────────

func open() -> void:
	"""打开设置界面并刷新全部控件（幂等）。"""
	if visible:
		return
	show()
	_refresh_all()


func close() -> void:
	"""关闭设置界面（幂等），退出捕获态并广播 closed。"""
	if not visible:
		return
	_cancel_capture()
	hide()
	closed.emit()


func is_open() -> bool:
	return visible


## 测试用：注入独立设置门面（须在 add_child 前调用，_ready 取其信号）。
func set_settings_override(override) -> void:
	_settings_override = override


func _settings():
	return _settings_override if _settings_override != null else Settings


# ── 输入 ──────────────────────────────────────────────────

func _input(event: InputEvent) -> void:
	if not visible:
		return
	if _capturing_action != "":
		if event is InputEventKey and event.pressed and not event.echo:
			if event.keycode == KEY_ESCAPE:
				_cancel_capture()
			else:
				_finish_capture(KeybindMap.event_to_dict(event))
			get_viewport().set_input_as_handled()
			return
		if event is InputEventMouseButton and event.pressed:
			_finish_capture(KeybindMap.event_to_dict(event))
			get_viewport().set_input_as_handled()
			return
		return
	if event is InputEventKey and event.pressed and not event.echo \
			and event.is_action_pressed("menu"):
		close()
		get_viewport().set_input_as_handled()


# ── 界面装配 ──────────────────────────────────────────────

func _build_ui() -> void:
	var dim := ColorRect.new()
	dim.name = "Dim"
	dim.color = DIM_COLOR
	dim.set_anchors_preset(Control.PRESET_FULL_RECT)
	dim.mouse_filter = Control.MOUSE_FILTER_STOP
	add_child(dim)

	var center := CenterContainer.new()
	center.set_anchors_preset(Control.PRESET_FULL_RECT)
	dim.add_child(center)

	var panel := PanelContainer.new()
	panel.custom_minimum_size = PANEL_MIN_SIZE
	panel.theme = _theme
	center.add_child(panel)

	var margin := MarginContainer.new()
	margin.add_theme_constant_override("margin_left", 22)
	margin.add_theme_constant_override("margin_top", 18)
	margin.add_theme_constant_override("margin_right", 22)
	margin.add_theme_constant_override("margin_bottom", 18)
	panel.add_child(margin)

	var vbox := VBoxContainer.new()
	vbox.add_theme_constant_override("separation", 12)
	margin.add_child(vbox)

	# 标题行
	var header := HBoxContainer.new()
	vbox.add_child(header)
	_title_label = Label.new()
	_title_label.theme_type_variation = "TitleLabel"
	header.add_child(_title_label)
	var spacer := Control.new()
	spacer.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	header.add_child(spacer)
	_close_button = Button.new()
	_close_button.text = "×"
	_close_button.pressed.connect(close)
	header.add_child(_close_button)

	vbox.add_child(HSeparator.new())

	# 分页
	_tab = TabContainer.new()
	_tab.size_flags_vertical = Control.SIZE_EXPAND_FILL
	vbox.add_child(_tab)
	_build_general_tab()
	_build_display_tab()
	_build_keys_tab()
	_build_audio_tab()

	# 底栏
	var footer := HBoxContainer.new()
	footer.alignment = BoxContainer.ALIGNMENT_END
	vbox.add_child(footer)
	_done_button = Button.new()
	_done_button.pressed.connect(close)
	footer.add_child(_done_button)


func _build_general_tab() -> void:
	var page := VBoxContainer.new()
	page.name = "general"
	page.add_theme_constant_override("separation", 14)
	_tab.add_child(page)
	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 12)
	page.add_child(row)
	_language_label = Label.new()
	_language_label.custom_minimum_size = Vector2(110, 0)
	row.add_child(_language_label)
	_language_option = OptionButton.new()
	_language_option.custom_minimum_size = Vector2(220, 0)
	_language_option.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	var index: int = 0
	for entry in LocaleCatalog.LOCALES:
		_language_option.add_item(entry["label"])
		_language_option.set_item_metadata(index, entry["locale"])
		index += 1
	_language_option.item_selected.connect(_on_language_selected)
	row.add_child(_language_option)

	var debug_row := HBoxContainer.new()
	debug_row.add_theme_constant_override("separation", 12)
	page.add_child(debug_row)
	_debug_mode_label = Label.new()
	_debug_mode_label.custom_minimum_size = Vector2(110, 0)
	_debug_mode_label.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
	debug_row.add_child(_debug_mode_label)
	_debug_mode_check = CheckBox.new()
	_debug_mode_check.custom_minimum_size = Vector2(220, 0)
	_debug_mode_check.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_debug_mode_check.toggled.connect(_on_debug_mode_toggled)
	debug_row.add_child(_debug_mode_check)

	_debug_mode_hint = Label.new()
	_debug_mode_hint.theme_type_variation = "MutedLabel"
	page.add_child(_debug_mode_hint)


func _build_display_tab() -> void:
	var page := VBoxContainer.new()
	page.name = "display"
	page.add_theme_constant_override("separation", 14)
	_tab.add_child(page)

	var res_row := HBoxContainer.new()
	res_row.add_theme_constant_override("separation", 12)
	page.add_child(res_row)
	_resolution_label = Label.new()
	_resolution_label.custom_minimum_size = Vector2(110, 0)
	res_row.add_child(_resolution_label)
	_resolution_option = OptionButton.new()
	_resolution_option.custom_minimum_size = Vector2(220, 0)
	_resolution_option.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_resolution_option.item_selected.connect(_on_resolution_selected)
	res_row.add_child(_resolution_option)

	var mode_row := HBoxContainer.new()
	mode_row.add_theme_constant_override("separation", 12)
	page.add_child(mode_row)
	_mode_label = Label.new()
	_mode_label.custom_minimum_size = Vector2(110, 0)
	mode_row.add_child(_mode_label)
	_mode_option = OptionButton.new()
	_mode_option.custom_minimum_size = Vector2(220, 0)
	_mode_option.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	for mode in SettingsStore.WINDOW_MODES:
		var idx: int = _mode_option.item_count
		_mode_option.add_item("ui.settings.mode." + mode)  # 文本在 _refresh_texts
		_mode_option.set_item_metadata(idx, mode)
	_mode_option.item_selected.connect(_on_mode_selected)
	mode_row.add_child(_mode_option)

	_resolution_hint = Label.new()
	_resolution_hint.theme_type_variation = "MutedLabel"
	page.add_child(_resolution_hint)


func _build_keys_tab() -> void:
	var page := VBoxContainer.new()
	page.name = "keys"
	page.add_theme_constant_override("separation", 10)
	_tab.add_child(page)
	var scroll := ScrollContainer.new()
	scroll.size_flags_vertical = Control.SIZE_EXPAND_FILL
	page.add_child(scroll)
	_keys_list = VBoxContainer.new()
	_keys_list.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_keys_list.add_theme_constant_override("separation", 8)
	scroll.add_child(_keys_list)

	var bottom := HBoxContainer.new()
	bottom.add_theme_constant_override("separation", 12)
	page.add_child(bottom)
	_reset_binds_button = Button.new()
	_reset_binds_button.theme_type_variation = "DangerButton"
	_reset_binds_button.pressed.connect(_on_reset_binds)
	bottom.add_child(_reset_binds_button)
	_keys_status = Label.new()
	_keys_status.theme_type_variation = "MutedLabel"
	_keys_status.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_keys_status.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
	bottom.add_child(_keys_status)


func _build_audio_tab() -> void:
	var page := VBoxContainer.new()
	page.name = "audio"
	page.alignment = BoxContainer.ALIGNMENT_CENTER
	_tab.add_child(page)
	_audio_note = Label.new()
	_audio_note.theme_type_variation = "MutedLabel"
	_audio_note.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	page.add_child(_audio_note)


## 按键页单行：动作名 + 键帽列表（点击移除）+「＋ 添加」。
func _make_key_row(action: String) -> HBoxContainer:
	var row := HBoxContainer.new()
	row.name = "Row_" + action
	row.add_theme_constant_override("separation", 8)
	var name_label := Label.new()
	name_label.text = tr("ui.settings.action." + action)
	name_label.custom_minimum_size = Vector2(110, 0)
	name_label.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
	row.add_child(name_label)
	var binds: Array = _settings().keybinds.get_binds(action)
	for i in binds.size():
		var cap := Button.new()
		cap.name = "Cap%d" % i
		cap.theme_type_variation = "KeyCap"
		cap.text = KeybindMap.event_label(binds[i])
		cap.tooltip_text = tr("ui.settings.remove_bind_hint")
		var index: int = i
		cap.pressed.connect(func() -> void: _on_remove_bind(action, index))
		row.add_child(cap)
	var add := Button.new()
	add.name = "Add"
	add.text = tr("ui.settings.add_bind")
	add.pressed.connect(func() -> void: _on_add_bind_pressed(action, add))
	row.add_child(add)
	return row


# ── 刷新 ──────────────────────────────────────────────────

func _refresh_all() -> void:
	_refresh_texts()
	_refresh_language()
	_refresh_display()
	_refresh_debug_mode()
	_refresh_keys()


## 全部静态文案重翻译（语言切换后调用）。
func _refresh_texts() -> void:
	_title_label.text = tr("ui.settings")
	_close_button.tooltip_text = tr("ui.close")
	_done_button.text = tr("ui.settings.done")
	_tab.set_tab_title(0, tr("ui.settings.tab.general"))
	_tab.set_tab_title(1, tr("ui.settings.tab.display"))
	_tab.set_tab_title(2, tr("ui.settings.tab.keys"))
	_tab.set_tab_title(3, tr("ui.settings.tab.audio"))
	_language_label.text = tr("ui.settings.language")
	_debug_mode_label.text = tr("ui.settings.debug_mode")
	_debug_mode_hint.text = tr("ui.settings.debug_mode_hint")
	_resolution_label.text = tr("ui.settings.resolution")
	_mode_label.text = tr("ui.settings.window_mode")
	for i in _mode_option.item_count:
		_mode_option.set_item_text(i, tr("ui.settings.mode." + str(_mode_option.get_item_metadata(i))))
	_resolution_hint.text = tr("ui.settings.resolution_disabled")
	_reset_binds_button.text = tr("ui.settings.reset_binds")
	_audio_note.text = tr("ui.settings.audio_note")


func _refresh_language() -> void:
	var locale: String = _settings().get_locale()
	for i in _language_option.item_count:
		if _language_option.get_item_metadata(i) == locale:
			_language_option.select(i)
			return


## 调试模式复选框与门面同步（幂等：同值设置不触发 toggled 事件）。
func _refresh_debug_mode() -> void:
	_debug_mode_check.button_pressed = _settings().get_debug_mode()


func _refresh_display() -> void:
	var d: Dictionary = _settings().get_display()
	var screen := DisplayServer.screen_get_size(DisplayServer.window_get_current_screen())
	var options := SettingsStore.resolution_options(str(d["resolution"]), screen)
	_resolution_option.clear()
	var selected: int = 0
	for i in options.size():
		var v := SettingsStore.parse_resolution(options[i])
		_resolution_option.add_item("%d × %d" % [v.x, v.y])
		_resolution_option.set_item_metadata(i, options[i])
		if options[i] == d["resolution"]:
			selected = i
	_resolution_option.select(selected)
	var mode := str(d["window_mode"])
	for i in _mode_option.item_count:
		if _mode_option.get_item_metadata(i) == mode:
			_mode_option.select(i)
			break
	_update_resolution_enabled(mode)


## 无边框全屏下分辨率无意义（跟随桌面），禁用并提示。
func _update_resolution_enabled(mode: String) -> void:
	var borderless: bool = mode == "borderless"
	_resolution_option.disabled = borderless
	_resolution_hint.visible = borderless


## 按键页整页重建（行数少，重建最省心；同时复位捕获按钮引用）。
## 先 remove_child 再 queue_free：信号回调内安全，且释放不占名，
## 新行立即拿到干净的 Row_<action> 名称。
func _refresh_keys() -> void:
	_capture_button = null
	for child in _keys_list.get_children():
		_keys_list.remove_child(child)
		child.queue_free()
	for action in KeybindMap.ACTIONS:
		_keys_list.add_child(_make_key_row(action))
	# 捕获态行的「添加」按钮恢复捕获文案
	if _capturing_action != "":
		var row := _keys_list.get_node_or_null("Row_" + _capturing_action)
		if row:
			var add := row.get_node_or_null("Add")
			if add:
				add.text = tr("ui.settings.capture")
				_capture_button = add


# ── 事件 ──────────────────────────────────────────────────

func _on_language_selected(index: int) -> void:
	_settings().set_locale(str(_language_option.get_item_metadata(index)))


func _on_debug_mode_toggled(pressed: bool) -> void:
	_settings().set_debug_mode(pressed)


func _on_resolution_selected(index: int) -> void:
	var resolution := str(_resolution_option.get_item_metadata(index))
	var mode := str(_mode_option.get_item_metadata(maxi(_mode_option.selected, 0)))
	_settings().set_display(resolution, mode)


func _on_mode_selected(index: int) -> void:
	var mode := str(_mode_option.get_item_metadata(index))
	var resolution := str(_resolution_option.get_item_metadata(maxi(_resolution_option.selected, 0)))
	_update_resolution_enabled(mode)
	_settings().set_display(resolution, mode)


func _on_remove_bind(action: String, index: int) -> void:
	_cancel_capture()
	_settings().remove_bind(action, index)
	_set_keys_status("", false)
	_refresh_keys()


func _on_add_bind_pressed(action: String, button: Button) -> void:
	_cancel_capture()
	_capturing_action = action
	_capture_button = button
	button.text = tr("ui.settings.capture")
	_set_keys_status("", false)


func _on_reset_binds() -> void:
	_cancel_capture()
	_settings().reset_keybinds()
	_set_keys_status("", false)
	_refresh_keys()


func _on_locale_changed(_locale: String) -> void:
	_refresh_texts()
	_refresh_keys()


# ── 捕获 ──────────────────────────────────────────────────

## 捕获到新按键：交给门面裁决（重复/冲突拒绝并提示）。
func _finish_capture(event_dict: Dictionary) -> void:
	var action := _capturing_action
	_cancel_capture()
	if event_dict.is_empty():
		# 仅 physical_keycode 的键（布局/IME 等）无 keycode 可绑定
		_set_keys_status(tr("ui.settings.bind_unsupported"), true)
		return
	var result: Dictionary = _settings().add_bind(action, event_dict)
	if result.get("ok", false):
		_set_keys_status("", false)
	else:
		match str(result.get("reason", "")):
			"duplicate":
				_set_keys_status(tr("ui.settings.bind_duplicate"), true)
			"conflict":
				var holder := tr("ui.settings.action." + str(result.get("conflict", "")))
				_set_keys_status(tr("ui.settings.bind_conflict").format({"action": holder}), true)
	_refresh_keys()


func _cancel_capture() -> void:
	if _capture_button != null and is_instance_valid(_capture_button):
		_capture_button.text = tr("ui.settings.add_bind")
	_capturing_action = ""
	_capture_button = null


func _set_keys_status(text: String, is_error: bool) -> void:
	_keys_status.text = text
	if is_error:
		_keys_status.add_theme_color_override("font_color", DANGER_COLOR)
	else:
		_keys_status.remove_theme_color_override("font_color")
