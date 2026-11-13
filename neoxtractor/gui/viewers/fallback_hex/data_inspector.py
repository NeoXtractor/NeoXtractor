"""Data Inspector utilities"""

import struct


def decode_uleb128(data, pos):
    """Decode an unsigned LEB128 value from the data at the given position."""
    result = 0
    shift = 0
    offset = 0

    while True:
        if pos + offset >= len(data):
            return None

        byte = data[pos + offset]
        result |= (byte & 0x7F) << shift
        offset += 1

        if not byte & 0x80:
            break

        shift += 7

    return result


def decode_sleb128(data, pos):
    """Decode a signed LEB128 value from the data at the given position."""
    result = 0
    shift = 0
    offset = 0

    while True:
        if pos + offset >= len(data):
            return None

        byte = data[pos + offset]
        result |= (byte & 0x7F) << shift
        offset += 1

        if not byte & 0x80:
            if byte & 0x40:  # Sign bit is set
                result |= -(1 << (shift + 7))
            break

        shift += 7

    return result


DATA_INSPECTOR_TYPES = {
    "binary": lambda data, pos, little_endian: f"{data[pos]:08b}",
    "octal": lambda data, pos, little_endian: f"{data[pos]:03o}",
    "uint8": lambda data, pos, little_endian: data[pos],
    "int8": lambda data, pos, little_endian: int.from_bytes(
        [data[pos]], byteorder="little" if little_endian else "big", signed=True
    ),
    "uint16": lambda data, pos, little_endian: (
        int.from_bytes(
            data[pos : pos + 2],
            byteorder="little" if little_endian else "big",
            signed=False,
        )
        if pos + 1 < len(data)
        else None
    ),
    "int16": lambda data, pos, little_endian: (
        int.from_bytes(
            data[pos : pos + 2],
            byteorder="little" if little_endian else "big",
            signed=True,
        )
        if pos + 1 < len(data)
        else None
    ),
    "uint24": lambda data, pos, little_endian: (
        int.from_bytes(
            data[pos : pos + 3],
            byteorder="little" if little_endian else "big",
            signed=False,
        )
        if pos + 2 < len(data)
        else None
    ),
    "int24": lambda data, pos, little_endian: (
        int.from_bytes(
            data[pos : pos + 3],
            byteorder="little" if little_endian else "big",
            signed=True,
        )
        if pos + 2 < len(data)
        else None
    ),
    "uint32": lambda data, pos, little_endian: (
        int.from_bytes(
            data[pos : pos + 4],
            byteorder="little" if little_endian else "big",
            signed=False,
        )
        if pos + 3 < len(data)
        else None
    ),
    "int32": lambda data, pos, little_endian: (
        int.from_bytes(
            data[pos : pos + 4],
            byteorder="little" if little_endian else "big",
            signed=True,
        )
        if pos + 3 < len(data)
        else None
    ),
    "uint64": lambda data, pos, little_endian: (
        int.from_bytes(
            data[pos : pos + 8],
            byteorder="little" if little_endian else "big",
            signed=False,
        )
        if pos + 7 < len(data)
        else None
    ),
    "int64": lambda data, pos, little_endian: (
        int.from_bytes(
            data[pos : pos + 8],
            byteorder="little" if little_endian else "big",
            signed=True,
        )
        if pos + 7 < len(data)
        else None
    ),
    "ULEB128": lambda data, pos, little_endian: decode_uleb128(data, pos),
    "SLEB128": lambda data, pos, little_endian: decode_sleb128(data, pos),
    "float16": lambda data, pos, little_endian: (
        struct.unpack("<e" if little_endian else ">e", data[pos : pos + 2])[0]
        if pos + 1 < len(data)
        else None
    ),
    "bfloat16": lambda data, pos, little_endian: (
        struct.unpack("<f", data[pos : pos + 2] + b"\x00\x00")[0]
        if pos + 1 < len(data)
        else None
    ),
    "float32": lambda data, pos, little_endian: (
        struct.unpack("<f" if little_endian else ">f", data[pos : pos + 4])[0]
        if pos + 3 < len(data)
        else None
    ),
    "float64": lambda data, pos, little_endian: (
        struct.unpack("<d" if little_endian else ">d", data[pos : pos + 8])[0]
        if pos + 7 < len(data)
        else None
    ),
    # "GUID": lambda data, pos, little_endian:
    #   str(uuid.UUID(bytes_le=bytes(data[pos:pos+16]))) if pos+15 < len(data) else None,
    "ASCII": lambda data, pos, little_endian: (
        chr(data[pos]) if 32 <= data[pos] <= 126 else "." if pos < len(data) else None
    ),
    "UTF-8": lambda data, pos, little_endian: (
        bytes([data[pos]]).decode("utf-8", errors="replace")
        if pos < len(data)
        else None
    ),
    "UTF-16": lambda data, pos, little_endian: (
        bytes(data[pos : pos + 2]).decode(
            "utf-16-le" if little_endian else "utf-16-be", errors="replace"
        )
        if pos + 1 < len(data)
        else None
    ),
    "GB18030": lambda data, pos, little_endian: (
        bytes([data[pos]]).decode("gb18030", errors="replace")
        if pos < len(data)
        else None
    ),
    "BIG5": lambda data, pos, little_endian: (
        bytes([data[pos]]).decode("big5", errors="replace") if pos < len(data) else None
    ),
    "SHIFT-JIS": lambda data, pos, little_endian: (
        bytes([data[pos]]).decode("shift-jis", errors="replace")
        if pos < len(data)
        else None
    ),
}
