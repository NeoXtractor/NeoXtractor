"""Mesh conversion utilities."""

from neoxtractor.core.resources.mesh.loader import MeshData

from .formats import ascii as mesh_ascii
from .formats import fbx, gltf, iqe, obj, pmx, smd

FORMATS = [mesh_ascii, gltf, iqe, obj, pmx, smd, fbx]


def convert_mesh(mesh: MeshData, target_format: type, **kwargs) -> bytes:
    """
    Convert mesh to the specified format.

    Parameters:
    - mesh: MeshData object containing bones, vertices, faces, etc.
    - target_format: The format class to convert the mesh to.
    - kwargs: Additional parameters for specific format conversions.

    Returns:
    - bytes: Converted mesh data in the specified format.
    """
    return target_format.convert(mesh, **kwargs)


__all__ = [
    "convert_mesh",
    "FORMATS",
    "mesh_ascii",
    "gltf",
    "iqe",
    "obj",
    "pmx",
    "smd",
    "fbx",
]
