"""地图预览调色 — 预览网格着色与悬停/范围信息文本。

从 MapSetupStep 拆出的纯渲染逻辑（RefCounted，无节点依赖，可单测）：
  - 单格着色：海拔深度渐变 / 温度冷蓝→暖红 / 降雨干黄→湿蓝 / 气候档定色
  - 图层视图语义：海域在地形视图保留深度渐变、气候视图统一深蓝
    （气候带仅陆地有意义），温度/降雨视图显示海域数据
  - 信息行：当前视图数值范围 + 悬停格数值（气候视图为档位名/海洋）

颜色常量（climate 档位色与后端 data/climate.json display_color 一致）与
气候档位名键（ui.map.climate_*）一并在此单源定义。
"""

class_name MapPreviewPalette
extends RefCounted


## 海洋色（仅气候视图海域用：气候带只有陆地有意义；
## 温度/降雨视图海域显示后端数据，地形视图保留深度渐变）
const SEA_COLOR: Color = Color(0.08, 0.12, 0.32)

## 气候档位 0-7 着色（与后端 climate.ClimateTemplate.display_color 一致）
const CLIMATE_ZONE_COLORS: Array = [
	Color("1a6b3a"),  # 0 热带雨林
	Color("c4a43e"),  # 1 热带草原
	Color("e6c878"),  # 2 沙漠
	Color("b8a060"),  # 3 草原
	Color("4a7c3f"),  # 4 温带森林
	Color("3a6a8a"),  # 5 亚寒带针叶林
	Color("d8d8e8"),  # 6 极地苔原
	Color("b0b0c0"),  # 7 高山
]

## 气候档位名键（与 CLIMATE_ZONE_COLORS 下标一一对应，同后端 ClimateZone）
const CLIMATE_NAME_KEYS: Array = [
	"ui.map.climate_rainforest", "ui.map.climate_savanna", "ui.map.climate_desert",
	"ui.map.climate_grassland", "ui.map.climate_temperate_forest", "ui.map.climate_taiga",
	"ui.map.climate_tundra", "ui.map.climate_alpine",
]


# ── 悬停/地图状态（绘制前由 MapSetupStep 注入）──────────────

## 最新预览数据（elevation 等字段数组）
var preview: Dictionary = {}
## 鼠标悬停地图格（(-1,-1) = 不在地图上）
var hover_cell: Vector2i = Vector2i(-1, -1)
## 地图网格宽高（格）
var map_w: int = 0
var map_h: int = 0


## 单格着色：海域在地形视图保留深度渐变、气候视图统一深蓝
## （气候带仅陆地有意义），温度/降雨视图显示海域数据
## （后端 temperature 海域 = 海面温度，rainfall 为全域场）。
func cell_color(view: Dictionary, e: float, value: Variant) -> Color:
	if e <= 0.0:
		if view["key"] == "elevation":
			return elevation_color(e)
		if view["key"] == "climate":
			return SEA_COLOR
	return layer_color(view, e, value)


## 图层着色：地形按海拔；温度冷蓝→暖红；降雨干黄→湿蓝；气候 8 档固定色。
func layer_color(view: Dictionary, _elevation: float, value: Variant) -> Color:
	match view["key"]:
		"elevation":
			return elevation_color(_elevation)
		"temp":
			return temp_color(float(value))
		"rain":
			return rain_color(float(value))
		"climate":
			return CLIMATE_ZONE_COLORS[int(value) % CLIMATE_ZONE_COLORS.size()]
	return SEA_COLOR


func elevation_color(e: float) -> Color:
	if e <= 0.0:
		var depth: float = clampf(-e / 2000.0, 0.0, 1.0)
		return Color(0.08 + depth * 0.05, 0.12 + depth * 0.12, 0.32 + depth * 0.18)
	if e < 300.0:
		return Color(0.30, 0.48, 0.24)
	if e < 1000.0:
		return Color(0.42, 0.52, 0.28)
	if e < 2000.0:
		return Color(0.55, 0.50, 0.34)
	if e < 3500.0:
		return Color(0.48, 0.42, 0.40)
	return Color(0.92, 0.92, 0.94)


## 温度着色（年均温 °C）：冷蓝 → 绿 → 黄 → 红（分段线性渐变）。
func temp_color(t: float) -> Color:
	var stops: Array = [
		[-15.0, Color(0.45, 0.55, 0.90)],
		[-5.0, Color(0.55, 0.72, 0.95)],
		[5.0, Color(0.40, 0.75, 0.65)],
		[15.0, Color(0.75, 0.80, 0.40)],
		[25.0, Color(0.90, 0.60, 0.30)],
		[35.0, Color(0.85, 0.30, 0.25)],
	]
	return gradient_color(stops, t)


## 降雨着色（年降雨 mm）：干黄沙 → 浅绿 → 绿 → 深蓝（湿）。
func rain_color(r: float) -> Color:
	var stops: Array = [
		[0.0, Color(0.80, 0.72, 0.45)],
		[200.0, Color(0.75, 0.78, 0.45)],
		[500.0, Color(0.55, 0.75, 0.45)],
		[1000.0, Color(0.35, 0.68, 0.55)],
		[1600.0, Color(0.25, 0.55, 0.72)],
		[2400.0, Color(0.20, 0.35, 0.65)],
	]
	return gradient_color(stops, r)


## 分段线性渐变：stops = [[值, Color], ...]，值域外钳制到两端。
func gradient_color(stops: Array, value: float) -> Color:
	if value <= float(stops[0][0]):
		return stops[0][1]
	if value >= float(stops[stops.size() - 1][0]):
		return stops[stops.size() - 1][1]
	for i in stops.size() - 1:
		var lo_v: float = float(stops[i][0])
		var hi_v: float = float(stops[i + 1][0])
		if value >= lo_v and value <= hi_v:
			var t: float = (value - lo_v) / maxf(0.0001, hi_v - lo_v)
			return stops[i][1].lerp(stops[i + 1][1], t)
	return stops[0][1]


## 信息行：当前视图数值范围（气候视图为悬停提示）+ 鼠标位置值。
func info_line(view: Dictionary, field: Array) -> String:
	var parts: PackedStringArray = []
	match view["key"]:
		"elevation", "temp", "rain":
			var r: Array = field_range(field)
			parts.append(range_text(view, r))
		"climate":
			parts.append(TranslationServer.tr("ui.map.hover_climate_hint"))
	var hover: String = hover_value_text(view, field)
	if hover != "":
		if view["key"] == "climate":
			parts[0] = hover
		else:
			parts.append(hover)
	return "    ".join(parts)


## 视图数值范围文本（r = [lo, hi] 已取整）。
func range_text(view: Dictionary, r: Array) -> String:
	match view["key"]:
		"elevation":
			return TranslationServer.tr("ui.map.range_elevation").format({"min": r[0], "max": r[1]})
		"temp":
			return TranslationServer.tr("ui.map.range_temp").format({"min": r[0], "max": r[1]})
		"rain":
			return TranslationServer.tr("ui.map.range_rain").format({"min": r[0], "max": r[1]})
	return ""


## 字段数值范围 [lo, hi]（取整）；空字段返回 [0, 0]。
func field_range(field: Array) -> Array:
	if field.is_empty():
		return [0, 0]
	var lo: float = float(field[0])
	var hi: float = lo
	for v in field:
		var fv: float = float(v)
		if fv < lo:
			lo = fv
		elif fv > hi:
			hi = fv
	return [int(round(lo)), int(round(hi))]


## 鼠标悬停格的值文本；不在地图上返回 ""。
## 气候视图：海域显示海洋，陆地显示档位名；其余视图显示数值。
func hover_value_text(view: Dictionary, field: Array) -> String:
	if hover_cell.x < 0 or hover_cell.y < 0:
		return ""
	var idx: int = hover_cell.y * map_w + hover_cell.x
	if idx < 0 or idx >= field.size():
		return ""
	var v: float = float(field[idx])
	if view["key"] == "climate":
		var elev: Array = preview.get("elevation", [])
		if idx < elev.size() and float(elev[idx]) <= 0.0:
			return TranslationServer.tr("ui.map.pos_ocean")
		return TranslationServer.tr("ui.map.pos_climate").format({
			"name": TranslationServer.tr(
				CLIMATE_NAME_KEYS[int(v) % CLIMATE_NAME_KEYS.size()]),
		})
	match view["key"]:
		"elevation":
			return TranslationServer.tr("ui.map.pos_elevation").format({"value": int(round(v))})
		"temp":
			return TranslationServer.tr("ui.map.pos_temp").format({"value": int(round(v))})
		"rain":
			return TranslationServer.tr("ui.map.pos_rain").format({"value": int(round(v))})
	return ""
