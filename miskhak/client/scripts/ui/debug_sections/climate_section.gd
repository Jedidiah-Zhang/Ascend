"""气候分区 — 显示年均基线（温度/湿度）与气候带。

仅在玩家移动到新 tile 时查询世界脚本，避免每帧字典遍历。
实时温度/湿度看天气分区（get_weather 轮询）。
"""

class_name ClimateSection
extends "res://scripts/ui/debug_sections/tile_polling_section.gd"


## 气候带 → 翻译键（顺序与后端 ClimateZone 编码一致；文案见 lang/*.json ui.map.climate_*）
const CLIMATE_LABEL_KEYS: Array[String] = [
	"ui.map.climate_rainforest", "ui.map.climate_savanna", "ui.map.climate_desert",
	"ui.map.climate_grassland", "ui.map.climate_temperate_forest",
	"ui.map.climate_taiga", "ui.map.climate_tundra", "ui.map.climate_alpine",
]


## 温度（摄氏度）
var temperature: float = 0.0

## 是否已收到温度数据
var _has_temp: bool = false

## 湿度（%）
var humidity: float = 0.0

## 是否已收到湿度数据
var _has_humidity: bool = false

## 气候带编码，-1 表示未知
var climate_zone: int = -1


## 构造函数：设置分区标签翻译键。
func _init() -> void:
	label_key = "debug.section.climate"


## 查询当前 tile 的基线气候数据：从世界脚本 get_debug_climate_at 拉取
## 年均温度/年均湿度/气候带，逐字段刷新并标记已收到；缺任一字段则返回未就绪。
##
## Args:
##     world_pos: 玩家当前世界坐标（跨格后才调用）。
##
## Returns:
##     三个字段（温度/湿度/气候带）全部收到返回 true。
func _poll(world_pos: Vector2) -> bool:
	if not _world.has_method("get_debug_climate_at"):
		return false
	var all_received: bool = true
	var climate_data: Dictionary = _world.get_debug_climate_at(world_pos)
	if climate_data.has("temperature"):
		temperature = climate_data["temperature"]
		_has_temp = true
	else:
		all_received = false
	if climate_data.has("humidity"):
		humidity = climate_data["humidity"]
		_has_humidity = true
	else:
		all_received = false
	if climate_data.has("climate_zone"):
		climate_zone = climate_data["climate_zone"]
	else:
		all_received = false
	return all_received


## 生成气候分区文本行：年均温/年均湿度与气候带名称
## （未收到的字段显示"—"，气候带编码越界同样显示"—"）。
##
## Returns:
##     两行 PackedStringArray（温湿度行 + 气候带行）。
func get_lines() -> PackedStringArray:
	var temp_str: String = "%.1f°C" % temperature if _has_temp else "—"
	var humid_str: String = "%.0f%%" % humidity if _has_humidity else "—"
	var zone_str: String = TranslationServer.tr(
		CLIMATE_LABEL_KEYS[climate_zone]) if climate_zone >= 0 and climate_zone < CLIMATE_LABEL_KEYS.size() else "—"
	return PackedStringArray([
		TranslationServer.tr("debug.climate_summary").format({"temp": temp_str, "hum": humid_str}),
		TranslationServer.tr("debug.climate_zone").format({"name": zone_str}),
	])
