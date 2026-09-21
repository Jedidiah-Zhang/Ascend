"""语言目录 — 加载与后端共用的 lang/<locale>.json 并注册 Translation。

打包构建会把语言文件拷入 PCK（res://lang）；开发期读游戏区语言目录（res://../lang = miskhak/lang）。
翻译键为扁平点分键（如 "ui.settings"），插值用 {param}：GDScript 侧
tr(key) % {"param": v}，与后端 Python str.format(**kwargs) 同构。
"""

class_name LocaleCatalog
extends RefCounted

## 语言目录候选顺序：res://../lang（开发期游戏区语言目录）优先于
## res://lang（打包构建拷入 PCK 的副本）。
const SEARCH_DIRS: Array[String] = ["res://../lang", "res://lang"]
const LOCALES: Array[Dictionary] = [
	{"locale": "zh_CN", "label": "简体中文"},
	{"locale": "en_US", "label": "English"},
]

var _translations: Dictionary = {}


## 定位语言目录（第一个含 zh_CN.json 的候选）；找不到返回空串。
static func find_lang_dir() -> String:
	for dir in SEARCH_DIRS:
		if FileAccess.file_exists(dir.path_join("zh_CN.json")):
			return dir
	return ""


## 解析单个语言文件；缺失/JSON 非法返回 {}（实例 parse 静默返回错误码）。
static func load_messages(path: String) -> Dictionary:
	if not FileAccess.file_exists(path):
		return {}
	var parser := JSON.new()
	if parser.parse(FileAccess.get_file_as_string(path)) != OK:
		return {}
	var data: Variant = parser.data
	if data is Dictionary:
		return data
	return {}


## 加载并注册全部语言（重复调用先移除同一 locale 已注册的 Translation）。返回注册数。
func load_all(lang_dir: String = "") -> int:
	if lang_dir.is_empty():
		lang_dir = find_lang_dir()
	if lang_dir.is_empty():
		return 0
	var count: int = 0
	for entry in LOCALES:
		var locale: String = entry["locale"]
		var messages: Dictionary = load_messages(lang_dir.path_join(locale + ".json"))
		if messages.is_empty():
			continue
		if _translations.has(locale):
			TranslationServer.remove_translation(_translations[locale])
		var translation := Translation.new()
		translation.locale = locale
		for key in messages:
			translation.add_message(str(key), str(messages[key]))
		TranslationServer.add_translation(translation)
		_translations[locale] = translation
		count += 1
	return count


## 已注册语言数（测试断言用）。
func registered_count() -> int:
	return _translations.size()


## 两语言键集合差（完整性对账）：{"only_in_a": [...], "only_in_b": [...]}。
static func key_diff(a: Dictionary, b: Dictionary) -> Dictionary:
	var only_a: Array = []
	var only_b: Array = []
	for key in a:
		if not b.has(key):
			only_a.append(key)
	for key in b:
		if not a.has(key):
			only_b.append(key)
	return {"only_in_a": only_a, "only_in_b": only_b}
