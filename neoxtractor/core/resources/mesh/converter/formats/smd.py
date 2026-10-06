"""Source Model Data (SMD) Format Converter"""

from neoxtractor.core.resources.mesh.loader import MeshData

NAME = "Source Model Data (SMD) Format"
EXTENSION = ".smd"


def convert(mesh: MeshData, flip_uv=False) -> bytes:
    """
    Convert mesh to Valve's SMD format as a reference (static) SMD.

    Parameters:
    - mesh: MeshData object containing bones, vertices, faces, etc.
    - flip_uv: Boolean to indicate whether to flip the UV coordinates on the Y-axis.

    Returns:
    - bytes: SMD file content as bytes
    """
    smd_lines = []
    smd_lines.append("version 1\n")

    # Bone Structure - Write nodes as in a static SMD
    if mesh.has_bones:
        smd_lines.append("nodes\n")
        parent_child_dict = {}
        for i, parent in enumerate(mesh.bones.parents):
            if parent not in parent_child_dict:
                parent_child_dict[parent] = []
            parent_child_dict[parent].append(i)

        # Recursive function to build bone hierarchy
        def write_bone_hierarchy(index, parent_index, lines):
            bone_name = mesh.bones.names[index]
            lines.append(f'{index} "{bone_name}" {parent_index}\n')
            if index in parent_child_dict:
                for child in parent_child_dict[index]:
                    write_bone_hierarchy(child, index, lines)

        # Start with root bones
        root_index = mesh.bones.parents.index(-1)
        write_bone_hierarchy(root_index, -1, smd_lines)
        smd_lines.append("end\n")

        # Skeleton - Static, only the initial frame at time 0
        smd_lines.append("skeleton\n")
        smd_lines.append("time 0\n")
        for i, matrix in enumerate(mesh.bones.matrix):
            # Extract translation from transformation matrix
            x, y, z = matrix[0, 3], matrix[1, 3], matrix[2, 3]
            # Extract rotation angles (simplified - assumes no complex rotations)
            rx, ry, rz = 0.0, 0.0, 0.0
            smd_lines.append(
                f"{i} {x:.6f} {y:.6f} {z:.6f} {rx:.6f} {ry:.6f} {rz:.6f}\n"
            )
        smd_lines.append("end\n")
    else:
        # No bones - create a single root bone
        smd_lines.append("nodes\n")
        smd_lines.append('0 "root" -1\n')
        smd_lines.append("end\n")

        smd_lines.append("skeleton\n")
        smd_lines.append("time 0\n")
        smd_lines.append("0 0.000000 0.000000 0.000000 0.000000 0.000000 0.000000\n")
        smd_lines.append("end\n")

    # Mesh Data - Vertices, Normals, UVs, and Faces
    smd_lines.append("triangles\n")
    for face_idx, (v1, v2, v3) in enumerate(mesh.mesh.face):
        material_name = f"material_{face_idx // 100}"  # Group faces by material
        smd_lines.append(f"{material_name}\n")

        for vertex_index in [v1, v2, v3]:
            pos = mesh.mesh.position[vertex_index]
            norm = mesh.mesh.normal[vertex_index]
            uv = mesh.mesh.uv[vertex_index]

            # Flip UV if specified
            if flip_uv:
                uv = (uv[0], 1 - uv[1])

            if mesh.has_bones and vertex_index < len(mesh.bones.joints):
                # Use bone weights
                joint_indices = mesh.bones.joints[vertex_index]
                weights = mesh.bones.weights[vertex_index]

                # Find the bone with highest weight
                max_weight_idx = weights.index(max(weights))
                bone_id = joint_indices[max_weight_idx]
            else:
                # No bones - assign to root bone
                bone_id = 0

            # Format: <bone ID> <x> <y> <z> <nx> <ny> <nz> <u> <v> [links]
            smd_lines.append(
                f"{bone_id} {pos[0]:.6f} {pos[1]:.6f} {pos[2]:.6f} "
                f"{norm[0]:.6f} {norm[1]:.6f} {norm[2]:.6f} "
                f"{uv[0]:.6f} {uv[1]:.6f}\n"
            )

    smd_lines.append("end\n")

    return "".join(smd_lines).encode("utf-8")
