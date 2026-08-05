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


static func build(terrain: Array, elevation: Array, materials: Dictionary) -> ArrayMesh:
	var mesh := ArrayMesh.new()
	var CS: int = CHUNK_SIZE

	var data: Dictionary = {}
	for item_id in materials:
		data[item_id] = _Collector.new()

	for z in CS:
		for x in CS:
			var idx := z * CS + x
			var terrain_id: int = int(terrain[idx]) if idx < terrain.size() else 0
			if terrain_id < 0 or terrain_id >= TERRAIN_TO_MESH.size():
				continue
			var item_id: int = TERRAIN_TO_MESH[terrain_id]

			var elev: float = float(elevation[idx])
			var is_water := (terrain_id == SHALLOW_WATER_ID or terrain_id == DEEP_WATER_ID)
			if not is_water and elev < 0.0:
				continue
			if is_water and elev < WATER_FLOOR_CUTOFF:
				continue

			var wy := roundi(elev)
			var c: _Collector = data[item_id]
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


static func _compute_top_ao(x: int, z: int, wy: int, elevation: Array, CS: int) -> Color:
	var ao: float = 1.0
	for d in [[0, 1], [0, -1], [1, 0], [-1, 0]]:
		var nx: int = x + int(d[0])
		var nz: int = z + int(d[1])
		if nx < 0 or nx >= CS or nz < 0 or nz >= CS:
			continue
		var nidx: int = nz * CS + nx
		if nidx >= elevation.size():
			continue
		var ne: float = float(elevation[nidx])
		var diff: int = roundi(ne) - wy
		if diff > 0:
			ao -= minf(float(diff), 3.0) * AO_STRENGTH_PER_LEVEL
	ao = maxf(ao, AO_TOP_MIN)
	return Color(ao, ao, ao)


static func _side_visible(x: int, z: int, dx: int, dz: int, wy: int,
		terrain: Array, elevation: Array, CS: int) -> bool:
	var nx := x + dx
	var nz := z + dz

	# chunk 边界 — 无法查邻居，始终渲染
	if nx < 0 or nx >= CS or nz < 0 or nz >= CS:
		return true

	var nidx := nz * CS + nx
	var ntid: int = int(terrain[nidx]) if nidx < terrain.size() else 0
	if ntid < 0 or ntid >= TERRAIN_TO_MESH.size():
		return true

	var n_water := (ntid == SHALLOW_WATER_ID or ntid == DEEP_WATER_ID)
	var n_elev: float = float(elevation[nidx]) if nidx < elevation.size() else 0.0
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

	func _init() -> void:
		v = PackedVector3Array()
		n = PackedVector3Array()
		u = PackedVector2Array()
		c = PackedColorArray()
		i = PackedInt32Array()

	func is_empty() -> bool:
		return v.is_empty()

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
