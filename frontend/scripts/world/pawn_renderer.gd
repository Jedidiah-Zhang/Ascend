"""实体 pawn 渲染器 — 按体型规格生成分层 Sprite2D 部件列表（纯逻辑）。

形态由"体型规格（body spec）"驱动：规格 = 部件槽位（头/躯干/附肢/变异层/
装备层）× 各槽位的部件（矩形色块占位，像素风）。后端 genome/body grammar
定型前使用默认物种规格（CREATURE 人形 / PLANT 植物 / 其余建筑石块）；
基因外观差异后续仅需替换 spec 的颜色与部件即可，渲染路径不变
（视觉风格设计文档：部件按槽位拆分拼接、基因变异层叠加）。

本类只做数据：build_parts 输出确定性的部件字典列表（尺寸/偏移/颜色/叠层），
main_world 负责实例化 Sprite2D 节点。无场景依赖，可纯逻辑单测。

部件坐标约定：节点原点 = 脚底中心（pawn 落地面），y 轴向上；
offsets 为整数像素偏移。facing_left 时水平镜像偏移（实体朝向反转，
左右附肢随之换位——solid 色块无需逐部件翻转纹理）。
"""

class_name PawnRenderer
extends RefCounted

## 部件槽位（SLOT_ORDER 列表序即渲染序，后渲染者在上）
const SLOT_HEAD: String = "head"
const SLOT_BODY: String = "body"
const SLOT_LIMB_LEFT: String = "limb_left"
const SLOT_LIMB_RIGHT: String = "limb_right"
const SLOT_VARIANT: String = "variant"  # 基因变异层（盖在基础体之上）
const SLOT_EQUIP: String = "equip"      # 装备层（最顶层）

const SLOT_ORDER: Array[String] = [
	SLOT_HEAD, SLOT_BODY, SLOT_LIMB_LEFT, SLOT_LIMB_RIGHT,
	SLOT_VARIANT, SLOT_EQUIP,
]

## 部件字典键
const PART_NAME: String = "name"
const PART_SLOT: String = "slot"
const PART_OFFSET: String = "offset"   # Vector2i，y 向上
const PART_SIZE: String = "size"       # Vector2i
const PART_COLOR: String = "color"     # Color
const PART_Z: String = "z"             # int，叠层

## 规格字典键
const SPEC_TYPE: String = "entity_type"
const SPEC_WIDTH: String = "width"     # 包围盒宽（px，镜像基准）
const SPEC_SLOTS: String = "slots"     # {slot: [part, ...]}
const SPEC_NAMEPLATE: String = "nameplate_offset"  # Vector2（头顶浮层锚点）


## 默认物种规格：CREATURE 人形 16×24（占位：头/躯干/左右附肢，色板沿用旧
## 玩家占位纹理），PLANT 14×20（冠/茎），其余（STRUCTURE/未知）石块 14×14。
static func default_spec(entity_type: String) -> Dictionary:
	match entity_type:
		"CREATURE":
			return {
				SPEC_TYPE: "CREATURE",
				SPEC_WIDTH: 16,
				SPEC_NAMEPLATE: Vector2(8, -26),
				SPEC_SLOTS: {
					SLOT_HEAD: [{
						PART_NAME: "head", PART_OFFSET: Vector2i(5, 16),
						PART_SIZE: Vector2i(6, 6),
						PART_COLOR: Color(0.95, 0.8, 0.65, 1.0),
					}],
					SLOT_BODY: [{
						PART_NAME: "body", PART_OFFSET: Vector2i(3, 2),
						PART_SIZE: Vector2i(10, 14),
						PART_COLOR: Color(0.9, 0.3, 0.3, 1.0),
					}],
					SLOT_LIMB_LEFT: [{
						PART_NAME: "limb_left", PART_OFFSET: Vector2i(1, 0),
						PART_SIZE: Vector2i(3, 12),
						PART_COLOR: Color(0.7, 0.2, 0.2, 1.0),
					}],
					SLOT_LIMB_RIGHT: [{
						PART_NAME: "limb_right", PART_OFFSET: Vector2i(12, 0),
						PART_SIZE: Vector2i(3, 12),
						PART_COLOR: Color(0.7, 0.2, 0.2, 1.0),
					}],
				},
			}
		"PLANT":
			return {
				SPEC_TYPE: "PLANT",
				SPEC_WIDTH: 14,
				SPEC_NAMEPLATE: Vector2(7, -22),
				SPEC_SLOTS: {
					SLOT_HEAD: [{
						PART_NAME: "crown", PART_OFFSET: Vector2i(2, 10),
						PART_SIZE: Vector2i(10, 8),
						PART_COLOR: Color(0.25, 0.55, 0.25, 1.0),
					}],
					SLOT_BODY: [{
						PART_NAME: "stem", PART_OFFSET: Vector2i(6, 0),
						PART_SIZE: Vector2i(2, 10),
						PART_COLOR: Color(0.5, 0.35, 0.2, 1.0),
					}],
				},
			}
		_:
			return {
				SPEC_TYPE: entity_type,
				SPEC_WIDTH: 14,
				SPEC_NAMEPLATE: Vector2(7, -16),
				SPEC_SLOTS: {
					SLOT_BODY: [{
						PART_NAME: "block", PART_OFFSET: Vector2i(0, 0),
						PART_SIZE: Vector2i(14, 14),
						PART_COLOR: Color(0.6, 0.6, 0.62, 1.0),
					}],
				},
			}


## 由规格产出部件列表（确定性顺序：SLOT_ORDER 槽位序，槽内按数组序）。
## facing_left 时部件 x 偏移镜像（对称色块，左右附肢随镜像换位）。
static func build_parts(spec: Dictionary, facing_left: bool = false) -> Array:
	var width: int = int(spec.get(SPEC_WIDTH, 14))
	var parts: Array = []
	var z: int = 0
	for slot in SLOT_ORDER:
		for part in spec.get(SPEC_SLOTS, {}).get(slot, []):
			var out: Dictionary = {
				PART_NAME: part[PART_NAME],
				PART_SLOT: slot,
				PART_OFFSET: part[PART_OFFSET],
				PART_SIZE: part[PART_SIZE],
				PART_COLOR: part[PART_COLOR],
				PART_Z: z,
			}
			if facing_left:
				var off: Vector2i = part[PART_OFFSET]
				var size: Vector2i = part[PART_SIZE]
				out[PART_OFFSET] = Vector2i(width - off.x - size.x, off.y)
			parts.append(out)
			z += 1
	return parts


## 部件占位纹理缓存：{size_x}_{size_y}_{color8 键} -> ImageTexture。
## 朝向切换重建部件时避免重复生成色块（实体少、变向频繁）。
static var _texture_cache: Dictionary = {}


## 生成部件占位纹理：size×size 纯色块（1:1 像素，像素风；无资源依赖）。
static func make_part_texture(size: Vector2i, color: Color) -> ImageTexture:
	var key: String = "%d_%d_%s" % [size.x, size.y, color.to_html(false)]
	if _texture_cache.has(key):
		return _texture_cache[key]
	var img := Image.create(size.x, size.y, false, Image.FORMAT_RGBA8)
	img.fill(color)
	var tex := ImageTexture.create_from_image(img)
	_texture_cache[key] = tex
	return tex


## 名称浮层锚点（规格缺失时给默认值）。
static func nameplate_offset(spec: Dictionary) -> Vector2:
	return spec.get(SPEC_NAMEPLATE, Vector2(0, -16))