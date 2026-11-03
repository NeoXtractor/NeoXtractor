from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for _path in (ROOT, HERE):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from id5mapping.wwise.index_builder import (
    BIN_MAGIC,
    INDEX_FORMAT,
    INDEX_VERSION,
    load_index_events,
    save_binary_events,
)

DEFAULT_BIN = os.path.join(ROOT, "id5mapping", "wwise", "wwise_event_hash_index.bin")


def is_binary(path: str) -> bool:
    with open(path, "rb") as handle:
        return handle.read(len(BIN_MAGIC)) == BIN_MAGIC


def write_json(events: dict[int, str], out_path: str) -> None:
    payload = {
        "format": INDEX_FORMAT,
        "version": INDEX_VERSION,
        "built": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "events": {str(raw): name for raw, name in sorted(events.items())},
        "stats": {"events": len(events)},
    }
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    opener = gzip.open if out_path.lower().endswith(".gz") else open
    with opener(out_path, "wt", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def convert(src: str, dst: str) -> int:
    t0 = time.time()
    events = load_index_events(src)
    if is_binary(src):
        write_json(events, dst)
        print("bin -> json: %d events" % len(events), flush=True)
    else:
        save_binary_events(events, dst)
        print("json -> bin: %d events" % len(events), flush=True)

    reloaded = load_index_events(dst)
    if reloaded != events:
        raise SystemExit("round-trip mismatch: %s != %s" % (dst, src))
    print("output %s %d bytes (%.1fs)"
          % (dst, os.path.getsize(dst), time.time() - t0), flush=True)
    return len(events)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Convert the NXWE Wwise event index between .bin and .json.")
    parser.add_argument("--in", dest="input", default=DEFAULT_BIN,
                        help="input index (.bin or .json/.json.gz)")
    parser.add_argument("--out", dest="output", default=None,
                        help="output path; defaults to the sibling with the other extension")
    args = parser.parse_args(argv)

    if not os.path.exists(args.input):
        parser.error("input not found: %s" % args.input)
    output = args.output
    if not output:
        stem, _ = os.path.splitext(args.input)
        output = (stem + ".json") if is_binary(args.input) else (stem + ".bin")
    convert(args.input, output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
