extends GutTest


func test_encode_decode_roundtrip_dict() -> void:
	var original: Dictionary = {"type": "request", "seq": 1.0, "payload": {"x": 10.0}}
	var encoded: PackedByteArray = MsgPack.encode(original)
	var decoded: Variant = MsgPack.decode(encoded)
	assert_eq_deep(decoded, original)


func test_encode_decode_roundtrip_array() -> void:
	var original: Array = [1.0, "hello", 3.14, true, null]
	var encoded: PackedByteArray = MsgPack.encode(original)
	var decoded: Variant = MsgPack.decode(encoded)
	assert_eq_deep(decoded, original)


func test_encode_decode_roundtrip_nested() -> void:
	var original: Dictionary = {"a": [{"b": [1.0, 2.0]}, null], "c": "str"}
	var encoded: PackedByteArray = MsgPack.encode(original)
	var decoded: Variant = MsgPack.decode(encoded)
	assert_eq_deep(decoded, original)


func test_encode_decode_roundtrip_numbers() -> void:
	assert_eq(MsgPack.decode(MsgPack.encode(0)), 0.0)
	assert_eq(MsgPack.decode(MsgPack.encode(42)), 42.0)
	assert_eq(MsgPack.decode(MsgPack.encode(-7)), -7.0)
	assert_eq(MsgPack.decode(MsgPack.encode(3.14)), 3.14)


func test_decode_empty_returns_null() -> void:
	assert_null(MsgPack.decode(PackedByteArray()))


func test_encode_returns_packedbytearray() -> void:
	var result: PackedByteArray = MsgPack.encode({"test": 1})
	assert_true(result is PackedByteArray)
	assert_gt(result.size(), 0)


func test_decode_invalid_json_returns_null() -> void:
	var invalid_bytes: PackedByteArray = "not valid json".to_utf8_buffer()
	var result: Variant = MsgPack.decode(invalid_bytes)
	assert_null(result)
	for err in get_errors():
		err.handled = true
