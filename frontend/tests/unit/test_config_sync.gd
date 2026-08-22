extends GutTest
## 前后端共享常量一致性（CI 层）：读后端 Python 源文件文本比对，
## 防止任一端单独改动协议/端口/消息大小导致握手失败或断连。



func _backend_source(rel: String) -> String:
	var abs: String = ProjectSettings.globalize_path("res://..").path_join("backend").path_join(rel)
	var f := FileAccess.open(abs, FileAccess.READ)
	if f == null:
		push_error("config_sync: backend file missing: %s" % abs)
		return ""
	return f.get_as_text()


func test_protocol_version_synced() -> void:
	"""Config.PROTOCOL_VERSION 与 backend/ascend/net/protocol.py 一致。"""
	var src := _backend_source("ascend/net/protocol.py")
	assert_string_contains(src, "PROTOCOL_VERSION: int = 0x%02X" % Config.PROTOCOL_VERSION,
		"协议版本漂移会导致握手直接断开（见 client_handler）")


func test_default_port_synced() -> void:
	"""Config.DEFAULT_PORT 与 server.py 默认端口一致。"""
	var src := _backend_source("ascend/net/server.py")
	assert_string_contains(src, "port: int = %d" % Config.DEFAULT_PORT,
		"端口漂移导致前端永远连不上后端")


func test_default_host_synced() -> void:
	"""Config.DEFAULT_HOST 与后端 SERVER_HOST 一致（均为 127.0.0.1）。"""
	var src := _backend_source("ascend/config.py")
	assert_string_contains(src, "SERVER_HOST: str = \"%s\"" % Config.DEFAULT_HOST,
		"主机地址漂移导致前端连错机器")


func test_max_message_size_synced() -> void:
	"""前后端最大消息长度均为 16 MiB（文本表达式比对，防任一端漂移）。"""
	var py := _backend_source("ascend/config.py")
	assert_eq(Config.MAX_MESSAGE_SIZE, 16 * 1024 * 1024, "前端 MAX_MESSAGE_SIZE 应为 16 MiB")
	assert_string_contains(py, "MAX_MESSAGE_SIZE: int = 16 * 1024 * 1024",
		"后端 MAX_MESSAGE_SIZE 表达式漂移")
