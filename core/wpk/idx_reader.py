"""IDX-specific parsing helpers."""

from __future__ import annotations

import os
from collections import Counter

from core.logger import get_logger
from core.npk.class_types import NPKIndex

from .constants import IDX_HEAD_SIZE, IDX_REC_SIZE


def read_idx_header(reader, file) -> None:
    """Read the header of a SKPW IDX file."""
    magic = file.read(4)
    if magic != b"SKPW":
        raise ValueError(f"Not a valid WPKS (.idx) file: {reader.idx_path}")

    file.seek(0x0C)
    reader.file_count = int.from_bytes(file.read(4), "little")

    get_logger().info("Archive type: WPKS (.idx)")
    get_logger().info("Entry count: %d", reader.file_count)


def read_idx_indices(reader, file) -> None:
    """Read all fixed-size IDX records into NPKIndex entries."""
    reader.indices = []
    file.seek(IDX_HEAD_SIZE)
    logger = get_logger()

    for i in range(reader.file_count):
        rec = file.read(IDX_REC_SIZE)
        if len(rec) != IDX_REC_SIZE:
            raise EOFError(f"IDX truncated while reading record {i}")

        index = NPKIndex()

        raw_hash = rec[0x00:0x10]
        pkg_id = rec[0x14]
        file_off_hdr = int.from_bytes(rec[0x18:0x1C], "little")
        payload_size = int.from_bytes(rec[0x1C:0x20], "little")
        hdr_size = int.from_bytes(rec[0x20:0x22], "little")
        total_size = hdr_size + payload_size

        index.file_signature = int.from_bytes(raw_hash, "little")
        index.file_offset = file_off_hdr
        index.file_length = total_size
        index.file_original_length = total_size
        index.zcrc = 0
        index.crc = 0
        index.file_structure = None
        index.filename = raw_hash.hex()

        index.pkg_id = pkg_id
        index.hdr_size = hdr_size
        index.payload_size = payload_size
        index.raw_hash_hex = raw_hash.hex()

        reader.indices.append(index)
        logger.debug(
            "IDX record %d: pkg=%d off=0x%X hdr=%d payload=%d name=%s",
            i,
            pkg_id,
            file_off_hdr,
            hdr_size,
            payload_size,
            index.filename,
        )

    log_idx_split_summary(reader)


def log_idx_split_summary(reader) -> None:
    """Log how many IDX entries point to each WPK package or slot_file."""
    pkg_counts = Counter(int(getattr(index, "pkg_id", -1)) for index in reader.indices)
    if not pkg_counts:
        return

    get_logger().info(
        "IDX split summary for %s",
        os.path.basename(reader.idx_path or reader.file_path),
    )

    slot_file_count = 0
    for pkg_id in sorted(pkg_counts):
        count = pkg_counts[pkg_id]
        if reader._is_slot_file_pkg(pkg_id):
            slot_file_count += count
            continue

        resolved_path = reader._find_wpk_path(pkg_id)
        label = (
            os.path.basename(resolved_path)
            if os.path.exists(resolved_path)
            else f"package {pkg_id}"
        )
        get_logger().info("  %s -> %d files", label, count)

    if slot_file_count:
        slot_dir = reader._get_slot_file_dir()
        actual_slot_files = 0
        if slot_dir.is_dir():
            actual_slot_files = sum(1 for p in slot_dir.iterdir() if p.is_file())
        get_logger().info(
            "  slot_file -> %d indexed entries%s",
            slot_file_count,
            f" ({actual_slot_files} files in {slot_dir.name}/)"
            if actual_slot_files
            else "",
        )
