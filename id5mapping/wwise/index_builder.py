"""Build the distributable Wwise event hash index.

The shipped file only contains stable, machine independent data:

    FNV-1(event name) -> event name

Event object ids inside `.bnk` banks are exactly `FNV-1(event name)`, so the
index lets the tool turn any event found in a bank into its readable name.
Everything else (media shortIDs, `wwise.thy` keys, md5 names, .idx offsets) is
resolved at runtime from the user's own package, never stored here.

Usage:
    python -m id5mapping.wwise.index_builder --events wwise_audio_length_ex.py \
        --out id5mapping/wwise/wwise_event_hash_index.bin
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import struct
import time
import zlib

from core.logger import get_logger
from core.utils import get_application_path

from .hashes import fnv1

INDEX_FORMAT = "neoxtractor-wwise-event-hash-index"
INDEX_VERSION = 1
INDEX_FILE_NAME = "wwise_event_hash_index.bin"
_BIN_FILE_NAME = INDEX_FILE_NAME
_JSON_FILE_NAME = "wwise_event_hash_index.json"
_GZ_FILE_NAME = _JSON_FILE_NAME + ".gz"

# Binary layout (compact successor of the JSON index, same idea as the
# NXTR path index): magic + zlib'ed sorted u32 hashes, name offsets and pool.
BIN_MAGIC = b"NXWE\0\0\0\0"


def bundled_index_path() -> str | None:
    """Path of the index bundled with the Identity V mapping folder."""
    roots = [
        get_application_path(),
        # Also look next to the source tree (e.g. when run via `uv run`).
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    ]
    for root in roots:
        data_dir = os.path.join(root, "id5mapping", "wwise")
        for name in (_BIN_FILE_NAME, _GZ_FILE_NAME, _JSON_FILE_NAME):
            path = os.path.join(data_dir, name)
            if os.path.exists(path):
                return path
    return None


def normalize_event_name(name: str) -> str:
    """Return the bare event name of a possibly prefixed entry.

    Some tables (e.g. `wwise_replace_event`) store names as
    `amb/Event|P_amb_static_racecourse_horse_vo_hit`; banks always hash the
    part after the last `|`.
    """
    return name.rsplit("|", 1)[-1].strip()


def load_event_names(path: str) -> list[str]:
    """Load event names from a decompiled `.py` or a `.json` file."""
    path = os.path.abspath(path)
    if path.lower().endswith((".json", ".json.gz")):
        opener = gzip.open if path.lower().endswith(".gz") else open
        with opener(path, "rt", encoding="utf-8") as handle:
            data = json.load(handle)
    else:
        namespace: dict = {"__name__": "wwise_event_names"}
        with open(path, "r", encoding="utf-8") as handle:
            exec(compile(handle.read(), path, "exec"), namespace)  # noqa: S102
        data = namespace.get("data")
    if not isinstance(data, dict):
        raise ValueError(f"No event dict found in {path}")
    return [normalize_event_name(str(name)) for name in data]


def load_binary_events(path: str) -> dict[int, str]:
    """Read the compact binary index (`NXWE`): zlib'ed hashes + name pool."""
    with open(path, "rb") as handle:
        magic, _build_time, raw_len = struct.unpack("<8s2I", handle.read(16))
        if magic != BIN_MAGIC:
            raise ValueError(f"Not a NXWE index: {path}")
        payload = zlib.decompress(handle.read())
    if len(payload) != raw_len:
        raise ValueError(f"Truncated NXWE index: {path}")
    count, pool_len = struct.unpack("<2I", payload[:8])
    body = payload[8:]
    hashes = struct.unpack_from(f"<{count}I", body, 0)
    offsets = struct.unpack_from(f"<{count + 1}I", body, 4 * count)
    pool = body[4 * count + 4 * (count + 1): 4 * count + 4 * (count + 1) + pool_len]
    return {
        hashes[index]: pool[offsets[index]:offsets[index + 1]].decode("utf-8")
        for index in range(count)
    }


def save_binary_events(events: dict[int, str], path: str) -> int:
    """Write the compressed binary index (sorted hashes + shared name pool)."""
    items = sorted((int(raw), name) for raw, name in events.items())
    if any(raw >= 2**32 for raw, _ in items):
        raise ValueError("Event hashes must fit in 32 bits")
    pool = bytearray()
    offsets: list[int] = []
    for _raw, name in items:
        offsets.append(len(pool))
        pool.extend(name.encode("utf-8"))
    offsets.append(len(pool))
    payload = (
        struct.pack("<2I", len(items), len(pool))
        + struct.pack(f"<{len(items)}I", *[raw for raw, _ in items])
        + struct.pack(f"<{len(items) + 1}I", *offsets)
        + bytes(pool)
    )
    compressed = zlib.compress(payload, 9)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(struct.pack("<8s2I", BIN_MAGIC, int(time.time()), len(payload)))
        handle.write(compressed)
    return 16 + len(compressed)


def load_index_events(path: str) -> dict[int, str]:
    """Read any supported index file (binary `.bin`, `.json` or `.json.gz`)."""
    path = os.path.abspath(path)
    with open(path, "rb") as handle:
        head = handle.read(len(BIN_MAGIC))
    if head == BIN_MAGIC:
        return load_binary_events(path)

    opener = gzip.open if path.lower().endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict) or data.get("format") != INDEX_FORMAT:
        raise ValueError(f"Not a {INDEX_FORMAT} file: {path}")
    events: dict[int, str] = {}
    for raw_hash, name in data.get("events", {}).items():
        try:
            events[int(raw_hash)] = str(name)
        except (TypeError, ValueError):
            continue
    return events


def build_hash_index(events_path: str) -> dict:
    """Compute the FNV-1 hash of every event name."""
    names = load_event_names(events_path)
    events: dict[str, str] = {}
    for name in sorted(names):
        events[str(fnv1(name))] = name
    get_logger().info("Wwise hash index: %d events hashed", len(events))
    return {
        "format": INDEX_FORMAT,
        "version": INDEX_VERSION,
        "built": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "events": events,
        "stats": {"events": len(events)},
    }


def merge_index(index: dict, path: str) -> dict:
    """Merge a freshly built index into an existing file, never dropping names.

    Event names can disappear from the game scripts while their banks (and
    therefore their hashes) are still shipped, so an update only ever adds.
    """
    path = os.path.abspath(path)
    if not os.path.exists(path):
        return index
    try:
        existing = load_index_events(path)
    except (OSError, ValueError):
        get_logger().exception("Wwise: failed to read existing index %s", path)
        return index
    merged = dict(existing)
    merged.update({int(raw): name for raw, name in index["events"].items()})
    index["events"] = {str(raw): name for raw, name in merged.items()}
    index["stats"] = {"events": len(merged)}
    return index


def save_index(index: dict, path: str) -> int:
    """Write the index as binary (`.bin`), plain JSON or gzip'ed JSON."""
    path = os.path.abspath(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if path.lower().endswith(".bin"):
        size = save_binary_events(
            {int(raw): name for raw, name in index["events"].items()}, path
        )
    else:
        payload = json.dumps(index, ensure_ascii=False, separators=(",", ":"))
        if path.lower().endswith(".gz"):
            with gzip.open(path, "wt", encoding="utf-8") as handle:
                handle.write(payload)
        else:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(payload)
        size = os.path.getsize(path)
    get_logger().info("Wwise: hash index written to %s (%.1f KB)", path, size / 1024)
    return size


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: rebuild the bundled Wwise event hash index."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", required=True,
                        help="Event list (.py or .json), only the names are used")
    parser.add_argument(
        "--out",
        default=os.path.join("id5mapping", "wwise", INDEX_FILE_NAME),
        help="Output path (.bin, .json or .json.gz)",
    )
    args = parser.parse_args(argv)

    index = build_hash_index(args.events)
    index = merge_index(index, args.out)
    size = save_index(index, args.out)
    print(f"events={index['stats']['events']} size={size / 1024:.0f}KB -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
