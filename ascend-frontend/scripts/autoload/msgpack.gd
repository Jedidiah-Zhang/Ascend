"""序列化工具 — 当前使用 JSON，预留 MessagePack 接口。

演进路径: JSON (调试期) → MessagePack (正式期)
切换时只需替换 encode/decode 实现，调用方无需修改。
"""

class_name MsgPack
extends RefCounted


static func encode(value: Variant) -> PackedByteArray:
	"""编码为传输格式。当前使用 JSON 以便调试。

	Args:
		value: 任意可序列化的 Variant

	Returns:
		编码后的字节数组
	"""
	var json_str: String = JSON.stringify(value)
	if json_str == "":
		push_error("MsgPack: JSON encode failed for value: %s" % str(value))
		return PackedByteArray()
	return json_str.to_utf8_buffer()


static func decode(data: PackedByteArray) -> Variant:
	"""从传输格式解码。当前使用 JSON。

	Args:
		data: 编码的字节数组

	Returns:
		解码后的 Variant，解码失败返回 null
	"""
	var json_str: String = data.get_string_from_utf8()
	if json_str == "":
		return null
	var result = JSON.parse_string(json_str)
	if result == null:
		push_error("MsgPack: JSON decode failed for: %s" % json_str.left(200))
	return result
