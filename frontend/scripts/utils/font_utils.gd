"""等宽字体工具 — 从项目主题获取等宽字体，主题缺失或未设默认字体时
回退到引擎内置 fallback 字体。
"""

class_name FontUtils
extends RefCounted


## 获取等宽字体：优先返回项目主题的默认字体，主题缺失或未设默认字体时
## 回退到引擎内置 fallback 字体，保证日志等场景始终有可用字体。
static func get_mono_font() -> Font:
	var project_theme: Theme = ThemeDB.get_project_theme()
	if project_theme and project_theme.default_font:
		return project_theme.default_font
	return ThemeDB.fallback_font
