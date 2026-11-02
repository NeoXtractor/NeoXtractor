"""Restore original resource paths for the entries of an opened IDX.

The bundle ``id5mapping/paths/path_index.bin`` is a compact hash index
``xxh32(relative path, seed 163) -> original vfs path`` (see
``tools/build_path_index.py``).  The game ships one THFB table pair per
package: ``thd/<pkg>.thy`` holds, for every record, the xxh32 key and the
content md5, ``thd/<pkg>.thh`` holds the matching xxh32 item hash.

Joining both sides names every IDX row without storing anything machine
specific::

    (.thy key, .thh item) --path index--> vfs path
    .thy record md5                    --> IDX record

``wwise`` packages are excluded: their media is labelled by the dedicated
Wwise event mapping instead.
"""

from __future__ import annotations

import array
import marshal
import os
import struct
from datetime import datetime

from core.logger import get_logger
from core.utils import get_application_path

SEED_KEY = 163
SEED_ITEM = 16361
EMPTY = 0xFFFFFFFF
HDR_FMT = "<8s18I"
HDR_LEN = struct.calcsize(HDR_FMT)
MAGIC = b"NXTR\0\0\0\0"
NGRP = 32

try:  # optional C extension; the pure-Python fallback is equivalent
    import xxhash

    def xxh32(data: bytes, seed: int) -> int:
        return xxhash.xxh32(data, seed=seed).intdigest()

except Exception:  # pragma: no cover
    MASK32 = 0xFFFFFFFF
    _P1 = 0x9E3779B1
    _P2 = 0x85EBCA77
    _P3 = 0xC2B2AE3D
    _P4 = 0x27D4EB2F
    _P5 = 0x165667B1

    def _rotl(value: int, bits: int) -> int:
        return ((value << bits) | (value >> (32 - bits))) & MASK32

    def xxh32(data: bytes, seed: int) -> int:
        length = len(data)
        index = 0
        if length >= 16:
            v1 = (seed + _P1 + _P2) & MASK32
            v2 = (seed + _P2) & MASK32
            v3 = seed & MASK32
            v4 = (seed - _P1) & MASK32
            while index <= length - 16:
                v1 = (_rotl((v1 + struct.unpack_from("<I", data, index)[0] * _P2) & MASK32, 13) * _P1) & MASK32
                v2 = (_rotl((v2 + struct.unpack_from("<I", data, index + 4)[0] * _P2) & MASK32, 13) * _P1) & MASK32
                v3 = (_rotl((v3 + struct.unpack_from("<I", data, index + 8)[0] * _P2) & MASK32, 13) * _P1) & MASK32
                v4 = (_rotl((v4 + struct.unpack_from("<I", data, index + 12)[0] * _P2) & MASK32, 13) * _P1) & MASK32
                index += 16
            value = (_rotl(v1, 1) + _rotl(v2, 7) + _rotl(v3, 12) + _rotl(v4, 18)) & MASK32
        else:
            value = (seed + _P5) & MASK32
        value = (value + length) & MASK32
        while index <= length - 4:
            value = (value + struct.unpack_from("<I", data, index)[0] * _P3) & MASK32
            value = (_rotl(value, 17) * _P4) & MASK32
            index += 4
        while index < length:
            value = (value + data[index] * _P5) & MASK32
            value = (_rotl(value, 11) * _P1) & MASK32
            index += 1
        value ^= value >> 15
        value = (value * _P2) & MASK32
        value ^= value >> 13
        value = (value * _P3) & MASK32
        value ^= value >> 16
        return value & MASK32


def rel_of(path: str) -> str:
    """Strip the package segment: `<pkg>/<rel>` -> `<rel>`."""
    return path.split("/", 1)[1] if "/" in path else path


def bundled_index_path() -> str:
    """Locate the bundled ``path_index.bin`` (app dir first, then source tree)."""
    roots = [
        get_application_path(),
        # Also look next to the source tree (e.g. when run via `uv run`).
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    ]
    for root in roots:
        path = os.path.join(root, "id5mapping", "paths", "path_index.bin")
        if os.path.exists(path):
            return path
    return os.path.join(
        get_application_path(), "id5mapping", "paths", "path_index.bin"
    )


class PathHashIndex:
    """Reader for the bundled ``NXTR`` path index."""

    def __init__(self, path: str):
        with open(path, "rb") as handle:
            data = handle.read()
        header = struct.unpack(HDR_FMT, data[:HDR_LEN])
        (magic, self.n, self.m, self.bucket_count, self.seed_g, self.seed_f,
         _groups, self.dir_count, self.overflow_count, dtab_off, slot_off,
         gid_off, dir_off, ngt_off, didx_off, pbuf_off, ebuf_off,
         ovf_off, self.build_time) = header
        if magic[:4] != MAGIC[:4]:
            raise ValueError(f"Unsupported path index format: {path}")

        self.data = data
        self.dtab = array.array("H")
        self.dtab.frombytes(data[dtab_off:slot_off])
        self.slot_rec = array.array("I")
        self.slot_rec.frombytes(data[slot_off:gid_off])
        self.slot_gid = array.array("H")
        self.slot_gid.frombytes(data[gid_off:dir_off])
        self.slot_dir = array.array("H")
        self.slot_dir.frombytes(data[dir_off:ngt_off])
        self.ngtab = array.array("I")
        self.ngtab.frombytes(data[ngt_off:didx_off])
        self.didx = array.array("I")
        self.didx.frombytes(data[didx_off:pbuf_off])
        self.pbuf = data[pbuf_off:ebuf_off]
        self.ebuf = data[ebuf_off:ovf_off]

        end = ovf_off + 4 * self.overflow_count
        self.ovf_key = array.array("I")
        self.ovf_key.frombytes(data[ovf_off:end])
        self.ovf_dir = array.array("H")
        self.ovf_dir.frombytes(data[end:end + 2 * self.overflow_count])
        self.ovf_off = array.array("I")
        self.ovf_off.frombytes(
            data[end + 2 * self.overflow_count:end + 6 * self.overflow_count]
        )

    def _cstr(self, buf, offset: int) -> bytes:
        return buf[offset:buf.find(b"\x00", offset)]

    def _name(self, gid: int, offset: int) -> bytes:
        first = self._cstr(self.ebuf, self.ngtab[gid] + 1)
        return first[: self.ebuf[offset]] + self._cstr(self.ebuf, offset + 1)

    def _path(self, dir_id: int, gid: int, offset: int) -> str:
        return (
            self._cstr(self.pbuf, self.didx[dir_id]) + self._name(gid, offset)
        ).decode("utf-8", "ignore")

    def _overflow_gid(self, index: int) -> int:
        offset = self.ovf_off[index]
        lo, hi = 0, len(self.ngtab) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self.ngtab[mid] <= offset:
                lo = mid
            else:
                hi = mid - 1
        return lo

    def lookup(self, key: int, item: int | None) -> str | None:
        """Return the path whose double hash is ``(key, item)``, else None."""
        key_bytes = struct.pack("<I", key)
        g = xxh32(key_bytes, self.seed_g)
        f = xxh32(key_bytes, self.seed_f)
        bucket = g % self.bucket_count
        slot = (g % self.m + (self.dtab[bucket] * (f % self.m)) % self.m) % self.m
        offset = self.slot_rec[slot]
        if offset != EMPTY:
            path = self._path(self.slot_dir[slot], self.slot_gid[slot], offset)
            if item is None or xxh32(rel_of(path).encode(), SEED_ITEM) == item:
                return path

        lo, hi = 0, self.overflow_count
        while lo < hi:
            mid = (lo + hi) // 2
            if self.ovf_key[mid] < key:
                lo = mid + 1
            else:
                hi = mid
        while lo < self.overflow_count and self.ovf_key[lo] == key:
            path = self._path(self.ovf_dir[lo], self._overflow_gid(lo), self.ovf_off[lo])
            if item is None or xxh32(rel_of(path).encode(), SEED_ITEM) == item:
                return path
            lo += 1
        return None


_index_cache: list[PathHashIndex | None] = []


def get_path_index() -> PathHashIndex | None:
    """Load the bundled index once; return None when it is not shipped."""
    if _index_cache:
        return _index_cache[0]
    path = bundled_index_path()
    index: PathHashIndex | None = None
    if os.path.exists(path):
        try:
            index = PathHashIndex(path)
            get_logger().info(
                "Path index loaded: %d keys, %d bytes, built %s",
                index.n,
                os.path.getsize(path),
                datetime.fromtimestamp(index.build_time).strftime("%Y-%m-%d %H:%M:%S"),
            )
        except Exception:  # noqa: BLE001
            get_logger().exception("Failed to load path index %s", path)
    else:
        get_logger().warning("Path index not found: %s", path)
    _index_cache.append(index)
    return index


# ----------------------------------------------------------------------
# THFB (.thy / .thh) tables
# ----------------------------------------------------------------------
def _u16(data: bytes, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def _i32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<i", data, offset)[0]


def _fields(data: bytes, node: int) -> list[int]:
    attr = node - _i32(data, node)
    count = max(0, (_u16(data, attr) - 4) // 2)
    return [_u16(data, attr + 4 + 2 * i) for i in range(count)]


def _target(data: bytes, base: int, fields: list[int], index: int) -> int:
    address = base + fields[index]
    return address + _u32(data, address)


def _load_table(path: str):
    """Return (data, record_offset, record_count, key_offset, key_count)."""
    data = open(path, "rb").read()
    root = 8 + _u32(data, 8)
    parent = _target(data, root, _fields(data, root), 1)
    parent_fields = _fields(data, parent)
    records = _target(data, parent, parent_fields, 0)
    count = _u32(data, records)
    keys_node = _target(data, parent, parent_fields, 1)
    keys = _target(data, keys_node, _fields(data, keys_node), 0)
    return data, records + 4, count, keys + 4, _u32(data, keys)


def _thy_candidates(idx_path: str) -> list[str]:
    """Return existing `.thy` files for a package, base first, hot last.

    The `thd` directory always sits next to the `res` directory holding the
    IDX, so a base `<root>/res` package maps to `<root>/thd` and a hot
    `<root>/Documents/res` package maps to `<root>/Documents/thd`.
    """
    idx_path = os.path.abspath(idx_path)
    idx_dir = os.path.dirname(idx_path)
    stem = os.path.splitext(os.path.basename(idx_path))[0]
    parent = os.path.dirname(idx_dir)
    if os.path.basename(parent).lower() == "documents":
        base_dir = os.path.join(os.path.dirname(parent), "thd")
        hot_dir = os.path.join(parent, "thd")
    else:
        base_dir = os.path.join(parent, "thd")
        hot_dir = os.path.join(parent, "Documents", "thd")

    candidates = []
    for directory in (base_dir, hot_dir):
        thy_path = os.path.join(directory, stem + ".thy")
        if os.path.exists(thy_path) and os.path.exists(thy_path[:-4] + ".thh"):
            candidates.append(thy_path)
    return candidates


def _read_thy_rows(thy_path: str) -> list[tuple[int, int | None, str]]:
    """Return `(key, item, md5 hex)` rows for one `.thy` / `.thh` pair."""
    data, record_off, record_count, key_off, key_count = _load_table(thy_path)
    hdata, hrecord_off, hcount, hkey_off, hkey_count = _load_table(
        thy_path[:-4] + ".thh"
    )
    keys = [_u32(data, key_off + 4 * i) for i in range(key_count)]
    items = [_u32(hdata, hrecord_off + 4 * i) for i in range(hcount)]
    rows = []
    for i, key in enumerate(keys):
        if i >= record_count:
            break
        record = data[record_off + 32 * i:record_off + 32 * i + 32]
        rows.append((key, items[i] if i < len(items) else None, record[16:32].hex()))
    return rows


def _is_unnamed(record) -> bool:
    """True while the row still carries its md5 hash instead of a path."""
    return record.filename == getattr(record, "raw_hash_hex", None)


def _restore_script_bytecode_paths(reader, package: str) -> int:
    """Name still-hashed `script` rows from the marshal header of their bytecode.

    The `script` package ships compiled modules (`.nxs`) whose marshal dump
    keeps the original source path in ``co_filename`` (e.g.
    ``lib\\anyio\\from_thread.py``). Those modules are absent from the THFB
    tables, so without this fallback their rows would stay md5-named.
    """
    restored = 0
    load_entry = getattr(reader, "load_entry", None)
    entries = getattr(reader, "entries", {})
    if load_entry is None:
        return 0

    for position, record in enumerate(getattr(reader, "indices", ())):
        if not _is_unnamed(record):
            continue
        try:
            load_entry(position)
        except Exception:  # noqa: BLE001
            continue
        entry = entries.get(position)
        if entry is None:
            continue
        try:
            code = marshal.loads(entry.get_export_data(decoded=False))
        except Exception:  # noqa: BLE001
            continue

        source = getattr(code, "co_filename", "") or ""
        if not source.endswith(".py"):
            continue
        rel = source.replace("\\", "/")
        if rel.startswith("/") or ":" in rel:
            continue

        name = f"{package}/{os.path.splitext(rel)[0]}.nxs"
        record.filename = name
        entry.filename = name
        entry.extension = "nxs"
        restored += 1

    if restored:
        get_logger().info(
            "Path restore: %d script rows named from their bytecode", restored
        )
    return restored


def restore_archive_paths(reader, thy_paths=None) -> int:
    """Name every IDX row of `reader` from its THFB table. Returns the count.

    :param thy_paths: explicit `.thy` files to use (base first, hot last);
        when omitted the tables next to the IDX are used.
    """
    idx_path = getattr(reader, "idx_path", None)
    if not idx_path:
        return 0

    stem = os.path.splitext(os.path.basename(idx_path))[0].lower()
    if stem == "wwise":
        return 0

    restored = 0
    index = get_path_index()
    if index is not None:
        candidates = list(thy_paths) if thy_paths else _thy_candidates(idx_path)

        by_md5: dict[str, str] = {}
        for thy_path in candidates:
            try:
                rows = _read_thy_rows(thy_path)
            except Exception:  # noqa: BLE001
                get_logger().exception("Failed to read THFB table %s", thy_path)
                continue
            found = 0
            for key, item, md5 in rows:
                if item is None:
                    continue
                path = index.lookup(key, item)
                if path:
                    by_md5[md5] = path
                    found += 1
            get_logger().info(
                "Path restore: %d/%d rows resolved from %s",
                found,
                len(rows),
                os.path.basename(thy_path),
            )

        for record in getattr(reader, "indices", ()):
            path = by_md5.get(getattr(record, "raw_hash_hex", ""))
            if path and record.filename != path:
                record.filename = path
                restored += 1

    # `script` modules carry their own path; use it where the tables did not.
    if stem == "script":
        restored += _restore_script_bytecode_paths(reader, stem)

    get_logger().info(
        "Path restore: %d/%d entries named for %s",
        restored,
        len(reader.indices),
        os.path.basename(idx_path),
    )
    return restored
