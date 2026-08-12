"""等宽字体工具 — 商业资产字体（Noto Sans Mono CJK SC，OFL 协议）获取。

前端资产（assets/）为闭源商业资源不入库：字体与主题仅存在于本地
开发环境；资源缺失（CI 或新机器）时回退到项目主题默认字体，再缺
回退引擎内置 fallback 字体——保证任何环境不崩、本地体验完整。
"""

class_name FontUtils
extends RefCounted

## 内置等宽字体路径（商业资产，见 frontend/assets/fonts/，不入库）
const MONO_FONT_PATH: String = "res://assets/fonts/NotoSansMonoCJKsc-Regular.otf"

## 缓存加载结果（Resource 常驻，避免每次调用重读磁盘）
static var _mono_font: Font = null


## 获取等宽字体：优先内置 Noto Sans Mono CJK；缺失时回退项目主题默认
## 字体，再缺回退引擎内置 fallback 字体。
static func get_mono_font() -> Font:
	if _mono_font != null:
		return _mono_font
	if ResourceLoader.exists(MONO_FONT_PATH):
		var font := load(MONO_FONT_PATH) as Font
		if font != null:
			_mono_font = font
			return _mono_font
	var project_theme: Theme = ThemeDB.get_project_theme()
	if project_theme and project_theme.default_font:
		return project_theme.default_font
	return ThemeDB.fallback_font
