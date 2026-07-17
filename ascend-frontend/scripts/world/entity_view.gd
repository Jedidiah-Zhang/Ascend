"""实体视图 — 单个实体的渲染节点（Issue #20）。

哑视图组件：只负责外观与元数据，不含任何决策/预测逻辑。
位置由 EntityLayer 统一驱动（本地控制实体由 MapDisplay 预测驱动）。

占位外观：按实体类型着色的等距菱形标记，待美术资源就绪后替换为
Sprite2D/AnimatedSprite2D。
"""
extends Node2D

class_name EntityView

## 实体类型 → 占位颜色
const TYPE_COLORS: Dictionary = {
	"CREATURE": Color(0.85, 0.30, 0.25),
	"PLANT": Color(0.30, 0.65, 0.30),
	"ITEM": Color(0.90, 0.75, 0.25),
	"STRUCTURE": Color(0.55, 0.55, 0.60),
}

## 玩家控制实体的高亮色
const PLAYER_COLOR: Color = Color(0.95, 0.95, 1.0)

## 菱形半宽/半高（等距 tile 走向，与 32x16 地砖比例一致）
const HALF_W: float = 7.0
const HALF_H: float = 3.5
## 立柱高度（让标记有一点体积感）
const PILLAR_H: float = 10.0

var entity_id: String = ""
var entity_type: String = ""
var controller: String = "NONE"

var _color: Color = Color.WHITE


func setup(id: String, type_name: String, controller_name: String) -> void:
	"""初始化元数据与外观。

	Args:
		id: 实体 ID（后端 UUID hex）。
		type_name: 实体类型名（CREATURE/PLANT/ITEM/STRUCTURE）。
		controller_name: 控制者名（NONE/AI/PLAYER）。
	"""
	entity_id = id
	entity_type = type_name
	controller = controller_name
	_color = PLAYER_COLOR if controller == "PLAYER" \
		else TYPE_COLORS.get(type_name, Color.MAGENTA)
	queue_redraw()


func is_player_controlled() -> bool:
	"""是否为玩家控制的实体。"""
	return controller == "PLAYER"


func _draw() -> void:
	"""绘制占位标记：底面菱形 + 左右侧面 + 顶面菱形。

	侧面拆为两个凸四边形——单个六顶点多边形在顶面下顶点处
	是凹的，凹多边形填充依赖三角剖分实现，凸形最稳。
	"""
	var top_y: float = -PILLAR_H
	var base := PackedVector2Array([
		Vector2(0, -HALF_H), Vector2(HALF_W, 0),
		Vector2(0, HALF_H), Vector2(-HALF_W, 0),
	])
	var top := PackedVector2Array([
		Vector2(0, top_y - HALF_H), Vector2(HALF_W, top_y),
		Vector2(0, top_y + HALF_H), Vector2(-HALF_W, top_y),
	])
	var side_left := PackedVector2Array([
		Vector2(-HALF_W, 0), Vector2(0, HALF_H),
		Vector2(0, top_y + HALF_H), Vector2(-HALF_W, top_y),
	])
	var side_right := PackedVector2Array([
		Vector2(0, HALF_H), Vector2(HALF_W, 0),
		Vector2(HALF_W, top_y), Vector2(0, top_y + HALF_H),
	])
	draw_colored_polygon(base, _color.darkened(0.45))
	draw_colored_polygon(side_left, _color.darkened(0.3))
	draw_colored_polygon(side_right, _color.darkened(0.2))
	draw_colored_polygon(top, _color)
	draw_polyline(top + PackedVector2Array([top[0]]), _color.darkened(0.6), 1.0)
