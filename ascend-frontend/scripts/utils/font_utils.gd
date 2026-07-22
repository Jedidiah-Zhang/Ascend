"""等宽字体工具 — 从项目主题获取等宽字体，回退到主题默认。
"""

class_name FontUtils
extends RefCounted


static func get_mono_font() -> Font:
	var project_theme: Theme = ThemeDB.get_project_theme()
	if project_theme and project_theme.default_font:
		return project_theme.default_font
	return ThemeDB.fallback_font
