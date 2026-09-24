"""Tests for the protobuf parser and text extraction."""

from qq_digest.collector.protobuf import (
    extract_message_text,
    extract_peer_uid_from_group_blob,
    parse_protobuf,
)


class TestParseProtobuf:
    def test_empty(self):
        assert parse_protobuf(b"") == {}

    def test_varint_field(self):
        # field 1, varint, value 42
        data = bytes([0x08, 0x2A])
        result = parse_protobuf(data)
        assert 1 in result
        assert result[1][0] == ("varint", 42)

    def test_bytes_field(self):
        # field 1, length-delimited, value "hello"
        data = bytes([0x0A, 0x05]) + b"hello"
        result = parse_protobuf(data)
        assert 1 in result
        assert result[1][0] == ("bytes", b"hello")

    def test_truncated_bytes(self):
        # field 1, claims length 10 but only 3 bytes available
        data = bytes([0x0A, 0x0A]) + b"abc"
        result = parse_protobuf(data)
        # Should stop gracefully
        assert 1 not in result


class TestExtractMessageText:
    def test_none_blob(self):
        text, kind = extract_message_text(None)
        assert text == ""
        assert kind == "unknown"

    def test_empty_blob(self):
        text, kind = extract_message_text(b"")
        assert text == ""
        assert kind == "unknown"

    def test_text_message(self):
        # Build a minimal text message protobuf:
        # inner: field 45002 = 1 (text), field 45101 = "你好"
        inner = make_varint_field(45002, 1) + make_bytes_field(45101, "你好".encode("utf-8"))
        # outer: field 40800 = inner
        outer = make_bytes_field(40800, inner)
        text, kind = extract_message_text(outer)
        assert text == "你好"
        assert kind == "text"

    def test_image_message(self):
        # inner: field 45002 = 2 (image)
        inner = make_varint_field(45002, 2)
        outer = make_bytes_field(40800, inner)
        text, kind = extract_message_text(outer)
        assert text == "[图片]"
        assert kind == "image"

    def test_no_inner_blob(self):
        # outer with a different field
        outer = make_varint_field(40001, 123)
        text, kind = extract_message_text(outer)
        assert text == ""
        assert kind == "no_inner"


class TestExtractPeerUid:
    def test_none(self):
        assert extract_peer_uid_from_group_blob(None) is None

    def test_empty(self):
        assert extract_peer_uid_from_group_blob(b"") is None

    def test_no_u_prefix(self):
        assert extract_peer_uid_from_group_blob(b"\x01\x02\x03") is None

    def test_extracts_uid(self):
        # Simulate a blob with u_ prefix embedded
        blob = b"\xc2\xa8\x1d\x20\x92\xa6\x1d\x18u_AbCdEf123GhIjKlMnOp\x90\xbe\x20\x00"
        result = extract_peer_uid_from_group_blob(blob)
        assert result == "u_AbCdEf123GhIjKlMnOp"

    def test_stops_at_non_alnum(self):
        blob = b"u_test-123\x00\x00"
        result = extract_peer_uid_from_group_blob(blob)
        assert result == "u_test-123"


def make_varint_field(field_num: int, value: int) -> bytes:
    """Encode a varint field in protobuf wire format."""
    tag = (field_num << 3) | 0
    return _encode_varint(tag) + _encode_varint(value)


def make_bytes_field(field_num: int, data: bytes) -> bytes:
    """Encode a length-delimited field in protobuf wire format."""
    tag = (field_num << 3) | 2
    return _encode_varint(tag) + _encode_varint(len(data)) + data


def _encode_varint(value: int) -> bytes:
    result = bytearray()
    while value > 0x7F:
        result.append((value & 0x7F) | 0x80)
        value >>= 7
    result.append(value & 0x7F)
    return bytes(result)
