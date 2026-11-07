"""FBX Mesh Format Converter

Writes a self-contained binary FBX 7.4 file (no Autodesk SDK required) with:
- mesh geometry, per-vertex normals and UVs
- a single default material
- the skeleton as LimbNode models (when the mesh has bones)
- a skin deformer with one cluster per bone, plus a bind pose

Coordinate conventions match the in-app mesh viewer: NeoX meshes are
left-handed, so X is mirrored and triangle winding is reversed to produce a
right-handed, Y-up scene. UV V is flipped because FBX uses a bottom-left origin.
"""

import math
import struct
import zlib
from typing import Any

import numpy as np

from core.mesh_loader import MeshData

NAME = "Kaydara (FBX) Format"
EXTENSION = ".fbx"

_FBX_VERSION = 7400

# Fixed header/footer identifiers. Binary FBX readers (including the Autodesk
# SDK) accept this FileId/CreationTime/footer combination; it is the same set
# Blender's FBX exporter writes.
_FILE_ID = b"\x28\xb3\x2a\xeb\xb6\x24\xcc\xc2\xbf\xc8\xb0\x2a\xa9\x2b\xfc\xf1"
_TIME_ID = "1970-01-01 10:00:00:000"
_FOOT_ID = b"\xfa\xbc\xab\x09\xd0\xc8\xd4\x66\xb1\x76\xfb\x83\x1c\xf7\x26\x7e"
_FOOTER_MAGIC = b"\xf8\x5a\x8c\x6a\xde\xf5\xd9\x7e\xec\xe9\x0c\xe3\x75\x8f\x29\x0b"

# Joint index values NeoX uses to mark an unused bone slot.
_UNUSED_JOINTS = (255, 65535)

# Mirror X: converts NeoX left-handed space to right-handed (as the viewer does).
_FLIP_X = np.diag([-1.0, 1.0, 1.0, 1.0])


# --------------------------------------------------------------------------- #
# Binary FBX node encoding
# --------------------------------------------------------------------------- #


class _Node:
    """A node of the FBX document tree."""

    __slots__ = ("name", "props", "children")

    def __init__(self, name: str, *props: tuple[str, Any]):
        self.name = name
        self.props = list(props)
        self.children: list["_Node"] = []

    def add(self, name: str, *props: tuple[str, Any]) -> "_Node":
        """Append and return a child node."""
        child = _Node(name, *props)
        self.children.append(child)
        return child


# Property helpers -> (type code, value)
def _i32(v: int):
    return ("I", int(v))


def _i64(v: int):
    return ("L", int(v))


def _f64(v: float):
    return ("D", float(v))


def _bool(v: bool):
    return ("C", bool(v))


def _str(v: str | bytes):
    return ("S", v)


def _raw(v: bytes):
    return ("R", v)


def _f64_arr(v):
    return ("d", np.asarray(v, dtype="<f8").ravel())


def _i32_arr(v):
    return ("i", np.asarray(v, dtype="<i4").ravel())


def _encode_prop(code: str, value: Any) -> bytes:
    if code == "C":
        return b"C" + struct.pack("<?", value)
    if code == "I":
        return b"I" + struct.pack("<i", value)
    if code == "L":
        return b"L" + struct.pack("<q", value)
    if code == "D":
        return b"D" + struct.pack("<d", value)
    if code in ("S", "R"):
        data = value.encode("utf-8") if isinstance(value, str) else bytes(value)
        return code.encode() + struct.pack("<I", len(data)) + data
    if code in ("d", "i"):
        raw = value.tobytes()
        if len(raw) > 128:
            packed = zlib.compress(raw, 6)
            return code.encode() + struct.pack("<III", len(value), 1, len(packed)) + packed
        return code.encode() + struct.pack("<III", len(value), 0, len(raw)) + raw
    raise ValueError(f"Unsupported FBX property type {code!r}")


_NULL_RECORD = b"\x00" * 13  # 3 x uint32 + uint8 for FBX < 7.5


def _encode_node(node: _Node, offset: int) -> bytes:
    """Encode a node (and its children) located at absolute file `offset`."""
    name = node.name.encode("ascii")
    props = b"".join(_encode_prop(c, v) for c, v in node.props)
    header_len = 13 + len(name)

    body = bytearray(props)
    child_offset = offset + header_len + len(props)
    for child in node.children:
        encoded = _encode_node(child, child_offset)
        body += encoded
        child_offset += len(encoded)
    if node.children or not node.props:
        body += _NULL_RECORD

    end_offset = offset + header_len + len(body)
    return (
        struct.pack("<III", end_offset, len(node.props), len(props))
        + struct.pack("<B", len(name))
        + name
        + bytes(body)
    )


def _encode_document(nodes: list[_Node]) -> bytes:
    out = bytearray(b"Kaydara FBX Binary  \x00\x1a\x00")
    out += struct.pack("<I", _FBX_VERSION)
    for node in nodes:
        out += _encode_node(node, len(out))
    out += _NULL_RECORD

    # Footer
    out += _FOOT_ID + b"\x00" * 4
    pad = ((len(out) + 15) & ~15) - len(out)
    out += b"\x00" * (pad if pad else 16)
    out += struct.pack("<I", _FBX_VERSION)
    out += b"\x00" * 120
    out += _FOOTER_MAGIC
    return bytes(out)


# --------------------------------------------------------------------------- #
# Scene helpers
# --------------------------------------------------------------------------- #


def _obj_name(name: str, cls: str) -> bytes:
    """Binary FBX object names are 'Name\\x00\\x01Class'."""
    return name.encode("utf-8", "replace") + b"\x00\x01" + cls.encode("ascii")


def _p(parent: _Node, name: str, ptype: str, label: str, flags: str, *values):
    """Add a Properties70 'P' entry."""
    parent.add("P", _str(name), _str(ptype), _str(label), _str(flags), *values)


def _fbx_matrix(m: np.ndarray) -> tuple[str, Any]:
    """FBX stores 4x4 matrices column-major (translation at elements 12..14)."""
    return _f64_arr(np.asarray(m, dtype=np.float64).T)


def _decompose(local: np.ndarray):
    """Split a column-vector 4x4 matrix into translation, XYZ euler (deg), scale."""
    t = local[:3, 3]
    basis = local[:3, :3]
    scale = np.linalg.norm(basis, axis=0)
    scale[scale == 0] = 1.0
    if np.linalg.det(basis) < 0:
        scale[0] = -scale[0]
    r = basis / scale

    # FBX eEulerXYZ: R = Rz * Ry * Rx
    sy = -r[2, 0]
    sy = max(-1.0, min(1.0, sy))
    ry = math.asin(sy)
    if abs(sy) < 0.999999:
        rx = math.atan2(r[2, 1], r[2, 2])
        rz = math.atan2(r[1, 0], r[0, 0])
    else:  # gimbal lock
        rx = math.atan2(-r[1, 2], r[1, 1])
        rz = 0.0
    return t, np.degrees([rx, ry, rz]), scale


def _unique_names(names: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    result = []
    for name in names:
        name = name or "bone"
        if name in seen:
            seen[name] += 1
            name = f"{name}.{seen[name]:03d}"
        else:
            seen[name] = 0
        result.append(name)
    return result


def _bone_globals(mesh: MeshData) -> list[np.ndarray]:
    """Global bind matrices of each bone in right-handed, column-vector form."""
    result = []
    for m in mesh.bones.matrix:
        neox = np.asarray(m, dtype=np.float64).reshape(4, 4)
        # NeoX matrices are row-vector (translation in last row): transpose.
        result.append(_FLIP_X @ neox.T @ _FLIP_X)
    return result


# --------------------------------------------------------------------------- #
# Conversion
# --------------------------------------------------------------------------- #


def convert(mesh_data: MeshData, flip_uv: bool = True) -> bytes:
    """
    Converts MeshData to FBX 7.4 Binary.

    Parameters:
    - mesh_data: MeshData object containing bones, vertices, faces, etc.
    - flip_uv: Flip V (NeoX uses a top-left UV origin, FBX bottom-left).

    Returns:
    - bytes: FBX file content as bytes
    """
    mesh = mesh_data.mesh
    vertex_count = len(mesh.position)
    if vertex_count == 0:
        raise ValueError("Mesh has no vertices to export")

    # ---- geometry -------------------------------------------------------- #
    positions = np.asarray(mesh.position, dtype=np.float64).reshape(-1, 3).copy()
    positions[:, 0] = -positions[:, 0]

    if len(mesh.normal) == vertex_count:
        normals = np.asarray(mesh.normal, dtype=np.float64).reshape(-1, 3).copy()
        normals[:, 0] = -normals[:, 0]
    else:
        normals = None

    if len(mesh.uv) >= vertex_count:
        uvs = np.asarray(mesh.uv[:vertex_count], dtype=np.float64).reshape(-1, 2).copy()
        if flip_uv:
            uvs[:, 1] = 1.0 - uvs[:, 1]
    else:
        uvs = None

    faces = np.asarray(mesh.face, dtype=np.int64).reshape(-1, 3)
    valid = ((faces >= 0) & (faces < vertex_count)).all(axis=1)
    faces = faces[valid][:, [0, 2, 1]]  # reverse winding after the X mirror
    if len(faces) == 0:
        raise ValueError("Mesh has no valid faces to export")

    polygon_vertex_index = faces.astype(np.int32).copy()
    polygon_vertex_index[:, 2] = ~polygon_vertex_index[:, 2]  # end-of-polygon marker

    # ---- object ids -------------------------------------------------------- #
    next_id = [1000000]

    def new_id() -> int:
        next_id[0] += 1
        return next_id[0]

    model_id, geometry_id, material_id = new_id(), new_id(), new_id()
    connections: list[tuple[int, int]] = []  # (child, parent)

    objects = _Node("Objects")

    geometry = objects.add(
        "Geometry", _i64(geometry_id), _str(_obj_name("NeoXMesh", "Geometry")), _str("Mesh")
    )
    geometry.add("Properties70")
    geometry.add("GeometryVersion", _i32(124))
    geometry.add("Vertices", _f64_arr(positions))
    geometry.add("PolygonVertexIndex", _i32_arr(polygon_vertex_index))

    layer = _Node("Layer", _i32(0))
    layer.add("Version", _i32(100))

    if normals is not None:
        le = geometry.add("LayerElementNormal", _i32(0))
        le.add("Version", _i32(101))
        le.add("Name", _str(""))
        le.add("MappingInformationType", _str("ByVertice"))
        le.add("ReferenceInformationType", _str("Direct"))
        le.add("Normals", _f64_arr(normals))
        ref = layer.add("LayerElement")
        ref.add("Type", _str("LayerElementNormal"))
        ref.add("TypedIndex", _i32(0))

    if uvs is not None:
        le = geometry.add("LayerElementUV", _i32(0))
        le.add("Version", _i32(101))
        le.add("Name", _str("UVMap"))
        le.add("MappingInformationType", _str("ByPolygonVertex"))
        le.add("ReferenceInformationType", _str("IndexToDirect"))
        le.add("UV", _f64_arr(uvs))
        le.add("UVIndex", _i32_arr(faces))
        ref = layer.add("LayerElement")
        ref.add("Type", _str("LayerElementUV"))
        ref.add("TypedIndex", _i32(0))

    le = geometry.add("LayerElementMaterial", _i32(0))
    le.add("Version", _i32(101))
    le.add("Name", _str(""))
    le.add("MappingInformationType", _str("AllSame"))
    le.add("ReferenceInformationType", _str("IndexToDirect"))
    le.add("Materials", _i32_arr([0]))
    ref = layer.add("LayerElement")
    ref.add("Type", _str("LayerElementMaterial"))
    ref.add("TypedIndex", _i32(0))

    geometry.children.append(layer)

    model = objects.add(
        "Model", _i64(model_id), _str(_obj_name("NeoXMesh", "Model")), _str("Mesh")
    )
    model.add("Version", _i32(232))
    props = model.add("Properties70")
    _p(props, "DefaultAttributeIndex", "int", "Integer", "", _i32(0))
    model.add("Shading", _bool(True))
    model.add("Culling", _str("CullingOff"))

    material = objects.add(
        "Material", _i64(material_id), _str(_obj_name("Material", "Material")), _str("")
    )
    material.add("Version", _i32(102))
    material.add("ShadingModel", _str("phong"))
    material.add("MultiLayer", _i32(0))
    props = material.add("Properties70")
    _p(props, "DiffuseColor", "Color", "", "A", _f64(0.8), _f64(0.8), _f64(0.8))

    connections += [(model_id, 0), (geometry_id, model_id), (material_id, model_id)]
    counts = {"Model": 1, "Geometry": 1, "Material": 1}

    # ---- skeleton & skin ------------------------------------------------- #
    bones = mesh_data.bones
    bone_count = len(bones.names)
    if mesh_data.has_bones and bone_count and len(bones.matrix) >= bone_count:
        names = _unique_names(list(bones.names))
        globals_ = _bone_globals(mesh_data)
        parents = [
            p if (p is not None and 0 <= p < bone_count and p != i) else -1
            for i, p in enumerate(list(bones.parents)[:bone_count])
        ]
        parents += [-1] * (bone_count - len(parents))
        # Break parent cycles so every bone hangs off a root.
        for i in range(bone_count):
            seen, j = set(), i
            while parents[j] != -1:
                if j in seen:
                    parents[i] = -1
                    break
                seen.add(j)
                j = parents[j]

        bone_ids = [new_id() for _ in range(bone_count)]
        for i in range(bone_count):
            attr_id = new_id()
            attr = objects.add(
                "NodeAttribute",
                _i64(attr_id),
                _str(_obj_name(names[i], "NodeAttribute")),
                _str("LimbNode"),
            )
            props = attr.add("Properties70")
            _p(props, "Size", "double", "Number", "", _f64(1.0))
            attr.add("TypeFlags", _str("Skeleton"))

            parent_global = globals_[parents[i]] if parents[i] != -1 else np.eye(4)
            local = np.linalg.inv(parent_global) @ globals_[i]
            t, r, s = _decompose(local)

            bone = objects.add(
                "Model", _i64(bone_ids[i]), _str(_obj_name(names[i], "Model")), _str("LimbNode")
            )
            bone.add("Version", _i32(232))
            props = bone.add("Properties70")
            _p(props, "Lcl Translation", "Lcl Translation", "", "A", *map(_f64, t))
            _p(props, "Lcl Rotation", "Lcl Rotation", "", "A", *map(_f64, r))
            _p(props, "Lcl Scaling", "Lcl Scaling", "", "A", *map(_f64, s))
            bone.add("Shading", _bool(True))
            bone.add("Culling", _str("CullingOff"))

            connections.append((attr_id, bone_ids[i]))
            connections.append(
                (bone_ids[i], bone_ids[parents[i]] if parents[i] != -1 else 0)
            )

        # Gather per-bone vertex weights
        influences: list[dict[int, float]] = [{} for _ in range(bone_count)]
        for v, (joint_row, weight_row) in enumerate(
            zip(bones.joints[:vertex_count], bones.weights[:vertex_count])
        ):
            for joint, weight in zip(list(joint_row)[:4], list(weight_row)[:4]):
                joint, weight = int(joint), float(weight)
                if joint in _UNUSED_JOINTS or not 0 <= joint < bone_count or weight <= 0:
                    continue
                influences[joint][v] = influences[joint].get(v, 0.0) + weight

        skin_id = new_id()
        skin = objects.add(
            "Deformer", _i64(skin_id), _str(_obj_name("Skin", "Deformer")), _str("Skin")
        )
        skin.add("Version", _i32(101))
        skin.add("Link_DeformAcuracy", _f64(50.0))
        connections.append((skin_id, geometry_id))

        mesh_global = np.eye(4)
        cluster_count = 0
        for i in range(bone_count):
            cluster_id = new_id()
            cluster = objects.add(
                "Deformer",
                _i64(cluster_id),
                _str(_obj_name(names[i], "SubDeformer")),
                _str("Cluster"),
            )
            cluster.add("Version", _i32(100))
            cluster.add("UserData", _str(""), _str(""))
            if influences[i]:
                indices = sorted(influences[i])
                cluster.add("Indexes", _i32_arr(indices))
                cluster.add("Weights", _f64_arr([influences[i][k] for k in indices]))
            cluster.add(
                "Transform", _fbx_matrix(np.linalg.inv(globals_[i]) @ mesh_global)
            )
            cluster.add("TransformLink", _fbx_matrix(globals_[i]))
            connections.append((cluster_id, skin_id))
            connections.append((bone_ids[i], cluster_id))
            cluster_count += 1

        pose_id = new_id()
        pose = objects.add(
            "Pose", _i64(pose_id), _str(_obj_name("BindPose", "Pose")), _str("BindPose")
        )
        pose.add("Type", _str("BindPose"))
        pose.add("Version", _i32(100))
        pose.add("NbPoseNodes", _i32(bone_count + 1))
        node = pose.add("PoseNode")
        node.add("Node", _i64(model_id))
        node.add("Matrix", _fbx_matrix(mesh_global))
        for i in range(bone_count):
            node = pose.add("PoseNode")
            node.add("Node", _i64(bone_ids[i]))
            node.add("Matrix", _fbx_matrix(globals_[i]))

        counts["Model"] += bone_count
        counts["NodeAttribute"] = bone_count
        counts["Deformer"] = 1 + cluster_count
        counts["Pose"] = 1

    # ---- document ------------------------------------------------------- #
    header = _Node("FBXHeaderExtension")
    header.add("FBXHeaderVersion", _i32(1003))
    header.add("FBXVersion", _i32(_FBX_VERSION))
    header.add("EncryptionType", _i32(0))
    stamp = header.add("CreationTimeStamp")
    stamp.add("Version", _i32(1000))
    for key, value in (
        ("Year", 1970),
        ("Month", 1),
        ("Day", 1),
        ("Hour", 10),
        ("Minute", 0),
        ("Second", 0),
        ("Millisecond", 0),
    ):
        stamp.add(key, _i32(value))
    header.add("Creator", _str("NeoXtractor"))

    global_settings = _Node("GlobalSettings")
    global_settings.add("Version", _i32(1000))
    props = global_settings.add("Properties70")
    for name, value in (
        ("UpAxis", 1),
        ("UpAxisSign", 1),
        ("FrontAxis", 2),
        ("FrontAxisSign", 1),
        ("CoordAxis", 0),
        ("CoordAxisSign", 1),
        ("OriginalUpAxis", 1),
        ("OriginalUpAxisSign", 1),
    ):
        _p(props, name, "int", "Integer", "", _i32(value))
    # 1 unit = 1 meter (FBX's scale factor is in centimeters), matching glTF.
    _p(props, "UnitScaleFactor", "double", "Number", "", _f64(100.0))
    _p(props, "OriginalUnitScaleFactor", "double", "Number", "", _f64(100.0))

    documents = _Node("Documents")
    documents.add("Count", _i32(1))
    document = documents.add(
        "Document", _i64(new_id()), _str(_obj_name("", "Scene")), _str("Scene")
    )
    props = document.add("Properties70")
    _p(props, "SourceObject", "object", "", "")
    _p(props, "ActiveAnimStackName", "KString", "", "", _str(""))
    document.add("RootNode", _i64(0))

    definitions = _Node("Definitions")
    definitions.add("Version", _i32(100))
    definitions.add("Count", _i32(1 + sum(counts.values())))
    definitions.add("ObjectType", _str("GlobalSettings")).add("Count", _i32(1))
    for type_name, count in counts.items():
        definitions.add("ObjectType", _str(type_name)).add("Count", _i32(count))

    connection_node = _Node("Connections")
    for child, parent in connections:
        connection_node.add("C", _str("OO"), _i64(child), _i64(parent))

    return _encode_document(
        [
            header,
            _Node("FileId", _raw(_FILE_ID)),
            _Node("CreationTime", _str(_TIME_ID)),
            _Node("Creator", _str("NeoXtractor")),
            global_settings,
            documents,
            _Node("References"),
            definitions,
            objects,
            connection_node,
            _Node("Takes"),
        ]
    )
