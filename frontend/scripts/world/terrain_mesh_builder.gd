"""生成 chunk 合并 ArrayMesh：每 terrain 类型一个 surface，本地坐标零精度丢失。
"""
class_name TerrainMeshBuilder
extends RefCounted

const Config = preload("res://scripts/config.gd")

const CHUNK_SIZE: int = Config.TILE_MAP_SIZE

# 后端 TerrainType (int) → MeshLibrary item_id
const TERRAIN_TO_MESH: PackedInt32Array = [3, 2, 8, 5, 4, 6, 9, 9, 4]

const UV_BL := Vector2(0, 1)
const UV_BR := Vector2(1, 1)
const UV_TR := Vector2(1, 0)
const UV_TL := Vector2(0, 0)

# 每个面定义: origin + u/v 方向矢量 + normal + 顶点索引偏移
# Vulkan Y-flip → 世界 CW = front face → front 叉积 = -normal
const FACE := {
	top   = {"origin": Vector3(0, 1, 0), "u": Vector3(1, 0, 0), "v": Vector3(0, 0, 1), "n": Vector3.UP, "idx": [0, 1, 2, 3]},
	north = {"origin": Vector3(0, 0, 1), "u": Vector3(1, 0, 0), "v": Vector3(0, 1, 0), "n": Vector3(0, 0, 1), "idx": [0, 3, 2, 1]},
	south = {"origin": Vector3(0, 0, 0), "u": Vector3(1, 0, 0), "v": Vector3(0, 1, 0), "n": Vector3(0, 0, -1), "idx": [0, 1, 2, 3]},
	east  = {"origin": Vector3(1, 0, 0), "u": Vector3(0, 0, 1), "v": Vector3(0, 1, 0), "n": Vector3(1, 0, 0), "idx": [0, 1, 2, 3]},
	west  = {"origin": Vector3(0, 0, 0), "u": Vector3(0, 0, 1), "v": Vector3(0, 1, 0), "n": Vector3(-1, 0, 0), "idx": [0, 3, 2, 1]},
}


static func build(terrain: Array, elevation: Array, materials: Dictionary) -> ArrayMesh:
	"""从 chunk 地形数据生成合并 ArrayMesh。
	materials: Dictionary[item_id → Material]
	返回的 ArrayMesh 每个 surface 对应一种出现的 terrain 材质，
	所有顶点在 chunk 本地空间 (0..CHUNK_SIZE-1)。
	"""
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
			var is_water := (terrain_id == 6 or terrain_id == 7)
			if not is_water and elev < 0.0:
				continue
			if is_water and elev < -50.0:
				continue

			var wy := roundi(elev)
			var c: _Collector = data[item_id]
			var b := Vector3(float(x), float(wy), float(z))

			c.add_quad(b, FACE.top)

			if _side_visible(x, z,  0,  1, wy, terrain, elevation, CS): c.add_quad(b, FACE.north)
			if _side_visible(x, z,  0, -1, wy, terrain, elevation, CS): c.add_quad(b, FACE.south)
			if _side_visible(x, z,  1,  0, wy, terrain, elevation, CS): c.add_quad(b, FACE.east)
			if _side_visible(x, z, -1,  0, wy, terrain, elevation, CS): c.add_quad(b, FACE.west)

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
		arrays[Mesh.ARRAY_INDEX] = c.i

		mesh.add_surface_from_arrays(Mesh.PRIMITIVE_TRIANGLES, arrays)
		mesh.surface_set_material(surf, materials[item_id])
		surf += 1

	return mesh


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

	var n_water := (ntid == 6 or ntid == 7)
	var n_elev: float = float(elevation[nidx]) if nidx < elevation.size() else 0.0
	if not n_water and n_elev < 0.0:
		return true
	if n_water and n_elev < -50.0:
		return true

	return wy != roundi(n_elev)


class _Collector:
	var v: PackedVector3Array
	var n: PackedVector3Array
	var u: PackedVector2Array
	var i: PackedInt32Array

	func _init() -> void:
		v = PackedVector3Array()
		n = PackedVector3Array()
		u = PackedVector2Array()
		i = PackedInt32Array()

	func is_empty() -> bool:
		return v.is_empty()

	func add_quad(base: Vector3, f: Dictionary) -> void:
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

		var idx: Array = f.idx
		i.append(vi + idx[0])
		i.append(vi + idx[1])
		i.append(vi + idx[2])
		i.append(vi + idx[0])
		i.append(vi + idx[2])
		i.append(vi + idx[3])
