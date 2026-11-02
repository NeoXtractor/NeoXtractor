"""Reader for the game's THFB (.thy) name tables.

`.thy` records are 32 bytes: stored_size, content_xxh32, flag, orig_size, md5[16].
The key table holds xxh32(path, seed 163) values, which lets us resolve a
`wwise/<WwiseShortID>.wem` vfs path to the md5 name used inside the .idx / .wpk.
"""

from __future__ import annotations

import os
import struct

from core.logger import get_logger

THY_REC_SIZE = 32


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def _i32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<i", data, offset)[0]


def _u16(data: bytes, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def _fields(data: bytes, node: int) -> list[int]:
    attr = node - _i32(data, node)
    count = max(0, (_u16(data, attr) - 4) // 2)
    return [_u16(data, attr + 4 + 2 * i) for i in range(count)]


def _target(data: bytes, base: int, fields: list[int], index: int) -> int:
    addr = base + fields[index]
    return addr + _u32(data, addr)


def _load_table(path: str):
    data = open(path, "rb").read()
    root = 8 + _u32(data, 8)
    parent = _target(data, root, _fields(data, root), 1)
    fields = _fields(data, parent)
    records = _target(data, parent, fields, 0)
    count = _u32(data, records)
    keys_node = _target(data, parent, fields, 1)
    keys = _target(data, keys_node, _fields(data, keys_node), 0)
    return data, records + 4, count, keys + 4, _u32(data, keys)


def load_thy_keys(path: str) -> dict[int, str]:
    """Return {vfs key hash: md5 hex} for a .thy table."""
    data, record_off, count, key_off, key_count = _load_table(path)
    keys = {_u32(data, key_off + 4 * i): i for i in range(key_count)}
    out: dict[int, str] = {}
    for key, record_index in keys.items():
        if record_index >= count:
            continue
        record = data[record_off + THY_REC_SIZE * record_index: record_off + THY_REC_SIZE * (record_index + 1)]
        out[key] = record[16:32].hex()
    return out


def guess_thy_path(idx_path: str) -> str | None:
    """Guess the matching .thy for an opened .idx (res/wwise.idx -> thd/wwise.thy)."""
    if not idx_path:
        return None
    idx_path = os.path.abspath(idx_path)
    stem = os.path.splitext(os.path.basename(idx_path))[0]
    res_dir = os.path.dirname(idx_path)
    root = os.path.dirname(res_dir)
    candidates = [
        os.path.join(root, "thd", stem + ".thy"),
        os.path.join(res_dir, "thd", stem + ".thy"),
        os.path.join(root, "Documents", "thd", stem + ".thy"),
        os.path.join(os.path.dirname(root), "thd", stem + ".thy"),
    ]
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    get_logger().warning("Wwise: could not auto-detect .thy for %s", idx_path)
    return None
