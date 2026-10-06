"""Object File Format (OBJ) Converter"""

from neoxtractor.core.resources.mesh.loader import MeshData

NAME = "Wavefront (OBJ) Format - No Skeleton"
EXTENSION = ".obj"


def convert(mesh: MeshData, flip_uv=False) -> bytes:
    """
    Convert mesh to OBJ format as a static mesh without skeleton.

    Parameters:
    - mesh: MeshData object to be converted to OBJ format.
    - flip_uv: Boolean to indicate whether to flip the UV coordinates on the Y-axis.

    Returns:
    - bytes: OBJ file content as bytes
    """
    obj_lines = []
    obj_lines.append("o Neox Mesh\n")

    # Write vertices
    for v in mesh.mesh.position:
        obj_lines.append(f"v {v[0]} {v[1]} {v[2]}\n")

    # Write normals
    for n in mesh.mesh.normal:
        obj_lines.append(f"vn {n[0]} {n[1]} {n[2]}\n")

    for uv in mesh.mesh.uv:
        if flip_uv:
            uv = (uv[0], 1 - uv[1])  # Flip UV on the Y axis
        obj_lines.append(f"vt {uv[0]} {uv[1]}\n")

    # Write all faces
    for v1, v2, v3 in mesh.mesh.face:
        if mesh.has_uvs:
            obj_lines.append(
                f"f {v1 + 1}/{v1 + 1} {v2 + 1}/{v2 + 1} {v3 + 1}/{v3 + 1}\n"
            )
        else:
            obj_lines.append(f"f {v1 + 1} {v2 + 1} {v3 + 1}\n")

    return "".join(obj_lines).encode("utf-8")
