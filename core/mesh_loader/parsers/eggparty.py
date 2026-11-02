"""Parser for the NeoX mesh v4 "115-bit per vertex" layout used by EggParty.

`identify_mesh_type()` has no branch for this layout, so `MeshParser0` bails out
with `NotImplementedError` on every mesh from the EggParty NPKs. This module
implements the parts of the format that have been verified against an
independently produced reference export of the same mesh; see
`docs/neox_mesh_v4_115bit.md` for the byte-level evidence and for the one
outstanding problem: **the x and z bit fields are still not correct**.

The parser is deliberately additive: it is registered *after* `MeshParser0`, so
files that already parse keep taking the old path.
"""

from __future__ import annotations

import numpy as np

from core.logger import get_logger
from core.mesh_loader.parsers.eggparty_layout import (
    BITS_PER_VERTEX,
    MESH_MAGIC,
    decode_faces,
    decode_positions,
    decode_uv,
)
from core.mesh_loader.types import BaseMeshParser, Bones, Mesh, MeshData


class EggPartyMeshParser(BaseMeshParser):
    """Parser for the 115-bits-per-vertex NeoX v4 mesh variant."""

    def parse(self, data: bytes) -> MeshData:
        """Parse mesh."""
        return self._parse(data)

    # ------------------------------------------------------------------
    # header

    def _read_header(self, data: bytes) -> dict:
        """Parse everything up to (but not including) the geometry block.

        The submesh table sits immediately after the bone data; `end_offset`
        is only the end of the geometry, so nothing needs seeking.
        """
        if len(data) < 16 or int.from_bytes(data[0:4], "little") != MESH_MAGIC:
            raise ValueError("not a NeoX mesh file")
        version = int.from_bytes(data[4:6], "little")
        if version != 4:
            raise ValueError(f"unsupported mesh version {version}")

        bone_exist = int.from_bytes(data[8:12], "little")
        p = 12
        bones = {
            "has_bones": bone_exist,
            "count": 0,
            "parents": [],
            "names": [],
            "matrix": [],
        }
        if bone_exist:
            if bone_exist > 1:
                # Other games prefix the bone block with a small lookup table.
                count = data[p]
                p += 3 + count * 4
            bone_count = bones["count"] = int.from_bytes(data[p:p + 2], "little")
            p += 2
            bones["parents"] = [
                (-1 if b == 0xFF else b) for b in data[p:p + bone_count]
            ]
            p += bone_count
            bones["names"] = [
                data[p + i * 32:p + i * 32 + 32]
                .split(b"\x00")[0]
                .decode("utf-8", "replace")
                .replace(" ", "_")
                for i in range(bone_count)
            ]
            p += bone_count * 32
            if data[p]:
                p += 28 * bone_count
            p += 1
            raw = np.frombuffer(data, dtype="<f4", count=bone_count * 16, offset=p)
            bones["matrix"] = list(raw.reshape(bone_count, 4, 4))
            p += 64 * bone_count
            p += 1  # flag after the bone block, always 0

        end_offset = int.from_bytes(data[p:p + 4], "little")
        p += 4

        submeshes: list[tuple[int, int, int, int]] = []
        while True:
            if p + 10 > len(data):
                raise ValueError("submesh table overran the file")
            if int.from_bytes(data[p:p + 2], "little") == 1:
                p += 2
                break
            submeshes.append((
                int.from_bytes(data[p:p + 4], "little"),
                int.from_bytes(data[p + 4:p + 8], "little"),
                data[p + 8],
                data[p + 9],
            ))
            p += 10

        vertex_count = int.from_bytes(data[p:p + 4], "little")
        face_count = int.from_bytes(data[p + 4:p + 8], "little")
        p += 8

        return {
            "bones": bones,
            "end_offset": end_offset,
            "submeshes": submeshes,
            "vertex_count": vertex_count,
            "face_count": face_count,
            "data_start": p,
        }

    # ------------------------------------------------------------------
    # body

    def _parse(self, data: bytes) -> MeshData:
        header = self._read_header(data)

        vertex_count = header["vertex_count"]
        face_count = header["face_count"]
        data_start = header["data_start"]
        end_offset = header["end_offset"]

        geometry_size = (BITS_PER_VERTEX * vertex_count + 16) // 8
        faces_offset = data_start + geometry_size
        uv_offset = faces_offset + face_count * 6

        uv_total = sum(layers * count for count, _, layers, _ in header["submeshes"])
        color_total = sum(color * count for count, _, _, color in header["submeshes"])
        uv_size = 4 * uv_total
        # ~28% of the pet_low meshes carry a 4-bytes-per-vertex colour section
        # between the UV block and the tail. Missing it shifts every following
        # section, which corrupts the joint/weight tail.
        color_size = 4 * color_total
        tail_size = 12 * vertex_count + 32
        tail_offset = uv_offset + uv_size + color_size
        has_tail = end_offset - tail_offset == tail_size

        positions = decode_positions(data[data_start:faces_offset], vertex_count)
        faces = decode_faces(data[faces_offset:uv_offset], face_count)
        if faces.size and (faces.min() < 0 or faces.max() >= vertex_count):
            raise ValueError("face indices out of range")

        uv = decode_uv(data[uv_offset:uv_offset + uv_size], uv_total)
        if uv is None:
            uv = np.zeros((vertex_count, 2))

        joints = np.zeros((vertex_count, 4), dtype=np.int64)
        weights = np.zeros((vertex_count, 4))
        if has_tail and header["bones"]["has_bones"] in (1, 4):
            tail = data[tail_offset:tail_offset + tail_size]
            joints = np.frombuffer(tail, dtype=np.uint8, count=vertex_count * 12)
            joints = joints.reshape(vertex_count, 12)[:, :4].astype(np.int64)
            raw_weights = np.frombuffer(tail, dtype="<u2", count=vertex_count * 4, offset=4)
            raw_weights = raw_weights.reshape(vertex_count, 4).astype(np.float64)
            bad = (joints < 0) | (joints >= max(header["bones"]["count"], 1))
            raw_weights = np.where(bad, 0.0, raw_weights)
            totals = raw_weights.sum(axis=1, keepdims=True)
            totals[totals == 0] = 1.0
            weights = raw_weights / totals
            joints = np.where(bad, 0, joints)

        normals = np.zeros((vertex_count, 3))
        triangles = positions[faces]
        face_normals = np.cross(
            triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]
        )
        for k in range(3):
            np.add.at(normals, faces[:, k], face_normals)
        lengths = np.linalg.norm(normals, axis=1, keepdims=True)
        lengths[lengths == 0] = 1.0
        normals = normals / lengths

        get_logger().info(
            "MESH: v4/115-bit | VERTS: %d | FACES: %d | UV: %d",
            vertex_count,
            face_count,
            uv_total,
        )

        mesh = Mesh(vertexes=vertex_count, faces=face_count)
        mesh.position = [tuple(v) for v in positions]
        mesh.normal = [tuple(v) for v in normals]
        mesh.face = [tuple(int(x) for x in face) for face in faces]
        mesh.uv = [tuple(v) for v in uv]

        bones = Bones()
        bones.has_bones = 1 if header["bones"]["has_bones"] else 0
        bones.count = header["bones"]["count"]
        bones.parents = list(header["bones"]["parents"])
        bones.names = list(header["bones"]["names"])
        bones.matrix = [m for m in header["bones"]["matrix"]]
        bones.joints = [tuple(int(x) for x in row) for row in joints]
        bones.weights = [tuple(float(x) for x in row) for row in weights]

        return MeshData(version=4, type=1, mesh=mesh, bones=bones)
