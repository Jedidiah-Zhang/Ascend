"""气候分区 — 显示年均基线（温度/湿度）与气候带。

仅在玩家移动到新 tile 时查询世界脚本，避免每帧字典遍历。
实时温度/湿度看天气分区（get_weather 轮询）。
"""

class_name ClimateSection
extends "res://scripts/ui/debug_sections/tile_polling_section.gd"


const CLIMATE_LABELS: Array[String] = [
	"热带雨林", "热带草原", "沙漠", "草原",
	"温带森林", "亚寒带针叶林", "极地苔原", "高山",
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


## 构造函数：设置分区标签为"气候"。
func _init() -> void:
	label = "气候"


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
	var temp_str := "%.1f°C" % temperature if _has_temp else "—"
	var humid_str := "%.0f%%" % humidity if _has_humidity else "—"
	var zone_str := CLIMATE_LABELS[climate_zone] if climate_zone >= 0 and climate_zone < CLIMATE_LABELS.size() else "—"
	return PackedStringArray([
		"年均温: %s  |  年均湿度: %s" % [temp_str, humid_str],
		"气候: %s" % zone_str,
	])
