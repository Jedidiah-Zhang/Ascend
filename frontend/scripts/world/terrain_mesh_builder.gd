"""生成 chunk 合并 ArrayMesh：每 terrain 类型一个 surface，本地坐标零精度丢失。
顶点色 AO：悬崖底部接触阴影 + 侧面变暗。
"""
class_name TerrainMeshBuilder
extends RefCounted

const Config = preload("res://scripts/config.gd")

const CHUNK_SIZE: int = Config.TILE_MAP_SIZE

## terrain_id → MeshLibrary item_id 映射（以 backend/ascend/space/terrain.py
## 的 TerrainType 为唯一事实源）：
##   0 GRASSLAND → 3 plains       1 SAND → 2 sand
##   2 FERTILE_SOIL → 8 fertile   3 ROCK → 5 rock
##   4 STEEP_SLOPE → 4 hills      5 MOUNTAIN_PEAK → 6 mountain
##   6 SHALLOW_WATER → 9 underwater_floor   7 DEEP_WATER → 9 underwater_floor
##   8 MARSH → 3 plains（无专用纹理，用草地近似，避免误用丘陵网格）
const TERRAIN_TO_MESH: PackedInt32Array = [3, 2, 8, 5, 4, 6, 9, 9, 3]

## 与后端 TerrainType 枚举对齐的水域 id（SHALLOW_WATER=6, DEEP_WATER=7）
const SHALLOW_WATER_ID: int = 6
const DEEP_WATER_ID: int = 7
## 低于该海拔的水域格不渲染（深水被水面覆盖，无需侧壁）
const WATER_FLOOR_CUTOFF: float = -50.0

const UV_BL := Vector2(0, 1)
const UV_BR := Vector2(1, 1)
const UV_TR := Vector2(1, 0)
const UV_TL := Vector2(0, 0)

const FACE := {
	top   = {"origin": Vector3(0, 1, 0), "u": Vector3(1, 0, 0), "v": Vector3(0, 0, 1), "n": Vector3.UP, "idx": [0, 1, 2, 3]},
	north = {"origin": Vector3(0, 0, 1), "u": Vector3(1, 0, 0), "v": Vector3(0, 1, 0), "n": Vector3(0, 0, 1), "idx": [0, 3, 2, 1]},
	south = {"origin": Vector3(0, 0, 0), "u": Vector3(1, 0, 0), "v": Vector3(0, 1, 0), "n": Vector3(0, 0, -1), "idx": [0, 1, 2, 3]},
	east  = {"origin": Vector3(1, 0, 0), "u": Vector3(0, 0, 1), "v": Vector3(0, 1, 0), "n": Vector3(1, 0, 0), "idx": [0, 1, 2, 3]},
	west  = {"origin": Vector3(0, 0, 0), "u": Vector3(0, 0, 1), "v": Vector3(0, 1, 0), "n": Vector3(-1, 0, 0), "idx": [0, 3, 2, 1]},
}

const AO_STRENGTH_PER_LEVEL: float = 0.12
const AO_TOP_MIN: float = 0.55
const AO_SIDE_FACTOR: float = 0.68


## 构建 chunk 合并 ArrayMesh：逐 tile 生成顶面（带 AO）与可见侧壁，按 item_id 分组 surface。
##
## Args:
##     terrain: 长度 CHUNK_SIZE² 的 terrain_id 数组（行优先，越界补 0）。
##     elevation: 长度 CHUNK_SIZE² 的海拔数组（行优先）。
##     materials: item_id → Material 材质表（key 决定 surface 分组与材质）。
##
## Returns:
##     合并后的 ArrayMesh（无可见面时 surface 数为 0）。
static func build(terrain: PackedInt32Array, elevation: PackedFloat32Array, materials: Dictionary) -> ArrayMesh:
	var mesh := ArrayMesh.new()
	var CS: int = CHUNK_SIZE

	var data: Dictionary = {}
	for item_id in materials:
		data[item_id] = _Collector.new()

	for z in CS:
		for x in CS:
			var idx := z * CS + x
			var terrain_id: int = terrain[idx] if idx < terrain.size() else 0
			if terrain_id < 0 or terrain_id >= TERRAIN_TO_MESH.size():
				continue
			var item_id: int = TERRAIN_TO_MESH[terrain_id]

			var elev: float = elevation[idx] if idx < elevation.size() else 0.0
			var is_water := (terrain_id == SHALLOW_WATER_ID or terrain_id == DEEP_WATER_ID)
			if not is_water and elev < 0.0:
				continue
			if is_water and elev < WATER_FLOOR_CUTOFF:
				continue

			var wy := roundi(elev)
			var c: _Collector = data.get(item_id)
			if c == null:
				continue  # 该 item_id 无材质/收集器（调用方表缺失）：跳过而非崩溃
			var b := Vector3(float(x), float(wy), float(z))

			var ao_top: Color = _compute_top_ao(x, z, wy, elevation, CS)
			c.add_quad(b, FACE.top, ao_top)

			var ao_side := Color(AO_SIDE_FACTOR, AO_SIDE_FACTOR, AO_SIDE_FACTOR)
			if _side_visible(x, z,  0,  1, wy, terrain, elevation, CS): c.add_quad(b, FACE.north, ao_side)
			if _side_visible(x, z,  0, -1, wy, terrain, elevation, CS): c.add_quad(b, FACE.south, ao_side)
			if _side_visible(x, z,  1,  0, wy, terrain, elevation, CS): c.add_quad(b, FACE.east, ao_side)
			if _side_visible(x, z, -1,  0, wy, terrain, elevation, CS): c.add_quad(b, FACE.west, ao_side)

	var surf := 0
	for item_id in data:
		var c: _Collector = data[item_id]
		if c.is_empty():
			continue

		var arrays: Array = []
		arrays.resize(Mesh.ARRAY_MAX)
		arrays[Mesh.ARRAY_VERTEX] = c.v
		arrays[Mesh.ARRAY_NORMAL] = c.n
		arrays[Mesh.ARRAY_TEX_UV] = c.u
		arrays[Mesh.ARRAY_COLOR] = c.c
		arrays[Mesh.ARRAY_INDEX] = c.i

		mesh.add_surface_from_arrays(Mesh.PRIMITIVE_TRIANGLES, arrays)
		mesh.surface_set_material(surf, materials[item_id])
		surf += 1

	return mesh


## 计算顶面 AO：四邻海拔高于本格时按高度差（上限 3 级）逐级削弱亮度。
##
## Args:
##     x/z: 格内 tile 坐标；wy: 本格海拔（四舍五入）。
##     elevation: chunk 高程数组；CS: chunk 边长。
##
## Returns:
##     灰阶 Color（各通道一致，下限 AO_TOP_MIN）。
static func _compute_top_ao(x: int, z: int, wy: int, elevation: PackedFloat32Array, CS: int) -> Color:
	var ao: float = 1.0
	for d in [[0, 1], [0, -1], [1, 0], [-1, 0]]:
		var nx: int = x + int(d[0])
		var nz: int = z + int(d[1])
		if nx < 0 or nx >= CS or nz < 0 or nz >= CS:
			continue
		var nidx: int = nz * CS + nx
		if nidx >= elevation.size():
			continue
		var ne: float = elevation[nidx]
		var diff: int = roundi(ne) - wy
		if diff > 0:
			ao -= minf(float(diff), 3.0) * AO_STRENGTH_PER_LEVEL
	ao = maxf(ao, AO_TOP_MIN)
	return Color(ao, ao, ao)


## 判断侧壁是否可见：邻居越界或不可渲染（非法 id/低于水面遮罩）恒可见，否则看高度差。
##
## Args:
##     x/z: 当前 tile 坐标；dx/dz: 邻居方向。
##     wy: 当前格海拔（四舍五入）；terrain/elevation: chunk 数据数组；CS: chunk 边长。
##
## Returns:
##     与邻居存在高度差时为 true（需渲染侧壁）。
static func _side_visible(x: int, z: int, dx: int, dz: int, wy: int,
		terrain: PackedInt32Array, elevation: PackedFloat32Array, CS: int) -> bool:
	var nx := x + dx
	var nz := z + dz

	# chunk 边界 — 无法查邻居，始终渲染
	if nx < 0 or nx >= CS or nz < 0 or nz >= CS:
		return true

	var nidx := nz * CS + nx
	var ntid: int = terrain[nidx] if nidx < terrain.size() else 0
	if ntid < 0 or ntid >= TERRAIN_TO_MESH.size():
		return true

	var n_water := (ntid == SHALLOW_WATER_ID or ntid == DEEP_WATER_ID)
	var n_elev: float = elevation[nidx] if nidx < elevation.size() else 0.0
	if not n_water and n_elev < 0.0:
		return true
	if n_water and n_elev < WATER_FLOOR_CUTOFF:
		return true

	return wy != roundi(n_elev)


class _Collector:
	var v: PackedVector3Array
	var n: PackedVector3Array
	var u: PackedVector2Array
	var c: PackedColorArray
	var i: PackedInt32Array

	## 初始化顶点缓冲（顶点/法线/UV/颜色/索引为空数组）。
	func _init() -> void:
		v = PackedVector3Array()
		n = PackedVector3Array()
		u = PackedVector2Array()
		c = PackedColorArray()
		i = PackedInt32Array()

	## 顶点缓冲是否为空（无任何面）。
	func is_empty() -> bool:
		return v.is_empty()

	## 追加一个四边形面（4 顶点 + 6 索引）：按面定义展开顶点，写入统一法线/UV 与顶点色。
	##
	## Args:
	##     base: tile 基准点（面原点相对它偏移）。
	##     f: 面定义字典（origin/u/v/n/idx）。
	##     color: 顶点色（默认白 = 无 AO 调制）。
	func add_quad(base: Vector3, f: Dictionary, color: Color = Color.WHITE) -> void:
		var vi := v.size()
		var o: Vector3 = base + f.origin
		var du: Vector3 = f.u
		var dv: Vector3 = f.v
		var nn: Vector3 = f.n

		v.append(o)
		v.append(o + du)
		v.append(o + du + dv)
		v.append(o + dv)

		n.append(nn)
		n.append(nn)
		n.append(nn)
		n.append(nn)

		u.append(UV_BL)
		u.append(UV_BR)
		u.append(UV_TR)
		u.append(UV_TL)

		c.append(color)
		c.append(color)
		c.append(color)
		c.append(color)

		var idx: Array = f.idx
		i.append(vi + idx[0])
		i.append(vi + idx[1])
		i.append(vi + idx[2])
		i.append(vi + idx[0])
		i.append(vi + idx[2])
		i.append(vi + idx[3])
