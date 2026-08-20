"""Godot editor script: build terrain MeshLibrary from PNG textures.

Run this in Godot's Script Editor or via Tools → Run Script.
Generates frontend/assets/terrain/terrain_mesh_library.tres with
9 terrain types (BoxMesh + StandardMaterial3D with NEAREST filter).

Usage:
    1. Ensure textures exist at frontend/assets/terrain/textures/top_<name>.png
    2. In Godot editor: File → Run Script → select this file
    3. Alternatively via CLI: godot --headless --script this_file.gd
"""

extends EditorScript


func _run() -> void:
	var terrain_map := {
		1: "shallow_water",
		2: "sand",
		3: "plains",
		4: "hills",
		5: "rock",
		6: "mountain",
		7: "snow",
		8: "fertile",
		9: "underwater_floor",
	}

	var mesh_lib := MeshLibrary.new()

	for item_id in terrain_map.keys():
		var name: String = terrain_map[item_id]
		var tex_path := "res://assets/terrain/textures/top_" + name + ".png"
		var tex = ResourceLoader.load(tex_path, "", 0)
		if tex == null:
			print("FAIL load texture: ", tex_path)
			continue

		var mat := StandardMaterial3D.new()
		mat.albedo_texture = tex
		mat.texture_filter = BaseMaterial3D.TEXTURE_FILTER_NEAREST

		var box := BoxMesh.new()
		box.surface_set_material(0, mat)

		mesh_lib.create_item(item_id)
		mesh_lib.set_item_name(item_id, name)
		mesh_lib.set_item_mesh(item_id, box)

		print("OK item %d: %s" % [item_id, name])

	var save_path := "res://assets/terrain/terrain_mesh_library.tres"
	var err := ResourceSaver.save(mesh_lib, save_path)
	if err != OK:
		print("FAIL save: err=", err)
	else:
		print("Saved: ", save_path)
