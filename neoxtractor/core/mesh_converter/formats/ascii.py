"""ASCII Mesh Format Converter"""

from neoxtractor.core.mesh_loader import MeshData

NAME = "Text Mesh (ASCII) Format"
EXTENSION = ".ascii"


def convert(mesh: MeshData, flip_uv=False) -> bytes:
    """
    Convert mesh to ASCII format.

    Parameters:
    - mesh: MeshData object containing bones, vertices, faces, etc.
    - flip_uv: Boolean to indicate whether to flip the UV coordinates on the Y-axis.

    Returns:
    - bytes: ASCII file content as bytes
    """
    ascii_lines = []

    # Write Bone Count
    if mesh.has_bones:
        ascii_lines.append(f"{len(mesh.bones.names)}\n")

        # Write Bone Information
        for i, (name, parent) in enumerate(zip(mesh.bones.names, mesh.bones.parents)):
            ascii_lines.append(f"{name}\n")
            ascii_lines.append(f"{parent}\n")
            if mesh.bones.matrix and i < len(mesh.bones.matrix):
                matrix = mesh.bones.matrix[i]
                position = " ".join(f"{val:.6f}" for val in matrix.flatten()[:3])
                ascii_lines.append(f"{position} 0 0 0 1\n")
            else:
                ascii_lines.append("0.000000 0.000000 0.000000 0 0 0 1\n")
    else:
        ascii_lines.append("0\n")

    # Write Vertex Positions
    ascii_lines.append(f"{len(mesh.mesh.position)}\n")
    for x, y, z in mesh.mesh.position:
        ascii_lines.append(f"{x:.6f} {y:.6f} {z:.6f}\n")

    ascii_lines.append(f"{len(mesh.mesh.normal)}\n")
    for nx, ny, nz in mesh.mesh.normal:
        ascii_lines.append(f"{nx:.6f} {ny:.6f} {nz:.6f}\n")

    # Write UVs, applying flip if needed
    if mesh.has_uvs:
        ascii_lines.append(f"{len(mesh.mesh.uv)}\n")
        for u, v in mesh.mesh.uv:
            if flip_uv:
                v = 1 - v
            ascii_lines.append(f"{u:.6f} {v:.6f}\n")
    else:
        ascii_lines.append("0\n")

    # Write Face Indices
    ascii_lines.append(f"{len(mesh.mesh.face)}\n")
    for v1, v2, v3 in mesh.mesh.face:
        ascii_lines.append(f"{v1} {v2} {v3}\n")

    return "".join(ascii_lines).encode("utf-8")
