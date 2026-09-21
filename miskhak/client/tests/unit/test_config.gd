extends GutTest



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


func test_2d_camera_zoom_range_valid() -> void:
	assert_gt(Config.TILE_PIXEL_SIZE, 0, "TILE_PIXEL_SIZE 必须 > 0")
	assert_lte(Config.CAMERA_ZOOM_MIN, Config.CAMERA_ZOOM_DEFAULT,
		"ZOOM_MIN 必须 <= ZOOM_DEFAULT")
	assert_lte(Config.CAMERA_ZOOM_DEFAULT, Config.CAMERA_ZOOM_MAX,
		"ZOOM_DEFAULT 必须 <= ZOOM_MAX")
	assert_gt(Config.CAMERA_ZOOM_STEP, 1.0, "缩放步长应为放大倍率（> 1）")


func test_terminal_limits_reasonable() -> void:
	assert_gt(Config.TERMINAL_OUTPUT_LINE_LIMIT, 0)
	assert_gt(Config.TERMINAL_HISTORY_LIMIT, 0)
