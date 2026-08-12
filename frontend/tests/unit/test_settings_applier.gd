"""设置应用器 — SettingsApplier 单元测试。

覆盖 scripts/settings/settings_applier.gd：InputMap 写入、非法事件
忽略、缺失 action 补建、locale 应用。apply_display 的窗口分支在
headless 环境直接跳过（仅验证不崩溃）。
"""

extends GutTest

var _prev_locale: String = ""


func after_each() -> void:
	TranslationServer.set_locale(_prev_locale)
	# 恢复 InputMap 到项目默认，避免污染其他测试
	SettingsApplier.apply_keybinds(KeybindMap.default_binds())


# ── apply_keybinds ──────────────────────────────────────────

func test_apply_keybinds_writes_inputmap() -> void:
	SettingsApplier.apply_keybinds({
		"move_up": [{"type": "key", "keycode": KEY_W}],
	})
	assert_eq(InputMap.action_get_events("move_up").size(), 1, "事件应写入 InputMap")
	var ev: InputEvent = InputMap.action_get_events("move_up")[0]
	assert_true(ev is InputEventKey)
	assert_eq(ev.keycode, KEY_W)


func test_apply_keybinds_clears_existing_events() -> void:
	SettingsApplier.apply_keybinds({
		"move_up": [{"type": "key", "keycode": KEY_W}, {"type": "key", "keycode": KEY_UP}],
	})
	SettingsApplier.apply_keybinds({"move_up": [{"type": "key", "keycode": KEY_W}]})
	assert_eq(InputMap.action_get_events("move_up").size(), 1, "重复应用应清空重建")


func test_apply_keybinds_ignores_invalid_events() -> void:
	SettingsApplier.apply_keybinds({
		"move_up": ["junk", {"type": "gamepad", "button": 1}, {"type": "key", "keycode": 0}],
	})
	assert_eq(InputMap.action_get_events("move_up").size(), 0, "非法事件应被忽略")


func test_apply_keybinds_creates_missing_action() -> void:
	InputMap.erase_action("move_up")
	assert_false(InputMap.has_action("move_up"))
	SettingsApplier.apply_keybinds(KeybindMap.default_binds())
	assert_true(InputMap.has_action("move_up"), "缺失 action 应补建")


func test_apply_keybinds_unknown_action_ignored() -> void:
	SettingsApplier.apply_keybinds({"not_an_action": [{"type": "key", "keycode": KEY_G}]})
	assert_false(InputMap.has_action("not_an_action"), "未知 action 不应被创建")


# ── apply_locale ────────────────────────────────────────────

func test_apply_locale_changes_server_locale() -> void:
	SettingsApplier.apply_locale("en_US")
	assert_eq(TranslationServer.get_locale(), "en_US")


# ── apply_display（headless 安全） ──────────────────────────

func test_apply_display_headless_noop() -> void:
	# headless 下直接返回不崩溃；真实窗口行为由手工验收覆盖
	SettingsApplier.apply_display("1920x1080", "fullscreen")
	SettingsApplier.apply_display("banana", "weird_mode")
