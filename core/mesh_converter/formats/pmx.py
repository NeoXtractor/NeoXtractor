"""PMX Format Converter

Ported from the original NeoX Model Converter ``savepmx`` implementation
(NeoXtractor v1.2.2, commit 7e4804c), adapted to the ``MeshData`` structure.
"""

import io

import numpy as np
import pymeshio.pmx.writer
from pymeshio import common, pmx

from core.mesh_loader import MeshData

NAME = "Polygon Model eXtended (PMX) Format"
EXTENSION = ".pmx"

# Joint index values NeoX uses to mark an unused bone slot.
_UNUSED_JOINTS = (255, 65535)


def _bone_translation(matrix) -> tuple[float, float, float]:
    """Return the translation part of a NeoX bone matrix.

    NeoX stores bone matrices row-major with the translation in the last row,
    so the translation is the last column of the transposed matrix (this is
    what ``transformations.translation_from_matrix(matrix.T)`` returned in the
    original converter, and what the mesh viewer uses to draw bones).
    """
    m = np.asarray(matrix, dtype=np.float64).reshape(4, 4).T
    return float(m[0, 3]), float(m[1, 3]), float(m[2, 3])


def _new_bone(name: str, position: common.Vector3, parent_index: int) -> pmx.Bone:
    bone = pmx.Bone(
        name=name,
        english_name=name,
        position=position,
        parent_index=parent_index,
        layer=0,
        flag=0,
    )
    bone.setFlag(pmx.BONEFLAG_CAN_ROTATE, True)
    bone.setFlag(pmx.BONEFLAG_IS_VISIBLE, True)
    bone.setFlag(pmx.BONEFLAG_CAN_MANIPULATE, True)
    return bone


def _build_bones(mesh: MeshData) -> tuple[list[pmx.Bone], dict[int, int]]:
    """Build the PMX bone list (parents always before children) and an
    old-index -> new-index map."""
    bones = mesh.bones
    bone_count = len(bones.names)
    parents = list(bones.parents)[:bone_count]

    children: dict[int, list[int]] = {}
    roots: list[int] = []
    for i, p in enumerate(parents):
        if p == -1 or p is None or not 0 <= p < bone_count or p == i:
            roots.append(i)
        else:
            children.setdefault(p, []).append(i)

    bone_pool: list[pmx.Bone] = []
    old2new: dict[int, int] = {}

    def visit(root: int):
        # Iterative depth-first search (avoids recursion limits on long chains)
        stack = [(root, -1)]
        while stack:
            index, parent_new = stack.pop()
            if index in old2new:
                continue
            new_index = len(bone_pool)
            old2new[index] = new_index
            x, y, z = _bone_translation(bones.matrix[index])
            bone_pool.append(
                _new_bone(bones.names[index], common.Vector3(-x, y, -z), parent_new)
            )
            for child in reversed(children.get(index, [])):
                stack.append((child, new_index))

    for root in roots:
        visit(root)
    # Bones caught in a parent cycle are never reached from a root.
    for i in range(bone_count):
        visit(i)

    return bone_pool, old2new


def convert(mesh: MeshData) -> bytes:
    """
    Convert mesh to PMX format.

    Parameters:
    - mesh: MeshData object containing bones, vertices, faces, etc.

    Returns:
    - bytes: PMX file content as bytes
    """
    pmx_model = pmx.Model()
    pmx_model.display_slots.append(pmx.DisplaySlot("表情", "Exp", 1, None))
    pmx_model.name = "NeoXtractor"
    pmx_model.english_name = "Empty model"
    pmx_model.comment = "NeoX Model Converterで生成"
    pmx_model.english_comment = "Created by NeoXtractor."

    has_bones = mesh.has_bones and len(mesh.bones.names) > 0
    if has_bones:
        pmx_model.bones, old2new = _build_bones(mesh)
    else:
        pmx_model.bones = [_new_bone("root", common.Vector3(0, 0, 0), -1)]
        old2new = {}

    positions = mesh.mesh.position
    normals = mesh.mesh.normal
    uvs = mesh.mesh.uv
    joints = mesh.bones.joints if has_bones else []
    weights = mesh.bones.weights if has_bones else []

    for i, (x, y, z) in enumerate(positions):
        nx, ny, nz = normals[i] if i < len(normals) else (0.0, 0.0, 1.0)
        u, v = uvs[i] if i < len(uvs) else (0.0, 0.0)

        if i < len(joints):
            joint_index = [
                old2new.get(int(j), 0) if int(j) not in _UNUSED_JOINTS else 0
                for j in list(joints[i])[:4]
            ]
            joint_weight = (
                [float(w) for w in list(weights[i])[:4]]
                if i < len(weights)
                else [1.0, 0.0, 0.0, 0.0]
            )
            joint_index += [0] * (4 - len(joint_index))
            joint_weight += [0.0] * (4 - len(joint_weight))
            deform = pmx.Bdef4(*joint_index, *joint_weight)
        else:
            deform = pmx.Bdef1(0)

        pmx_model.vertices.append(
            pmx.Vertex(
                common.Vector3(-float(x), float(y), -float(z)),
                common.Vector3(-float(nx), float(ny), -float(nz)),
                common.Vector2(float(u), float(v)),
                deform,
                0.0,
            )
        )

    vertex_total = len(pmx_model.vertices)
    face_total = 0
    for face in mesh.mesh.face:
        face = [int(f) for f in face]
        # Skip faces that would point outside the vertex list and corrupt the file
        if any(not 0 <= f < vertex_total for f in face):
            continue
        pmx_model.indices.extend(face)
        face_total += 1

    pmx_model.materials.append(
        pmx.Material(
            name="Mat0",
            english_name="material0",
            diffuse_color=common.RGB(1, 1, 1),
            alpha=1.0,
            specular_factor=1,
            specular_color=common.RGB(1, 1, 1),
            ambient_color=common.RGB(0, 0, 0),
            flag=0,
            edge_color=common.RGBA(0, 0, 0, 1),
            edge_size=0,
            texture_index=-1,
            sphere_texture_index=-1,
            sphere_mode=pmx.MATERIALSPHERE_NONE,
            toon_sharing_flag=1,
            toon_texture_index=0,
            comment="comment",
            vertex_count=face_total * 3,
        )
    )

    # Write to bytes buffer (text encoding 0 = UTF-16LE, as the working
    # converter did via pymeshio.pmx.writer.write_to_file)
    buffer = io.BytesIO()
    pymeshio.pmx.writer.write(buffer, pmx_model)
    return buffer.getvalue()
