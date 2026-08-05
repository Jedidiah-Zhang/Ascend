"""序列化工具 — 调试期使用 JSON 作为传输编码。

演进路径: JSON (调试期) → MessagePack (正式期)
切换时新建 MessagePack 编解码类并替换调用点，勿在本类上改名复用。
"""

extends RefCounted
class_name JsonCodec


static func encode(value: Variant) -> PackedByteArray:
	"""编码为传输格式。当前使用 JSON 以便调试。

	Args:
		value: 任意可序列化的 Variant

	Returns:
		编码后的字节数组
	"""
	var json_str: String = JSON.stringify(value)
	if json_str == "":
		push_error("JsonCodec: JSON encode failed for value: %s" % str(value))
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
		push_error("JsonCodec: JSON decode failed for: %s" % json_str.left(200))
	return result
