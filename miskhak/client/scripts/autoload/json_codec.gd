"""序列化工具 — 传输帧体编解码，使用 JSON。

JSON 可读性好，便于调试期直接检查帧内容。
"""

extends RefCounted
class_name JsonCodec


static func encode(value: Variant) -> PackedByteArray:
	"""编码为传输格式（JSON，便于调试期排查）。

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
	"""从传输格式解码（JSON）。

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
