#!/usr/bin/env python3
"""Score a candidate bit-layout for the NeoX v4 115-bit position block.

The x and z fields of this layout are still unknown (see
`docs/neox_mesh_v4_115bit.md`). Rather than guessing, this harness takes a mesh
file plus an independently produced reference list of the *same* mesh's vertex
positions, and reports how well a candidate decode matches it. Try alternative
field definitions until all three axes match.

Usage
-----
    # score the layout currently implemented
    python tools/verify_eggparty_positions.py sample.mesh reference.npy

    # score an explicit layout: three (low, high) bit-range pairs
    python tools/verify_eggparty_positions.py sample.mesh reference.npy \\
        --fields 8-18,20-27 27-38,40-47 48-58,60-64 --z-wrap 0,1,2:15

Reference
---------
`reference.npy` is an `(n, 3)` float array whose rows correspond, in order, to the
*first* `n` vertices of the mesh file. Any independently produced export of the
same asset works; see `docs/neox_mesh_v4_115bit.md` for how the layout is scored.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

import numpy as np

# Loaded straight from its file: importing the package would pull in the Qt
# logging stack, which this tool has no use for.
_LAYOUT = Path(__file__).resolve().parents[1] / "core/mesh_loader/parsers/eggparty_layout.py"
_spec = importlib.util.spec_from_file_location("eggparty_layout", _LAYOUT)
assert _spec and _spec.loader
layout = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(layout)

BITS_PER_VERTEX = layout.BITS_PER_VERTEX
MESH_MAGIC = layout.MESH_MAGIC
POSITION_FIELDS = layout.POSITION_FIELDS
Z_WRAP_BITS = layout.Z_WRAP_BITS
Z_WRAP_SHIFT = layout.Z_WRAP_SHIFT


def parse_bits(data: bytes) -> tuple[np.ndarray, int, int]:
    """Return the packed 64-bit records, the vertex count and data start."""
    if int.from_bytes(data[0:4], "little") != MESH_MAGIC:
        raise SystemExit("not a NeoX mesh file")
    bone_exist = int.from_bytes(data[8:12], "little")
    if not bone_exist:
        raise SystemExit("mesh has no bone block; cannot walk the header")

    bone_count = int.from_bytes(data[12:14], "little")
    p = 14 + bone_count + bone_count * 32
    if data[p]:
        p += 28 * bone_count
    p += 1 + 64 * bone_count + 1
    p += 4  # end_offset

    while int.from_bytes(data[p:p + 2], "little") != 1:
        p += 10
    p += 2
    vertex_count = int.from_bytes(data[p:p + 4], "little")
    p += 8

    geometry_size = (BITS_PER_VERTEX * vertex_count + 16) // 8
    records = np.frombuffer(data, dtype=np.uint8, count=vertex_count * 8, offset=p)
    bits = np.unpackbits(records, bitorder="little").reshape(vertex_count, 64)
    return bits, vertex_count, p + geometry_size


def _field(bits: np.ndarray, spec: str) -> np.ndarray:
    low, _, high = spec.replace("..", "-").partition("-")
    return bits[:, int(low):int(high)].astype(np.uint64)


def _to_int(field: np.ndarray) -> np.ndarray:
    weights = 1 << np.arange(field.shape[1], dtype=np.uint64)
    return (field * weights).sum(axis=1)


def decode(bits: np.ndarray, fields, z_wrap, z_wrap_shift) -> np.ndarray:
    columns = []
    for low, high in fields:
        value = _to_int(_field(bits, low))
        high_bits = _to_int(_field(bits, high))
        low_width = int(low.replace("..", "-").partition("-")[2]) - int(
            low.replace("..", "-").partition("-")[0]
        )
        columns.append(value + (high_bits << low_width))
    if z_wrap:
        wrapped = _to_int(bits[:, list(z_wrap)])
        columns[2] = columns[2] + (wrapped << z_wrap_shift)
    return np.stack(columns, axis=1).astype(np.float64)


def score(decode: np.ndarray, truth: np.ndarray) -> dict:
    """Compare a decode against the reference, per axis and overall."""
    n = min(len(decode), len(truth))
    decode, truth = decode[:n], truth[:n]
    result = {}
    for k, name in enumerate("xyz"):
        d, t = decode[:, k], truth[:, k]
        # normalise both to [0, 1] so only the *shape* of the distribution is
        # compared, not the scale or offset
        dn = (d - d.min()) / max(d.max() - d.min(), 1e-9)
        tn = (t - t.min()) / max(t.max() - t.min(), 1e-9)
        quantiles = np.linspace(0, 1, 21)
        deviation = float(np.abs(np.quantile(dn, quantiles) - np.quantile(tn, quantiles)).max())
        correlation = float(np.corrcoef(dn, tn)[0, 1]) if dn.std() and tn.std() else 0.0
        result[name] = {"deviation": deviation, "correlation": correlation}
    result["span_ratio"] = (decode.max(0) - decode.min(0)) / max(
        (truth.max(0) - truth.min(0)).max(), 1e-9
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mesh", type=Path, help=".mesh file to decode")
    parser.add_argument("truth", type=Path, help="reference vertex list (n, 3) .npy")
    parser.add_argument(
        "--fields",
        nargs=3,
        metavar=("X", "Y", "Z"),
        help="three 'low-high,low-high' bit ranges, e.g. 8-18,20-27",
    )
    parser.add_argument(
        "--z-wrap",
        default=":".join([",".join(str(b) for b in Z_WRAP_BITS), str(Z_WRAP_SHIFT)]),
        help="z wrap-around bits and shift, e.g. '0,1,2:15'",
    )
    args = parser.parse_args()

    if args.fields:
        fields = tuple(tuple(part.split(",")) for part in args.fields)
    else:
        fields = tuple(
            (f"{lo}-{hi}", f"{hlo}-{hhi}")
            for (lo, hi), (hlo, hhi) in POSITION_FIELDS
        )

    wrap_bits, _, wrap_shift = args.z_wrap.partition(":")
    z_wrap = [int(b) for b in wrap_bits.split(",") if b.strip()]

    bits, vertex_count, _ = parse_bits(args.mesh.read_bytes())
    truth = np.load(args.truth).astype(np.float64)
    decoded = decode(bits, fields, z_wrap, int(wrap_shift or 0))

    print(f"mesh     : {args.mesh.name}  ({vertex_count} vertices)")
    print(f"truth    : {args.truth.name}  {truth.shape}")
    print(f"fields   : {fields}")
    print(f"z wrap   : bits {z_wrap} << {wrap_shift}")
    print()
    print(f"{'axis':>5} {'deviation':>10} {'correlation':>12}")
    scores = score(decoded, truth)
    for name in "xyz":
        row = scores[name]
        flag = "OK" if row["deviation"] < 0.10 and row["correlation"] > 0.9 else "<-- wrong"
        print(f"{name:>5} {row['deviation']:>10.3f} {row['correlation']:>12.3f}  {flag}")
    print()
    print(f"span ratio (decoded / truth): {np.round(scores['span_ratio'], 3)}")
    print("a correct layout gives deviation < 0.10 and correlation > 0.9 on all axes")


if __name__ == "__main__":
    main()
