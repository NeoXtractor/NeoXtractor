from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for _path in (ROOT, HERE):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from build_path_index import build_from_paths
from id5mapping.paths.path_restore import SEED_ITEM, PathHashIndex, rel_of, xxh32 as h32
from build_path_index import SEED_KEY

DEFAULT_IN = os.path.join(ROOT, "id5mapping", "paths", "path_index.json")
DEFAULT_OUT = os.path.join(ROOT, "id5mapping", "paths", "path_index.bin")


def load_paths(path: str) -> list[str]:
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if isinstance(data, dict):
        paths = data.get("paths")
        if paths is None:
            paths = list(data.values())
    else:
        paths = data
    if not isinstance(paths, list):
        raise ValueError("No path list found in %s" % path)
    return [str(path) for path in paths if path]


def verify(paths: list[str], bin_path: str, samples: int) -> None:
    t0 = time.time()
    index = PathHashIndex(bin_path)
    rnd = random.Random(1)
    ok = bad = 0
    for path in rnd.sample(paths, min(samples, len(paths))):
        rel = rel_of(path).encode()
        got = index.lookup(h32(rel, SEED_KEY), h32(rel, SEED_ITEM))
        if got == path:
            ok += 1
        else:
            bad += 1
            if bad <= 5:
                print("  MISMATCH", path, "->", got)
    print("verify ok=%d bad=%d (%.1fs)" % (ok, bad, time.time() - t0), flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Rebuild the NXTR path index (.bin) from its JSON dump.")
    parser.add_argument("--in", dest="input", default=DEFAULT_IN,
                        help="input JSON file produced by path_index_bin2json.py")
    parser.add_argument("--out", dest="output", default=DEFAULT_OUT,
                        help="output path_index.bin")
    parser.add_argument("--samples", type=int, default=100000,
                        help="entries to check after building")
    parser.add_argument("--no-verify", action="store_true",
                        help="skip the post-build verification")
    args = parser.parse_args(argv)

    if not os.path.exists(args.input):
        parser.error("input not found: %s" % args.input)
    paths = load_paths(args.input)
    print("input paths %d" % len(paths), flush=True)
    build_from_paths(paths, args.output)
    if not args.no_verify:
        verify(paths, args.output, args.samples)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
