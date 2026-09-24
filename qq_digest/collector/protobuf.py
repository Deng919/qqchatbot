"""Protobuf parser for NTQQ message blobs.

NTQQ stores message content in protobuf-encoded blobs in the 40800 column
of group_msg_table.  The structure is:

    outer protobuf {
        field 40800 (bytes) = inner protobuf {
            field 45002 (varint) = message type
            field 45101 (bytes) = text content  (when type == 1)
            field 48214 (bytes) = gtip XML       (when type == 8/17)
            field 48271 (bytes) = JSON card      (when type == 8/17)
            field 48504 (bytes) = alt text       (system messages)
            field 48505 (bytes) = alt text
        }
    }

Message types observed in practice:
    1  = plain text
    2  = image
    5  = reply
    6  = forward
    7  = voice
    8  = system/pat
    10 = flash
    11 = emoji
    16 = markdown
    17 = pat
"""

from __future__ import annotations


def _decode_varint(data: bytes, pos: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while pos < len(data):
        b = data[pos]
        result |= (b & 0x7F) << shift
        pos += 1
        if not (b & 0x80):
            break
        shift += 7
    return result, pos


def parse_protobuf(data: bytes) -> dict[int, list[tuple[str, object]]]:
    """Parse a protobuf blob into {field_number: [(wire_type, value), ...]}.

    Only varint and length-delimited (bytes) wire types are needed for message
    extraction; fixed32/fixed64 are consumed but rarely useful here.
    """
    pos = 0
    fields: dict[int, list[tuple[str, object]]] = {}
    while pos < len(data):
        try:
            tag, pos = _decode_varint(data, pos)
        except Exception:
            break
        field_num = tag >> 3
        wire_type = tag & 0x07
        if wire_type == 0:  # varint
            val, pos = _decode_varint(data, pos)
            fields.setdefault(field_num, []).append(("varint", val))
        elif wire_type == 2:  # length-delimited
            length, pos = _decode_varint(data, pos)
            if pos + length > len(data):
                break
            val = data[pos : pos + length]
            pos += length
            fields.setdefault(field_num, []).append(("bytes", val))
        elif wire_type == 5:  # fixed32
            pos += 4
        elif wire_type == 1:  # fixed64
            pos += 8
        else:
            break
    return fields


def _get_varint(fields: dict, num: int) -> int | None:
    for wire, val in fields.get(num, []):
        if wire == "varint":
            return val  # type: ignore[return-value]
    return None


def _get_bytes(fields: dict, num: int) -> bytes | None:
    for wire, val in fields.get(num, []):
        if wire == "bytes":
            return val  # type: ignore[return-value]
    return None


def _get_text(fields: dict, num: int) -> str:
    parts: list[str] = []
    for wire, val in fields.get(num, []):
        if wire == "bytes":
            parts.append(val.decode("utf-8", errors="replace"))  # type: ignore[union-attr]
        elif wire == "varint":
            parts.append(str(val))
    return "".join(parts)


def extract_message_text(blob: bytes | None) -> tuple[str, str]:
    """Extract displayable text from a 40800 protobuf blob.

    Returns (text, message_type_label).  For non-text messages the text
    is a short bracketed description (e.g. "[图片]").
    """
    if not blob:
        return "", "unknown"
    try:
        outer = parse_protobuf(blob)
        inner_blob = _get_bytes(outer, 40800)
        if not inner_blob:
            return "", "no_inner"
        inner = parse_protobuf(inner_blob)
        msg_type = _get_varint(inner, 45002)

        if msg_type == 1:  # plain text
            return _get_text(inner, 45101), "text"
        if msg_type == 2:
            return "[图片]", "image"
        if msg_type == 3:
            return "[文件]", "file"
        if msg_type == 5:
            return "[回复]", "reply"
        if msg_type == 6:
            return "[转发]", "forward"
        if msg_type == 7:
            return "[语音]", "voice"
        if msg_type in (8, 17):  # system / pat
            gtip = _get_text(inner, 48214)
            if gtip:
                return f"[系统] {gtip[:200]}", "system"
            json_text = _get_text(inner, 48271)
            if json_text:
                return f"[卡片] {json_text[:200]}", "json"
            text = _get_text(inner, 48504) + _get_text(inner, 48505)
            if text:
                return text, "system_text"
            return "[系统]", "system"
        if msg_type == 10:
            return "[闪照]", "flash"
        if msg_type == 11:
            return "[表情]", "emoji"
        if msg_type == 16:
            return "[markdown]", "markdown"

        # Fallback: try 45101 anyway
        text = _get_text(inner, 45101)
        if text:
            return text, f"type_{msg_type}"
        return "", f"type_{msg_type}"
    except Exception:
        return "", "error"


def extract_peer_uid_from_group_blob(blob: bytes | None) -> str | None:
    """Extract the u_xxx peer_uid from a group_list.60040 blob.

    The blob is protobuf-encoded but the peer_uid is a plain ASCII string
    embedded in a length-delimited field, so a simple byte scan works.
    """
    if not blob:
        return None
    idx = blob.find(b"u_")
    if idx < 0:
        return None
    end = idx + 2
    while end < len(blob):
        c = chr(blob[end])
        if c.isalnum() or c in "-_":
            end += 1
        else:
            break
    return blob[idx:end].decode("ascii")


def _encode_varint(value: int) -> bytes:
    """Encode an integer as a protobuf varint."""
    result = bytearray()
    while value > 0x7F:
        result.append((value & 0x7F) | 0x80)
        value >>= 7
    result.append(value & 0x7F)
    return bytes(result)


def make_varint_field(field_num: int, value: int) -> bytes:
    """Encode a varint field for testing."""
    tag = (field_num << 3) | 0
    return _encode_varint(tag) + _encode_varint(value)


def make_bytes_field(field_num: int, data: bytes) -> bytes:
    """Encode a length-delimited field for testing."""
    tag = (field_num << 3) | 2
    return _encode_varint(tag) + _encode_varint(len(data)) + data
