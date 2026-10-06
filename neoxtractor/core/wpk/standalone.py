"""Standalone FKPW WPK scanning helpers."""

from __future__ import annotations

import os

from neoxtractor.core.logger import get_logger
from neoxtractor.core.npk.class_types import NPKIndex

from .constants import EMBEDDED_MAGIC, MIN_EMBEDDED_HEADER_SIZE, WPK_MAGIC


def read_wpk_header(reader, file) -> None:
    """Read a standalone WPK header and store archive size."""
    magic = file.read(4)
    if magic != WPK_MAGIC:
        raise ValueError(f"Not a valid WPKF (.wpk) file: {reader.wpk_path}")

    file.seek(0, os.SEEK_END)
    reader._wpk_size = file.tell()
    file.seek(0)

    get_logger().info("Archive type: WPKF standalone .wpk")
    get_logger().info("WPK size: %d bytes", reader._wpk_size)


def scan_wpk_indices(reader, file) -> None:
    """Scan embedded 1DPW entries from a standalone WPK container."""
    data = file.read()
    offsets: list[int] = []
    pos = 0
    while True:
        found = data.find(EMBEDDED_MAGIC, pos)
        if found == -1:
            break
        offsets.append(found)
        pos = found + 1

    reader.indices = []
    if not offsets:
        reader.file_count = 0
        get_logger().warning(
            "No embedded %r entries found in WPK: %s", EMBEDDED_MAGIC, reader.wpk_path
        )
        return

    for i, off in enumerate(offsets):
        next_off = offsets[i + 1] if i + 1 < len(offsets) else len(data)
        index = build_index_from_embedded_header(data, off, next_off, i)
        reader.indices.append(index)

    reader.file_count = len(reader.indices)
    reader._wpk_paths[0] = reader.wpk_path or reader.file_path

    get_logger().info(
        "Scanned %d embedded entries from standalone WPK", reader.file_count
    )


def build_index_from_embedded_header(
    data: bytes, off: int, next_off: int, ordinal: int
) -> NPKIndex:
    """Build an NPKIndex from one embedded WPK entry header."""
    index = NPKIndex()

    hdr_size = 0
    payload_size = 0
    raw_hash = b""

    available = len(data) - off
    if available >= MIN_EMBEDDED_HEADER_SIZE:
        raw_hash = data[off + 0x08 : off + 0x18]
        payload_size = int.from_bytes(data[off + 0x20 : off + 0x24], "little")
        hdr_size = int.from_bytes(data[off + 0x24 : off + 0x26], "little")

    guessed_total = hdr_size + payload_size if hdr_size > 0 else 0
    max_possible = max(0, next_off - off)

    if hdr_size < MIN_EMBEDDED_HEADER_SIZE or hdr_size > max_possible:
        hdr_size = (
            MIN_EMBEDDED_HEADER_SIZE
            if max_possible >= MIN_EMBEDDED_HEADER_SIZE
            else max_possible
        )
    if guessed_total <= 0 or guessed_total > max_possible:
        total_size = max_possible
        if total_size >= hdr_size:
            payload_size = total_size - hdr_size
        else:
            hdr_size = total_size
            payload_size = 0
    else:
        total_size = guessed_total

    if not raw_hash or raw_hash == b"\x00" * 16:
        raw_hash = off.to_bytes(8, "little") + ordinal.to_bytes(8, "little")

    index.file_signature = int.from_bytes(raw_hash, "little")
    index.file_offset = off
    index.file_length = total_size
    index.file_original_length = total_size
    index.zcrc = 0
    index.crc = 0
    index.file_structure = None
    index.filename = raw_hash.hex()

    index.pkg_id = 0
    index.hdr_size = hdr_size
    index.payload_size = payload_size
    index.raw_hash_hex = raw_hash.hex()

    get_logger().debug(
        "WPK scan record %d: off=0x%X hdr=%d payload=%d total=%d name=%s",
        ordinal,
        off,
        hdr_size,
        payload_size,
        total_size,
        index.filename,
    )
    return index
