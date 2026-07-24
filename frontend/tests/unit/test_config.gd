extends GutTest

const Config = preload("res://scripts/config.gd")


func test_default_host_is_localhost() -> void:
	assert_eq(Config.DEFAULT_HOST, "127.0.0.1",
		"服务器默认地址应为 127.0.0.1")


func test_default_port_in_valid_range() -> void:
	assert_between(Config.DEFAULT_PORT, 1, 65535,
		"端口号 %s 不在有效范围 [1, 65535]" % Config.DEFAULT_PORT)


func test_tile_map_size_positive() -> void:
	assert_gt(Config.TILE_MAP_SIZE, 0,
		"TILE_MAP_SIZE 必须 > 0")



func test_message_size_reasonable() -> void:
	assert_gt(Config.MAX_MESSAGE_SIZE, 1024,
		"MAX_MESSAGE_SIZE 必须 >= 1KB")
	assert_lte(Config.MAX_MESSAGE_SIZE, 512 * 1024 * 1024,
		"MAX_MESSAGE_SIZE 不应超过 512MB")


func test_3d_camera_distance_range_valid() -> void:
	assert_lte(Config.CAMERA_3D_DISTANCE_MIN, Config.CAMERA_3D_DISTANCE_DEFAULT,
		"DISTANCE_MIN 必须 <= DISTANCE_DEFAULT")
	assert_lte(Config.CAMERA_3D_DISTANCE_DEFAULT, Config.CAMERA_3D_DISTANCE_MAX,
		"DISTANCE_DEFAULT 必须 <= DISTANCE_MAX")


func test_terminal_limits_reasonable() -> void:
	assert_gt(Config.TERMINAL_OUTPUT_LINE_LIMIT, 0)
	assert_gt(Config.TERMINAL_HISTORY_LIMIT, 0)
