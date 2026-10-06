"""Inter-Quake Export (IQE) Format Converter"""

import numpy as np

from neoxtractor.core.mesh_loader import MeshData

NAME = "Inter-Quake Export (IQE) Format"
EXTENSION = ".iqe"


def convert(mesh: MeshData) -> bytes:
    """
    Convert mesh to IQE format.

    Parameters:
    - mesh: MeshData object containing bones, vertices, faces, etc.

    Returns:
    - bytes: IQE file content as bytes
    """
    iqe_lines = []

    # Helper functions to replace transformations library
    def translation_from_matrix(matrix):
        """Extract translation from 4x4 transformation matrix"""
        return matrix[:3, 3]

    def quaternion_from_matrix(matrix):
        """Return quaternion from rotation matrix."""
        matrix = np.asarray(matrix, dtype=np.float64)[:4, :4]
        m00 = matrix[0, 0]
        m01 = matrix[0, 1]
        m02 = matrix[0, 2]
        m10 = matrix[1, 0]
        m11 = matrix[1, 1]
        m12 = matrix[1, 2]
        m20 = matrix[2, 0]
        m21 = matrix[2, 1]
        m22 = matrix[2, 2]
        smatrix = np.array(
            [
                [m00 - m11 - m22, 0.0, 0.0, 0.0],
                [m01 + m10, m11 - m00 - m22, 0.0, 0.0],
                [m02 + m20, m12 + m21, m22 - m00 - m11, 0.0],
                [m21 - m12, m02 - m20, m10 - m01, m00 + m11 + m22],
            ]
        )
        smatrix /= 3.0
        w, vec = np.linalg.eigh(smatrix)
        q = vec[[3, 0, 1, 2], np.argmax(w)]
        if q[0] < 0.0:
            np.negative(q, q)
        return q

    # Calculate bone transforms if bones exist
    bone_translate = []
    bone_rotation = []
    if mesh.has_bones:
        bone_count = len(mesh.bones.parents)
        for i in range(bone_count):
            matrix = np.eye(4)
            parent_node = mesh.bones.parents[i]
            if parent_node >= 0:
                matrix = mesh.bones.matrix[parent_node]
            relative_matrix = np.dot(mesh.bones.matrix[i], np.linalg.inv(matrix))
            bone_translate.append(translation_from_matrix(relative_matrix.T))
            bone_rotation.append(quaternion_from_matrix(relative_matrix.T))

    iqe_lines.append("# Inter-Quake Export\n")
    iqe_lines.append("\n")

    # Write bone hierarchy if bones exist
    if mesh.has_bones:
        parent_child_dict = {}
        old2new = {}
        index_pool = [-1]

        for i, p in enumerate(mesh.bones.parents):
            if p not in parent_child_dict:
                parent_child_dict[p] = []
            parent_child_dict[p].append(i)

        def print_joint(index, parent_index):
            iqe_lines.append(f'joint "{mesh.bones.names[index]}" {parent_index}\n')
            x, y, z = bone_translate[index]
            w, i_quat, j, k = bone_rotation[index]
            iqe_lines.append(f"pq {-x} {y} {z} {i_quat} {-j} {-k} {w}\n")

        def deep_first_search(index, index_pool, parent_index):
            index_pool[0] += 1
            current_node_index = index_pool[0]
            old2new[index] = current_node_index
            print_joint(index, parent_index)
            if index in parent_child_dict:
                for child in parent_child_dict[index]:
                    deep_first_search(child, index_pool, current_node_index)

        try:
            root_index = mesh.bones.parents.index(-1)
            deep_first_search(root_index, index_pool, -1)
        except ValueError:
            # No root bone found, create default structure
            for i in range(len(mesh.bones.names)):
                old2new[i] = i
                print_joint(
                    i,
                    mesh.bones.parents[i] if mesh.bones.parents[i] != -1 else -1,
                )

        iqe_lines.append("\n")
    else:
        old2new = {}

    # Single mesh without sub-mesh data
    iqe_lines.append("mesh mesh0\n")
    iqe_lines.append('material "mesh0Mat"\n')
    iqe_lines.append("\n")
    # Write vertex positions
    for x, y, z in mesh.mesh.position:
        iqe_lines.append(f"vp {-x} {y} {z}\n")
    iqe_lines.append("\n")

    for x, y, z in mesh.mesh.normal:
        iqe_lines.append(f"vn {-x} {y} {z}\n")
    iqe_lines.append("\n")

    # Write UV coordinates
    if mesh.has_uvs:
        for u, v in mesh.mesh.uv:
            iqe_lines.append(f"vt {u} {1 - v}\n")
        iqe_lines.append("\n")

    # Write vertex bone weights
    if mesh.has_bones:
        for i, bone_indices in enumerate(mesh.bones.joints):
            iqe_lines.append("vb")
            for j in range(min(4, len(bone_indices))):
                v = bone_indices[j]
                if v in (255, 65535):  # Invalid bone index
                    break
                v = old2new.get(v, v)  # Map old bone index to new index
                w = (
                    mesh.bones.weights[i][j]
                    if i < len(mesh.bones.weights) and j < len(mesh.bones.weights[i])
                    else 0.0
                )
                iqe_lines.append(f" {v} {w}")
            iqe_lines.append("\n")
        iqe_lines.append("\n")

    # Write faces
    for v1, v2, v3 in mesh.mesh.face:
        iqe_lines.append(f"fm {v3} {v1} {v2}\n")

    return "".join(iqe_lines).encode("utf-8")
