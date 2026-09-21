"""按键绑定映射 — KeybindMap 单元测试。

覆盖 scripts/settings/keybind_map.gd：默认表与 project.godot [input]
对账、冲突检测、序列化与显示名（含鼠标键的翻译路径）。
"""

extends GutTest


# ── 默认表对账 ──────────────────────────────────────────────

func test_defaults_match_project_input_section() -> void:
	"""默认表必须镜像 project.godot [input] 段（改动需双向同步）。"""
	for action in KeybindMap.ACTIONS:
		var raw: Variant = ProjectSettings.get_setting("input/" + action)
		assert_true(raw is Dictionary, "project.godot 应定义 %s" % action)
		if not raw is Dictionary:
			continue
		var events: Array = raw.get("events", [])
		var defaults: Array = KeybindMap.default_binds().get(action, [])
		assert_eq(events.size(), defaults.size(),
			"%s 事件数应与 project.godot 一致" % action)
		for i in mini(events.size(), defaults.size()):
			assert_eq(KeybindMap.event_to_dict(events[i]), defaults[i],
				"%s 第 %d 个绑定应一致" % [action, i])


func test_default_binds_cover_all_actions() -> void:
	var defaults: Dictionary = KeybindMap.default_binds()
	for action in KeybindMap.ACTIONS:
		assert_true(defaults.has(action), "每个动作都应有默认绑定")
		assert_false(defaults[action].is_empty(), "默认绑定不得为空")


# ── 序列化 ──────────────────────────────────────────────────

func test_event_dict_roundtrip_key() -> void:
	var ev := InputEventKey.new()
	ev.keycode = KEY_G
	var d: Dictionary = KeybindMap.event_to_dict(ev)
	assert_eq(d, {"type": "key", "keycode": KEY_G})
	var back: InputEvent = KeybindMap.dict_to_event(d)
	assert_true(back is InputEventKey)
	assert_eq(back.keycode, KEY_G)


func test_event_dict_roundtrip_mouse_wheel() -> void:
	var ev := InputEventMouseButton.new()
	ev.button_index = MOUSE_BUTTON_WHEEL_UP
	var d: Dictionary = KeybindMap.event_to_dict(ev)
	assert_eq(d, {"type": "mouse", "button": MOUSE_BUTTON_WHEEL_UP})
	var back: InputEvent = KeybindMap.dict_to_event(d)
	assert_true(back is InputEventMouseButton)
	assert_eq(back.button_index, MOUSE_BUTTON_WHEEL_UP)


func test_event_to_dict_rejects_unsupported() -> void:
	var ev := InputEventMouseMotion.new()
	assert_eq(KeybindMap.event_to_dict(ev), {}, "不支持的事件类型应返回空字典")


func test_sanitize_event_normalizes_floats() -> void:
	"""INI/JSON 读出的 keycode 可能为 float，应归一为 int 参与冲突判定。"""
	var d: Dictionary = KeybindMap.sanitize_event({"type": "key", "keycode": 87.0})
	assert_eq(d, {"type": "key", "keycode": 87})
	var d2: Dictionary = KeybindMap.sanitize_event({"type": "mouse", "button": 4.5})
	assert_eq(d2, {}, "非法鼠标键（小数）应被拒绝")


func test_sanitize_event_rejects_garbage() -> void:
	assert_eq(KeybindMap.sanitize_event("junk"), {})
	assert_eq(KeybindMap.sanitize_event({"type": "gamepad", "button": 1}), {})
	assert_eq(KeybindMap.sanitize_event({"type": "key", "keycode": 0}), {})


# ── 添加与冲突 ──────────────────────────────────────────────

func test_add_bind_ok() -> void:
	var km := KeybindMap.new()
	var result: Dictionary = km.add_bind("move_up", {"type": "key", "keycode": KEY_G})
	assert_true(result["ok"])
	assert_eq(km.get_binds("move_up").size(), 3, "默认 2 键 + 新增 1 键")


func test_add_bind_duplicate_rejected() -> void:
	var km := KeybindMap.new()
	var result: Dictionary = km.add_bind("move_up", {"type": "key", "keycode": KEY_W})
	assert_false(result["ok"])
	assert_eq(result["reason"], "duplicate")


func test_add_bind_conflict_rejected() -> void:
	var km := KeybindMap.new()
	var result: Dictionary = km.add_bind("move_up", {"type": "key", "keycode": KEY_E})
	assert_false(result["ok"])
	assert_eq(result["reason"], "conflict")
	assert_eq(result["conflict"], "interact", "应指出占用动作")


func test_add_bind_unknown_action_rejected() -> void:
	var km := KeybindMap.new()
	var result: Dictionary = km.add_bind("fly", {"type": "key", "keycode": KEY_G})
	assert_false(result["ok"])
	assert_eq(result["reason"], "unknown_action")


func test_find_conflict_excludes_self() -> void:
	var km := KeybindMap.new()
	assert_eq(km.find_conflict({"type": "key", "keycode": KEY_W}, "move_up"), "",
		"自身已有绑定不算冲突")


# ── 删除 / 清空 / 重置 ──────────────────────────────────────

func test_remove_bind() -> void:
	var km := KeybindMap.new()
	km.remove_bind("move_up", 0)
	assert_eq(km.get_binds("move_up").size(), 1)
	km.remove_bind("move_up", 99)
	assert_eq(km.get_binds("move_up").size(), 1, "越界删除应忽略")


func test_clear_action() -> void:
	var km := KeybindMap.new()
	km.clear_action("interact")
	assert_true(km.get_binds("interact").is_empty())


func test_reset_restores_defaults() -> void:
	var km := KeybindMap.new()
	km.clear_action("move_up")
	km.add_bind("move_up", {"type": "key", "keycode": KEY_G})
	km.reset()
	assert_eq(km.get_binds("move_up"), KeybindMap.default_binds()["move_up"])


func test_to_dict_is_deep_copy() -> void:
	var km := KeybindMap.new()
	var out: Dictionary = km.to_dict()
	out["move_up"] = []
	assert_eq(km.get_binds("move_up").size(), 2, "导出修改不应影响内部状态")


func test_init_with_invalid_binds_cleans() -> void:
	var km := KeybindMap.new({
		"move_up": [{"type": "key", "keycode": 87.0}, "junk", {"type": "mouse", "button": 5}],
		"bogus_action": [{"type": "key", "keycode": 1}],
	})
	assert_eq(km.get_binds("move_up").size(), 2, "非法项静默丢弃")
	assert_true(km.get_binds("bogus_action").is_empty(), "未知动作不进入内部表")


# ── 显示名 ──────────────────────────────────────────────────

func test_event_label_arrows() -> void:
	assert_eq(KeybindMap.event_label({"type": "key", "keycode": KEY_UP}), "↑")
	assert_eq(KeybindMap.event_label({"type": "key", "keycode": KEY_DOWN}), "↓")
	assert_eq(KeybindMap.event_label({"type": "key", "keycode": KEY_LEFT}), "←")
	assert_eq(KeybindMap.event_label({"type": "key", "keycode": KEY_RIGHT}), "→")


func test_event_label_keycode_string() -> void:
	assert_eq(KeybindMap.event_label({"type": "key", "keycode": KEY_W}), "W")


func test_event_label_unknown_fallback() -> void:
	assert_eq(KeybindMap.event_label({}), "?")


# ── 鼠标键显示名（走翻译） ──────────────────────────────────

func test_event_label_mouse_uses_translation() -> void:
	"""鼠标键显示名经 TranslationServer：注册最小翻译后断言五分支。"""
	var translation := Translation.new()
	translation.locale = "zh_CN"
	translation.add_message("ui.settings.key.mouse_left", "鼠标左键")
	translation.add_message("ui.settings.key.mouse_middle", "鼠标中键")
	translation.add_message("ui.settings.key.mouse_right", "鼠标右键")
	translation.add_message("ui.settings.key.mwheel_up", "滚轮上")
	translation.add_message("ui.settings.key.mwheel_down", "滚轮下")
	TranslationServer.add_translation(translation)
	var prev_locale: String = TranslationServer.get_locale()
	TranslationServer.set_locale("zh_CN")

	assert_eq(KeybindMap.event_label({"type": "mouse", "button": MOUSE_BUTTON_LEFT}), "鼠标左键")
	assert_eq(KeybindMap.event_label({"type": "mouse", "button": MOUSE_BUTTON_MIDDLE}), "鼠标中键")
	assert_eq(KeybindMap.event_label({"type": "mouse", "button": MOUSE_BUTTON_RIGHT}), "鼠标右键")
	assert_eq(KeybindMap.event_label({"type": "mouse", "button": MOUSE_BUTTON_WHEEL_UP}), "滚轮上")
	assert_eq(KeybindMap.event_label({"type": "mouse", "button": MOUSE_BUTTON_WHEEL_DOWN}), "滚轮下")
	assert_eq(KeybindMap.event_label({"type": "mouse", "button": 99}), "Mouse 99",
		"未知鼠标键回退编号形式")

	TranslationServer.set_locale(prev_locale)
	TranslationServer.remove_translation(translation)
