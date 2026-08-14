"""设置界面 — SettingsScreen 集成测试。

直接实例化 settings_screen.tscn，注入独立设置门面（settings.gd 脚本
实例 + 临时路径 SettingsStore），验证：打开/关闭、四页装配、语言/
显示变更落盘、按键捕获（成功/取消/冲突）、删除绑定与恢复默认。
"""

extends GutTest

const SCREEN_SCENE: String = "res://scenes/settings_screen.tscn"
const SETTINGS_SCRIPT: String = "res://scripts/autoload/settings.gd"
const TEST_PATH: String = "user://test_settings_screen.cfg"

var _facade = null
var _screen: SettingsScreen = null
var _prev_locale: String = ""


func before_each() -> void:
	_prev_locale = TranslationServer.get_locale()
	_facade = load(SETTINGS_SCRIPT).new()
	_facade.setup(SettingsStore.new(TEST_PATH), null)
	_screen = load(SCREEN_SCENE).instantiate()
	_screen.set_settings_override(_facade)
	add_child(_screen)
	_screen.open()


func after_each() -> void:
	_screen.close()
	_screen.queue_free()
	_facade.free()
	_facade = null
	TranslationServer.set_locale(_prev_locale)
	DirAccess.remove_absolute(TEST_PATH)


func _make_key_event(keycode: Key) -> InputEventKey:
	var ev := InputEventKey.new()
	ev.keycode = keycode
	ev.pressed = true
	return ev


func _row(action: String) -> HBoxContainer:
	return _screen._keys_list.get_node("Row_" + action)


# ── 打开 / 关闭 / 分页 ──────────────────────────────────────

func test_open_shows_four_tabs() -> void:
	assert_true(_screen.is_open(), "open() 后应可见")
	assert_eq(_screen._tab.get_tab_count(), 4, "通用/显示/按键/音频 四页")
	for i in 4:
		assert_false(_screen._tab.get_tab_title(i).is_empty(), "页标题非空")


func test_open_is_idempotent() -> void:
	_screen.open()
	assert_true(_screen.is_open())


func test_close_emits_signal() -> void:
	watch_signals(_screen)
	_screen.close()
	assert_false(_screen.is_open())
	assert_signal_emitted(_screen, "closed")


func test_esc_closes_when_not_capturing() -> void:
	_screen._input(_make_key_event(KEY_ESCAPE))
	assert_false(_screen.is_open(), "非捕获态 ESC 应关闭设置界面")


# ── 语言变更 ────────────────────────────────────────────────

func test_language_selection_persists() -> void:
	watch_signals(_facade)
	_screen._on_language_selected(1)
	assert_eq(_facade.get_locale(), "en_US", "门面应更新语言")
	assert_signal_emitted(_facade, "locale_changed")

	var reloaded := SettingsStore.new(TEST_PATH)
	reloaded.load()
	assert_eq(reloaded.get_value("language/locale"), "en_US", "应落盘")


func test_language_option_synced_on_open() -> void:
	_facade.set_locale("en_US")
	_screen._refresh_language()
	assert_eq(_screen._language_option.get_item_metadata(_screen._language_option.selected),
		"en_US", "下拉框应反映当前语言")


# ── 显示变更 ────────────────────────────────────────────────

func test_resolution_selection_persists() -> void:
	# 选非默认档位（索引 0 即默认值，会被门面幂等守卫拦截为 no-op）
	var target: String = str(_screen._resolution_option.get_item_metadata(1))
	assert_ne(target, _facade.get_display()["resolution"], "索引 1 应非默认档位")
	watch_signals(_facade)
	_screen._on_resolution_selected(1)
	assert_eq(_facade.get_display()["resolution"], target, "分辨率应生效")
	assert_signal_emitted(_facade, "display_changed")
	var reloaded := SettingsStore.new(TEST_PATH)
	reloaded.load()
	assert_eq(reloaded.get_value("display/resolution"), target, "应落盘")


func test_borderless_disables_resolution() -> void:
	var mode_index: int = SettingsStore.WINDOW_MODES.find("borderless")
	_screen._on_mode_selected(mode_index)
	assert_true(_screen._resolution_option.disabled, "无边框全屏下分辨率应禁用")
	assert_true(_screen._resolution_hint.visible, "应显示提示")
	assert_eq(_facade.get_display()["window_mode"], "borderless")


func test_windowed_reenables_resolution() -> void:
	_screen._on_mode_selected(SettingsStore.WINDOW_MODES.find("borderless"))
	_screen._on_mode_selected(SettingsStore.WINDOW_MODES.find("windowed"))
	assert_false(_screen._resolution_option.disabled)
	assert_false(_screen._resolution_hint.visible)


# ── 按键捕获 ────────────────────────────────────────────────

func test_capture_adds_bind() -> void:
	_screen._on_add_bind_pressed("move_up", _row("move_up").get_node("Add"))
	assert_eq(_screen._capturing_action, "move_up", "应进入捕获态")
	_screen._input(_make_key_event(KEY_G))
	assert_eq(_screen._capturing_action, "", "捕获完成应退出捕获态")

	var binds: Array = _facade.keybinds.get_binds("move_up")
	assert_eq(binds.size(), 3, "默认 2 键 + G")
	assert_true(binds.has({"type": "key", "keycode": KEY_G}), "应包含新绑定")
	var reloaded := SettingsStore.new(TEST_PATH)
	reloaded.load()
	assert_eq(reloaded.get_value("input/move_up").size(), 3, "应落盘")
	assert_eq(_row("move_up").get_node("Cap2").text, "G", "键帽应显示新键")


func test_capture_esc_cancels() -> void:
	_screen._on_add_bind_pressed("move_up", _row("move_up").get_node("Add"))
	_screen._input(_make_key_event(KEY_ESCAPE))
	assert_eq(_screen._capturing_action, "", "ESC 应取消捕获")
	assert_eq(_facade.keybinds.get_binds("move_up").size(), 2, "绑定不应变化")
	assert_true(_screen.is_open(), "取消捕获不应关闭设置界面")


func test_capture_conflict_rejected() -> void:
	_screen._on_add_bind_pressed("move_up", _row("move_up").get_node("Add"))
	_screen._input(_make_key_event(KEY_E))
	assert_false(_screen._keys_status.text.is_empty(), "冲突应提示")
	assert_eq(_facade.keybinds.get_binds("move_up").size(), 2, "冲突绑定不应生效")


func test_capture_duplicate_rejected() -> void:
	_screen._on_add_bind_pressed("move_up", _row("move_up").get_node("Add"))
	_screen._input(_make_key_event(KEY_W))
	assert_false(_screen._keys_status.text.is_empty(), "重复应提示")


func test_keycap_click_removes_bind() -> void:
	_screen._on_remove_bind("interact", 0)
	assert_true(_facade.keybinds.get_binds("interact").is_empty(), "键帽点击应删除绑定")
	assert_eq(_row("interact").get_node_or_null("Cap0"), null, "键帽应被移除")
	assert_eq(_row("interact").get_children().size(), 2, "仅剩动作名 + 添加按钮")


func test_reset_binds_restores_defaults() -> void:
	_screen._on_remove_bind("interact", 0)
	_screen._on_reset_binds()
	assert_eq(_facade.keybinds.get_binds("interact"),
		KeybindMap.default_binds()["interact"], "恢复默认应还原")
	assert_eq(_row("interact").get_children().size(), 3, "键帽应重新出现")


# ── 调试模式 ────────────────────────────────────────────────

func test_debug_mode_checkbox_reflects_facade() -> void:
	assert_true(_screen._debug_mode_check.button_pressed, "默认调试模式开启，复选框应勾选")
	_facade.set_debug_mode(false)
	_screen._refresh_debug_mode()
	assert_false(_screen._debug_mode_check.button_pressed, "门面关闭后刷新应取消勾选")


func test_debug_mode_toggle_persists() -> void:
	watch_signals(_facade)
	_screen._on_debug_mode_toggled(false)
	assert_false(_facade.get_debug_mode(), "门面应更新调试模式")
	assert_signal_emitted(_facade, "debug_mode_changed")

	var reloaded := SettingsStore.new(TEST_PATH)
	reloaded.load()
	assert_eq(reloaded.get_value("debug/debug_mode"), false, "应落盘")


# ── 语言切换时刷新 ──────────────────────────────────────────

func test_locale_change_refreshes_keys_rows() -> void:
	# 键帽文案含翻译（鼠标键），切换语言后应重渲染
	_facade.set_locale("en_US")
	await get_tree().process_frame
	assert_true(_screen.is_open(), "语言切换不应关闭设置界面")
