from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for _path in (ROOT, HERE):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from id5mapping.paths.path_restore import EMPTY, PathHashIndex

DEFAULT_IN = os.path.join(ROOT, "id5mapping", "paths", "path_index.bin")
DEFAULT_OUT = os.path.join(ROOT, "id5mapping", "paths", "path_index.json")
FORMAT = "neoxtractor-id5-path-index"
VERSION = 1


def iter_paths(index: PathHashIndex) -> list[str]:
    found: set[str] = set()
    for slot in range(index.m):
        offset = index.slot_rec[slot]
        if offset == EMPTY:
            continue
        found.add(index._path(index.slot_dir[slot], index.slot_gid[slot], offset))
    for i in range(index.overflow_count):
        found.add(index._path(index.ovf_dir[i], index._overflow_gid(i), index.ovf_off[i]))
    return sorted(found)


def bin2json(bin_path: str, out_path: str) -> int:
    t0 = time.time()
    index = PathHashIndex(bin_path)
    paths = iter_paths(index)
    payload = {
        "format": FORMAT,
        "version": VERSION,
        "count": len(paths),
        "keys": index.n,
        "built": datetime.fromtimestamp(index.build_time).isoformat(timespec="seconds"),
        "paths": paths,
    }
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
    print("paths %d (bin keys %d)" % (len(paths), index.n), flush=True)
    print("output %s %d bytes (%.1fs)"
          % (out_path, os.path.getsize(out_path), time.time() - t0), flush=True)
    return len(paths)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Convert the NXTR path index (.bin) to a readable JSON file.")
    parser.add_argument("--in", dest="input", default=DEFAULT_IN,
                        help="input path_index.bin")
    parser.add_argument("--out", dest="output", default=DEFAULT_OUT,
                        help="output JSON file")
    args = parser.parse_args(argv)

    if not os.path.exists(args.input):
        parser.error("input not found: %s" % args.input)
    bin2json(args.input, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
