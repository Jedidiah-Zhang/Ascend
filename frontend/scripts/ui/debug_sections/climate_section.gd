"""气候分区 — 显示年均基线（温度/湿度）与气候带。

仅在玩家移动到新 tile 时查询世界脚本，避免每帧字典遍历。
实时温度/湿度看天气分区（get_weather 轮询）。
"""

class_name ClimateSection
extends "res://scripts/ui/debug_section.gd"


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

var _world: Node = null
var _last_tile_pos: Vector2i = Vector2i(-999999, -999999)


func _init() -> void:
	label = "气候"


func setup(world: Node) -> void:
	_world = world


func process_section(_delta: float) -> void:
	if _world == null or not _world.has_method("get_debug_climate_at"):
		return
	var player_info: Dictionary = _world.get_debug_player_info()
	var world_pos: Vector2 = player_info.get("world_pos", Vector2.ZERO)
	var tile_pos := Vector2i(int(world_pos.x), int(world_pos.y))
	if tile_pos == _last_tile_pos:
		return

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

	if all_received:
		_last_tile_pos = tile_pos


func get_lines() -> PackedStringArray:
	var temp_str := "%.1f°C" % temperature if _has_temp else "—"
	var humid_str := "%.0f%%" % humidity if _has_humidity else "—"
	var zone_str := CLIMATE_LABELS[climate_zone] if climate_zone >= 0 and climate_zone < CLIMATE_LABELS.size() else "—"
	return PackedStringArray([
		"年均温: %s  |  年均湿度: %s" % [temp_str, humid_str],
		"气候: %s" % zone_str,
	])
