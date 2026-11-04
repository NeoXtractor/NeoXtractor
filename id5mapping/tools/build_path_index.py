from __future__ import annotations

import argparse
import array
import json
import os
import random
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from id5mapping.paths.path_restore import xxh32 as h32

DEFAULT_MAP = os.path.join(HERE, "vfs_path_map.json")
DEFAULT_OUT = os.path.join(ROOT, "id5mapping", "paths", "path_index.bin")

SEED_KEY, SEED_ITEM = 163, 16361
SEED_G, SEED_F = 0x1234567, 0x89ABCDEF
THETA = 4
SLACK = 1.10
EMPTY = 0xFFFFFFFF
HDR_FMT = "<8s18I"
HDR_LEN = struct.calcsize(HDR_FMT)
MAGIC = b"NXTR\0\0\0\0"
NGRP = 32


def rel_of(path: str) -> str:
    return path.split("/", 1)[1] if "/" in path else path


def cstr(buf: bytes, off: int) -> bytes:
    return buf[off:buf.find(b"\x00", off)]


def build(map_path: str, out_path: str) -> None:
    vmap = json.load(open(map_path, encoding="utf-8"))
    build_from_paths(list(vmap.values()), out_path)


def build_from_paths(paths: list[str], out_path: str) -> None:
    t0 = time.time()
    groups: dict[int, set[str]] = {}
    for path in paths:
        key = h32(rel_of(path).encode(), SEED_KEY)
        groups.setdefault(key, set()).add(path)
    keys = list(groups.keys())
    n = len(keys)
    npath = sum(len(v) for v in groups.values())
    print("keys %d paths %d (%.1fs)" % (n, npath, time.time() - t0), flush=True)

    m = int(n * SLACK) | 1
    B = max(1, n // THETA)
    gs, fs = [0] * n, [0] * n
    for i, k in enumerate(keys):
        kb = struct.pack("<I", k)
        gs[i] = h32(kb, SEED_G)
        fs[i] = h32(kb, SEED_F)
    gm = [g % m for g in gs]
    fm = [f % m for f in fs]
    buckets: list[list[int]] = [[] for _ in range(B)]
    for i in range(n):
        buckets[gs[i] % B].append(i)
    order = sorted(range(B), key=lambda b: -len(buckets[b]))
    used = bytearray(m)
    dtab = array.array("H", bytes(2 * B))
    slot_i = array.array("i", bytes(4 * m))
    for i in range(m):
        slot_i[i] = -1
    overflow = 0
    for b in order:
        idxs = buckets[b]
        if not idxs:
            continue
        d = 0
        while d < 200000:
            ok = True
            hus = []
            for i in idxs:
                hu = (gm[i] + d * fm[i] % m) % m
                if used[hu]:
                    ok = False
                    break
                hus.append(hu)
            if ok:
                break
            d += 1
        else:
            overflow += len(idxs)
            continue
        dtab[b] = d
        for hu, i in zip(hus, idxs):
            used[hu] = 1
            slot_i[hu] = i
    print("chd overflow=%d (%.1fs)" % (overflow, time.time() - t0), flush=True)

    dirs: dict[str, int] = {}
    recs = []
    for k in keys:
        for p in groups[k]:
            if "/" in p:
                pre, nm = p.rsplit("/", 1)
                pre += "/"
            else:
                pre, nm = "", p
            d = dirs.get(pre)
            if d is None:
                d = len(dirs)
                dirs[pre] = d
            recs.append((nm, d, k, p))
    ND = len(dirs)
    didx = array.array("I", bytes(4 * ND))
    pbuf = bytearray()
    for pre, d in sorted(dirs.items(), key=lambda kv: kv[1]):
        didx[d] = len(pbuf)
        pbuf += pre.encode() + b"\x00"
    print("dirs %d buf %d (%.1fs)" % (ND, len(pbuf), time.time() - t0), flush=True)

    recs.sort()
    ebuf = bytearray()
    ngtab = array.array("I", [])
    first = b""
    ng = -1
    ent_off = array.array("I", bytes(4 * len(recs)))
    ent_gid = array.array("I", bytes(4 * len(recs)))
    for j, (nm, d, k, p) in enumerate(recs):
        nb = nm.encode()
        if j % NGRP == 0:
            first = nb
            ng += 1
            ngtab.append(len(ebuf))
            lcp, suffix = 0, nb
        else:
            mx = min(len(first), len(nb))
            lcp = 0
            while lcp < mx and first[lcp] == nb[lcp]:
                lcp += 1
            suffix = nb[lcp:]
        ent_off[j] = len(ebuf)
        ent_gid[j] = ng
        ebuf.append(lcp)
        ebuf += suffix + b"\x00"
    print("names %d groups %d buf %d (%.1fs)"
          % (len(recs), ng + 1, len(ebuf), time.time() - t0), flush=True)

    pos_of = {}
    for j, (nm, d, k, p) in enumerate(recs):
        pos_of[(k, p)] = j
    slot_rec = array.array("I", bytes(4 * m))
    slot_gid = array.array("H", bytes(2 * m))
    slot_dir = array.array("H", bytes(2 * m))
    for i in range(m):
        slot_rec[i] = EMPTY
    ovf = []
    for i, k in enumerate(keys):
        ps = sorted(groups[k])
        gm_i, fm_i = gm[i], fm[i]
        d = dtab[gs[i] % B]
        hu = (gm_i + d * fm_i % m) % m
        if slot_i[hu] != i:
            for p in ps:
                j = pos_of[(k, p)]
                ovf.append((k, recs[j][1], ent_off[j]))
            continue
        j0 = pos_of[(k, ps[0])]
        slot_rec[hu] = ent_off[j0]
        slot_gid[hu] = ent_gid[j0]
        slot_dir[hu] = recs[j0][1]
        for p in ps[1:]:
            j = pos_of[(k, p)]
            ovf.append((k, recs[j][1], ent_off[j]))
    ovf.sort()
    NOVF = len(ovf)
    ovf_arr = array.array("I", bytes(4 * NOVF))
    ovf_dir = array.array("H", bytes(2 * NOVF))
    ovf_ent = array.array("I", bytes(4 * NOVF))
    for x, (k, d, o) in enumerate(ovf):
        ovf_arr[x] = k
        ovf_dir[x] = d
        ovf_ent[x] = o
    print("ovf %d (%.1fs)" % (NOVF, time.time() - t0), flush=True)

    dtab_off = HDR_LEN
    slot_off = dtab_off + 2 * B
    gid_off = slot_off + 4 * m
    dir_off = gid_off + 2 * m
    ngt_off = dir_off + 2 * m
    didx_off = ngt_off + 4 * len(ngtab)
    pbuf_off = didx_off + 4 * ND
    ebuf_off = pbuf_off + len(pbuf)
    ovf_off = ebuf_off + len(ebuf)
    hdr = struct.pack(HDR_FMT, MAGIC, n, m, B, SEED_G, SEED_F,
                      len(ngtab), ND, NOVF, dtab_off, slot_off, gid_off,
                      dir_off, ngt_off, didx_off, pbuf_off, ebuf_off, ovf_off,
                      int(time.time()))
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(hdr)
        f.write(dtab.tobytes())
        f.write(slot_rec.tobytes())
        f.write(slot_gid.tobytes())
        f.write(slot_dir.tobytes())
        f.write(ngtab.tobytes())
        f.write(didx.tobytes())
        f.write(bytes(pbuf))
        f.write(bytes(ebuf))
        f.write(ovf_arr.tobytes())
        f.write(ovf_dir.tobytes())
        f.write(ovf_ent.tobytes())
    print("output %s %d bytes (%.1fs)"
          % (out_path, os.path.getsize(out_path), time.time() - t0), flush=True)


def verify(map_path: str, out_path: str, samples: int) -> None:
    from id5mapping.paths.path_restore import PathHashIndex

    t0 = time.time()
    idx = PathHashIndex(out_path)
    vmap = json.load(open(map_path, encoding="utf-8"))
    items = list(vmap.items())
    rnd = random.Random(1)
    ok = bad = 0
    for _, path in rnd.sample(items, min(samples, len(items))):
        rel = rel_of(path)
        got = idx.lookup(h32(rel.encode(), SEED_KEY), h32(rel.encode(), SEED_ITEM))
        if got == path:
            ok += 1
        else:
            bad += 1
            if bad <= 5:
                print("  MISMATCH", path, "->", got)
    print("verify ok=%d bad=%d (%.1fs)" % (ok, bad, time.time() - t0), flush=True)
    false_hit = 0
    for _ in range(20000):
        if idx.lookup(rnd.getrandbits(32), rnd.getrandbits(32)) is not None:
            false_hit += 1
    print("false_hit %d / 20000" % false_hit)
    size = os.path.getsize(out_path)
    print("size %d bytes  (%.2f bytes/key)" % (size, idx.n))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build the NXTR path index bundled with NeoXtractor.")
    parser.add_argument("--map", default=DEFAULT_MAP,
                        help="vfs_path_map.json produced by make_vfs_path_map.py")
    parser.add_argument("--out", default=DEFAULT_OUT,
                        help="output .bin (defaults to id5mapping/paths/path_index.bin)")
    parser.add_argument("--samples", type=int, default=100000,
                        help="entries to check after building")
    parser.add_argument("--no-verify", action="store_true",
                        help="skip the post-build verification")
    args = parser.parse_args(argv)

    if not os.path.exists(args.map):
        parser.error("map file not found: %s" % args.map)
    build(args.map, args.out)
    if not args.no_verify:
        verify(args.map, args.out, args.samples)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
