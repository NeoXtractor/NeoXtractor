"""Pure helpers for the NeoX v4 mesh layout (EggParty).

Format reference
--------------------------------------------------------------------------
Mesh header: magic ``0xBBC88034``, version 4, bone block, ``end_offset``,
then a submesh descriptor table (10-byte entries until a ``must_be_one == 1``
terminator, followed by total vertex/face counts).

After the header the vertex payload is a single sequential byte stream
eaten pass by pass (``vc`` = vertices, ``fc`` = faces)::

    flag       1 byte
    positions  vc*8  quantized 20-bit fields, or vc*12 raw float32 triples
    normals    vc*3  signed-byte snorm triples (version 4 always)
    tangents   T     optional: 3*vc signed bytes + ceil(3*vc/8) bitmap
                     (+1 phantom byte when 3*vc is a multiple of 8)
    faces      fc*6  optional: three int16 deltas, global prefix sum
    uvs        4*sum(layers*vc_s)   float16 pairs
    colors     4*sum(color*vc_s)    optional raw u32 per vertex
    tail       12*vc + 32           optional: 4xu8 joints + 4xu16 weights

The flag byte gates the stream: bit 0 selects the position encoding
(quantized vs raw float32), bit 1 clear keeps the face pass present,
bit 2 clear keeps the tangent pass present.  The tangent pass size is
``T = 3*vc + ceil(3*vc/8) + (1 if 3*vc % 8 == 0 else 0)``.

Position records (bit 0 clear): one 64-bit LE value per vertex holding
three consecutive 20-bit fields (X at bits 0-19, Y at 20-39, Z at
40-59; bits 60-63 unused).  Each field is a signed fixed-point value:
a 19-bit magnitude (bits 0-18) divided by 2048, negated when bit 19 is
set.
"""

from __future__ import annotations

import numpy as np

MESH_MAGIC = 0xBBC88034

#: Fixed-point scale: 19-bit magnitude divided by 2048.
_POS_SCALE = 1.0 / 2048.0

#: snorm8 divisor shared by the normal and tangent component bytes.
_SNORM8 = 127.0


def decode_positions(blob: bytes, vertex_count: int) -> np.ndarray:
    """Decode v4 positions from the geometry section (flag byte included).

    Returns an (vertex_count, 3) float64 array in model space.
    """
    if len(blob) < 1:
        raise ValueError("position block truncated")
    flag = blob[0]
    body = blob[1:]
    if flag & 1:
        needed = vertex_count * 12
        if len(body) < needed:
            raise ValueError("position block truncated")
        return (
            np.frombuffer(body, dtype="<f4", count=vertex_count * 3)
            .reshape(vertex_count, 3)
            .astype(np.float64)
        )
    needed = vertex_count * 8
    if len(body) < needed:
        raise ValueError("position block truncated")
    records = np.frombuffer(body, dtype="<u8", count=vertex_count)
    positions = np.empty((vertex_count, 3), dtype=np.float64)
    for coord in range(3):
        field = (records >> (coord * 20)) & 0xFFFFF
        magnitude = (field & 0x7FFFF).astype(np.float64)
        sign = (field >> 19) & 1
        value = magnitude * _POS_SCALE
        positions[:, coord] = np.where(sign, -value, value)
    return positions


def decode_normals(blob: bytes) -> np.ndarray:
    """Decode the snorm8 normal pass (3 signed bytes per vertex).

    Returns an (n, 3) float64 array of unit-ish direction vectors.
    """
    if len(blob) % 3:
        raise ValueError("normal block truncated")
    raw = np.frombuffer(blob, dtype=np.int8).reshape(-1, 3).astype(np.float64)
    normals = raw / _SNORM8
    lengths = np.linalg.norm(normals, axis=1, keepdims=True)
    lengths[lengths == 0] = 1.0
    return normals / lengths


def tangent_size(vertex_count: int) -> int:
    """Byte size of the tangent pass for ``vertex_count`` vertices.

    Layout: 3 attribute bytes per vertex, then one bitmap bit per
    attribute byte (3*vc bits), plus one phantom byte the engine's
    bitmap pre-fetch leaves behind when 3*vc is a multiple of 8.
    """
    bits = 3 * vertex_count
    size = bits + (bits + 7) // 8
    if bits % 8 == 0:
        size += 1
    return size


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