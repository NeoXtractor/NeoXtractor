"""FBX Mesh Format Converter"""

from io import BytesIO

from neoxtractor.core.mesh_loader import MeshData

from .datapackers import write_half_float

NAME = "Kaydara (FBX) Format"
EXTENSION = ".fbx"


def convert(mesh_data: MeshData):
    """
    Converts MeshData to FBX 7.4 Binary.

    Parameters:
    - mesh: MeshData object containing bones, vertices, faces, etc.

    Returns:
    - bytes: FBX file content as bytes
    """
    data = BytesIO()
    data.write(b"Kaydara FBX Binary  \x00\x1a\x00\x85\x1c")

    return data.getvalue()
