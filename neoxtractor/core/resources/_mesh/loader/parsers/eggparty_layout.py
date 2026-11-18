"""Pure helpers for the NeoX v4 115-bits-per-vertex mesh layout (EggParty).

Kept free of project imports so the layout can be unit-tested and scored by
``tools/verify_eggparty_positions.py`` without pulling the logging stack.

Format reference (verified against real samples and an independent export)
--------------------------------------------------------------------------
Container: NXPK archives with ``encrypt_mode == 3`` (EggParty index cipher).
Mesh header: magic ``0xBBC88034``, version 4, bone block, ``end_offset``,
then a submesh descriptor table (10-byte entries until a ``must_be_one == 1``
terminator, followed by total vertex/face counts).

Sections follow with no padding (``vc`` = total vertices, ``fc`` = faces)::

    geometry  (115*vc + 16)//8     bit-packed positions, 64-bit records
    faces     6*fc                 three int16 deltas, global prefix sum
    uv        4*sum(layers*vc_s)   float16 pairs
    colors    4*sum(color*vc_s)    optional, ~28% of pet_low meshes
    tail      12*vc + 32           optional: 4xu8 joints + 4xu16 weights

The section identity is exact on 1651 sampled meshes. Faces reproduce an
independent FBX export triangle-for-triangle (14330/14330); UVs match the
reference within 1e-5.

Position bit layout: each coordinate is two fields separated by one
always-zero bit; the third coordinate's high block wraps to the record
start. Only the y pair is verified (quantile deviation 0.049, correlation
0.967). The x and z pairs are placeholders: models render as thin slabs
until the true fields are found. Per-bit correlation hints: the real x
high bit is 27 (not 26) and the real z wrap reaches bit 3 (not 2); a second
non-degenerate reference sample is needed to resolve them. Score candidate
layouts with ``tools/verify_eggparty_positions.py``.
"""

from __future__ import annotations

import numpy as np

MESH_MAGIC = 0xBBC88034

#: Bits consumed by one vertex in the packed geometry block.
BITS_PER_VERTEX = 115

#: Each coordinate is stored as two fields separated by one always-zero bit.
#: The third coordinate's high block wraps around to the start of the record.
#:
#: NOTE: only the *y* pair below is verified against a reference export. The x and z
#: pairs are placeholders - see the module docstring.
POSITION_FIELDS = (
    ((8, 19), (20, 27)),
    ((27, 39), (40, 47)),
    ((48, 59), (60, 64)),
)

#: ``z`` also picks up bits 0..2 of the *same* record (wrap-around).
Z_WRAP_BITS = (0, 1, 2)
Z_WRAP_SHIFT = 15


def bits_to_int(bits: np.ndarray) -> np.ndarray:
    """Little-endian bit columns to an unsigned integer per row."""
    weights = 1 << np.arange(bits.shape[1], dtype=np.uint64)
    return (bits.astype(np.uint64) * weights).sum(axis=1)


def unpack_records(blob: bytes, vertex_count: int) -> np.ndarray:
    """Return the `vertex_count x 64` little-endian bit matrix of the block."""
    needed = vertex_count * 8
    if len(blob) < needed:
        raise ValueError("position block truncated")
    records = np.frombuffer(blob, dtype=np.uint8, count=needed)
    return np.unpackbits(records, bitorder="little").reshape(vertex_count, 64)


def decode_positions(
    blob: bytes,
    vertex_count: int,
    fields=POSITION_FIELDS,
    z_wrap_bits=Z_WRAP_BITS,
    z_wrap_shift=Z_WRAP_SHIFT,
) -> np.ndarray:
    """Decode the bit-packed position block.

    NOTE: the x and z fields are known to be wrong; the model comes out far too
    thin on those axes. The y field is verified.
    """
    bits = unpack_records(blob, vertex_count)

    columns = []
    for (low_a, low_b), (high_a, high_b) in fields:
        value = bits_to_int(bits[:, low_a:low_b])
        high = bits_to_int(bits[:, high_a:high_b])
        columns.append(value + (high << (low_b - low_a)))

    if z_wrap_bits:
        wrapped = bits_to_int(bits[:, list(z_wrap_bits)])
        columns[2] = columns[2] + (wrapped << z_wrap_shift)

    return np.stack(columns, axis=1).astype(np.int64).astype(np.float64)


def decode_faces(blob: bytes, face_count: int) -> np.ndarray:
    """Face indices are three signed 16-bit deltas, prefix-summed globally."""
    needed = face_count * 6
    if len(blob) < needed:
        raise ValueError("face block truncated")
    deltas = np.frombuffer(blob, dtype="<i2", count=face_count * 3).astype(np.int64)
    return np.cumsum(deltas).reshape(-1, 3)


def decode_uv(blob: bytes, count: int) -> np.ndarray | None:
    """UVs are pairs of float16. Returns None when the block is not sane."""
    needed = count * 4
    if count <= 0 or len(blob) < needed:
        return None
    flat = np.frombuffer(blob, dtype="<f2", count=count * 2).astype(np.float64)
    uv = flat.reshape(-1, 2)
    if not np.isfinite(uv).all() or uv.min() < -0.01 or uv.max() > 1.01:
        return None
    return uv