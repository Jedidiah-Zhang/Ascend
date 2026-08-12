"""语言目录 — LocaleCatalog 单元测试。

覆盖 scripts/settings/locale_catalog.gd：语言文件定位、JSON 解析、
TranslationServer 注册、翻译生效与中英键集合完整性对账。
"""

extends GutTest

const REPO_LANG_DIR: String = "res://../lang"

var _prev_locale: String = ""


func before_each() -> void:
	_prev_locale = TranslationServer.get_locale()


func after_each() -> void:
	TranslationServer.set_locale(_prev_locale)


# ── 定位与解析 ──────────────────────────────────────────────

func test_find_lang_dir_finds_repo_lang() -> void:
	var dir: String = LocaleCatalog.find_lang_dir()
	assert_false(dir.is_empty(), "应能找到语言目录（开发期 res://../lang）")
	assert_true(FileAccess.file_exists(dir.path_join("zh_CN.json")))


func test_load_messages_parses_flat_keys() -> void:
	var messages: Dictionary = LocaleCatalog.load_messages(
		REPO_LANG_DIR.path_join("zh_CN.json"))
	assert_true(messages.has("ui.settings"), "应解析出扁平点分键")
	assert_eq(messages["ui.settings"], "设置")
	assert_true(messages.has("console.day_start"), "后端键也应存在（共用文件）")


func test_load_messages_missing_file_returns_empty() -> void:
	assert_eq(LocaleCatalog.load_messages("res://nonexistent/xx.json"), {})


func test_load_messages_corrupt_json_returns_empty() -> void:
	var bad_path := "user://test_bad_lang.json"
	var f := FileAccess.open(bad_path, FileAccess.WRITE)
	f.store_string("{ 这不是合法 JSON !!!")
	f.close()
	assert_eq(LocaleCatalog.load_messages(bad_path), {}, "损坏 JSON 应返回空字典")
	DirAccess.remove_absolute(bad_path)


# ── 注册与翻译 ──────────────────────────────────────────────

func test_load_all_registers_both_locales() -> void:
	var catalog := LocaleCatalog.new()
	assert_eq(catalog.load_all(REPO_LANG_DIR), 2, "zh_CN + en_US 应全部注册")

	TranslationServer.set_locale("zh_CN")
	assert_eq(TranslationServer.tr("ui.settings"), "设置")
	TranslationServer.set_locale("en_US")
	assert_eq(TranslationServer.tr("ui.settings"), "Settings")


func test_load_all_idempotent() -> void:
	"""重复注册应先移除旧 Translation，避免翻译叠加。"""
	var catalog := LocaleCatalog.new()
	catalog.load_all(REPO_LANG_DIR)
	catalog.load_all(REPO_LANG_DIR)
	assert_eq(catalog.registered_count(), 2)


func test_load_all_empty_dir_registers_nothing() -> void:
	var catalog := LocaleCatalog.new()
	assert_eq(catalog.load_all("res://nonexistent"), 0)


# ── 键集合完整性 ────────────────────────────────────────────

func test_both_locales_have_identical_key_sets() -> void:
	"""中英两文件键集合必须一致（缺键会退化为显示键名）。"""
	var zh := LocaleCatalog.load_messages(REPO_LANG_DIR.path_join("zh_CN.json"))
	var en := LocaleCatalog.load_messages(REPO_LANG_DIR.path_join("en_US.json"))
	assert_false(zh.is_empty())
	assert_false(en.is_empty())
	var diff: Dictionary = LocaleCatalog.key_diff(zh, en)
	assert_true(diff["only_in_a"].is_empty(), "zh_CN 独有键：%s" % [diff["only_in_a"]])
	assert_true(diff["only_in_b"].is_empty(), "en_US 独有键：%s" % [diff["only_in_b"]])


func test_key_diff_reports_missing() -> void:
	var diff: Dictionary = LocaleCatalog.key_diff({"a": 1}, {"b": 2})
	assert_eq(diff["only_in_a"], ["a"])
	assert_eq(diff["only_in_b"], ["b"])


# ── 插值兼容 ────────────────────────────────────────────────

func test_interpolation_compatible_with_backend_format() -> void:
	"""{param} 插值应与后端 str.format(**kwargs) 同构（String.format）。"""
	var catalog := LocaleCatalog.new()
	catalog.load_all(REPO_LANG_DIR)
	TranslationServer.set_locale("zh_CN")
	var text: String = TranslationServer.tr("console.day_start").format({"day": 3})
	assert_eq(text, "第 3 天开始", "GDScript format 字典插值应产出与后端一致文本")
