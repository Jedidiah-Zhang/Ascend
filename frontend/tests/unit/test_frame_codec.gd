extends GutTest

const MAX_SIZE := 16 * 1024 * 1024


func test_next_seq_increments() -> void:
	var codec: FrameCodec = FrameCodec.new()
	assert_eq(codec.next_seq(), 1)
	assert_eq(codec.next_seq(), 2)
	assert_eq(codec.next_seq(), 3)


func test_frame_encode_roundtrip_for_dict() -> void:
	var codec: FrameCodec = FrameCodec.new()
	var msg: Dictionary = {"type": "request", "payload": {"x": 10.0}}
	var framed: PackedByteArray = codec.frame_encode(msg)
	assert_gt(framed.size(), 4, "帧至少 4 字节头 + 内容")

	var decoded: Dictionary = codec.frame_decode(framed, MAX_SIZE)
	assert_eq(decoded["bodies"].size(), 1)

	var body: PackedByteArray = decoded["bodies"][0]
	var parsed: Variant = MsgPack.decode(body)
	assert_eq_deep(parsed, msg)


func test_frame_encode_roundtrip_multiple_messages() -> void:
	var codec: FrameCodec = FrameCodec.new()
	var framed1: PackedByteArray = codec.frame_encode({"a": 1.0})
	var framed2: PackedByteArray = codec.frame_encode({"b": 2.0})
	var combined: PackedByteArray = PackedByteArray()
	combined.append_array(framed1)
	combined.append_array(framed2)

	var decoded: Dictionary = codec.frame_decode(combined, MAX_SIZE)
	assert_eq(decoded["bodies"].size(), 2)
	assert_eq_deep(MsgPack.decode(decoded["bodies"][0]), {"a": 1.0})
	assert_eq_deep(MsgPack.decode(decoded["bodies"][1]), {"b": 2.0})


func test_frame_decode_incomplete_frame() -> void:
	var codec: FrameCodec = FrameCodec.new()
	var framed: PackedByteArray = codec.frame_encode({"key": 1.0})
	var partial: PackedByteArray = framed.slice(0, framed.size() - 2)

	var decoded: Dictionary = codec.frame_decode(partial, MAX_SIZE)
	assert_eq(decoded["bodies"].size(), 0, "不完整帧不应返回任何消息")
	assert_eq(decoded["remaining"].size(), partial.size(),
		"不完整帧应保留在缓冲区中等待更多数据")


func test_frame_decode_with_remaining_data() -> void:
	var codec: FrameCodec = FrameCodec.new()
	var framed: PackedByteArray = codec.frame_encode({"msg": 1.0})
	var partial_trailer: PackedByteArray = PackedByteArray([0x00, 0x00, 0x00])
	var combined: PackedByteArray = PackedByteArray()
	combined.append_array(framed)
	combined.append_array(partial_trailer)

	var decoded: Dictionary = codec.frame_decode(combined, MAX_SIZE)
	assert_eq(decoded["bodies"].size(), 1)
	assert_eq(decoded["remaining"].size(), 3,
		"不完整帧头应保留在 remaining 中")


func test_frame_decode_empty_buffer() -> void:
	var codec: FrameCodec = FrameCodec.new()
	var decoded: Dictionary = codec.frame_decode(PackedByteArray(), MAX_SIZE)
	assert_eq(decoded["bodies"].size(), 0)
	assert_eq(decoded["remaining"].size(), 0)


func test_frame_decode_zero_length_header() -> void:
	var codec: FrameCodec = FrameCodec.new()
	var zero_header: PackedByteArray = PackedByteArray([0x00, 0x00, 0x00, 0x00])
	var decoded: Dictionary = codec.frame_decode(zero_header, MAX_SIZE)
	assert_eq(decoded["remaining"].size(), 0,
		"长度 0 的帧应清空缓冲区（视为无效）")
	for err in get_errors():
		err.handled = true


func test_frame_decode_exceeds_max_size() -> void:
	var codec: FrameCodec = FrameCodec.new()
	var too_large: PackedByteArray = PackedByteArray([0x00, 0x00, 0x00, 0x05])
	too_large.append_array("hello".to_utf8_buffer())
	var decoded: Dictionary = codec.frame_decode(too_large, 3)
	assert_eq(decoded["remaining"].size(), 0, "超大小消息应清空缓冲区")
	for err in get_errors():
		err.handled = true
