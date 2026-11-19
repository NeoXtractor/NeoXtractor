"""Parser for the NeoX mesh v4 layout used by EggParty.

``MeshParser0`` has no branch for this layout and fails on every EggParty
mesh.  This parser walks the vertex payload the way the engine does: one
sequential pass stream (flag, positions, normals, optional tangents,
optional faces, UVs, optional colours, optional skinning tail).  See
``eggparty_layout`` for the byte-level format reference.

The parser is registered *after* ``MeshParser0``: files that already parse
keep taking the original path.
"""

from __future__ import annotations

import numpy as np

from neoxtractor.core.utils.logger import get_logger
from neoxtractor.core.resources._mesh.loader.types import (
    BaseMeshParser,
    Bones,
    Mesh,
    MeshData,
)

from .eggparty_layout import (
    MESH_MAGIC,
    decode_faces,
    decode_normals,
    decode_positions,
    decode_uv,
    tangent_size,
)


class EggPartyMeshParser(BaseMeshParser):
    """Parser for the NeoX v4 sequential-pass mesh layout."""

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

        flag = data[data_start]
        pos = data_start + 1

        pos_size = vertex_count * (12 if flag & 1 else 8)
        positions = decode_positions(
            data[data_start:data_start + 1 + pos_size], vertex_count
        )
        pos += pos_size

        normals = decode_normals(data[pos:pos + vertex_count * 3])
        pos += vertex_count * 3

        # bit 2 clear keeps the tangent pass in the stream
        if not flag & 4:
            pos += tangent_size(vertex_count)

        faces = np.zeros((0, 3), dtype=np.int64)
        # bit 1 clear keeps the face pass in the stream
        if not flag & 2:
            faces = decode_faces(data[pos:pos + face_count * 6], face_count)
            if faces.size and (faces.min() < 0 or faces.max() >= vertex_count):
                raise ValueError("face indices out of range")
            pos += face_count * 6

        uv_total = sum(layers * count for count, _, layers, _ in header["submeshes"])
        color_total = sum(color * count for count, _, _, color in header["submeshes"])
        uv_size = 4 * uv_total
        # Some meshes carry a 4-bytes-per-vertex colour section between the
        # UV block and the skinning tail.  Missing it shifts every following
        # section, which corrupts the joint/weight data.
        color_size = 4 * color_total
        uv = decode_uv(data[pos:pos + uv_size], uv_total)
        if uv is None:
            uv = np.zeros((vertex_count, 2))
        pos += uv_size + color_size

        tail_size = 12 * vertex_count + 32
        has_tail = end_offset - pos == tail_size

        joints = np.zeros((vertex_count, 4), dtype=np.int64)
        weights = np.zeros((vertex_count, 4))
        if has_tail and header["bones"]["has_bones"] in (1, 4):
            tail = data[pos:pos + tail_size]
            joint_bytes = np.frombuffer(tail, dtype=np.uint8, count=vertex_count * 12)
            joints = joint_bytes.reshape(vertex_count, 12)[:, :4].astype(np.int64)
            weight_raw = np.frombuffer(tail, dtype="<u2", count=vertex_count * 4, offset=4)
            raw_weights = weight_raw.reshape(vertex_count, 4).astype(np.float64)
            bad = (joints < 0) | (joints >= max(header["bones"]["count"], 1))
            raw_weights = np.where(bad, 0.0, raw_weights)
            totals = raw_weights.sum(axis=1, keepdims=True)
            totals[totals == 0] = 1.0
            weights = raw_weights / totals
            joints = np.where(bad, 0, joints)

        get_logger().debug(
            "MESH: v4 pass stream | VERTS: %d | FACES: %d | UV: %d | FLAG: 0x%02x",
            vertex_count,
            face_count,
            uv_total,
            flag,
        )

        mesh = Mesh(vertexes=vertex_count, faces=face_count)
        mesh.position = [(float(v[0]), float(v[1]), float(v[2])) for v in positions]
        mesh.normal = [(float(v[0]), float(v[1]), float(v[2])) for v in normals]
        mesh.face = [(int(f[0]), int(f[1]), int(f[2])) for f in faces]
        mesh.uv = [(float(v[0]), float(v[1])) for v in uv]

        bones = Bones()
        bones.has_bones = 1 if header["bones"]["has_bones"] else 0
        bones.count = header["bones"]["count"]
        bones.parents = list(header["bones"]["parents"])
        bones.names = list(header["bones"]["names"])
        bones.matrix = [m for m in header["bones"]["matrix"]]
        bones.joints = [
            (int(r[0]), int(r[1]), int(r[2]), int(r[3])) for r in joints
        ]
        bones.weights = [
            (float(r[0]), float(r[1]), float(r[2]), float(r[3])) for r in weights
        ]

        return MeshData(version=4, type=1, mesh=mesh, bones=bones)