"""Mesh conversion utilities.

The facade path is ``resources.mesh.convert`` (routed through
``neoxtractor.core.processors``); the format modules below carry the
per-target encoders.  This package keeps no dispatcher of its own.
"""

from .formats import ascii as mesh_ascii
from .formats import fbx, gltf, iqe, obj, pmx, smd

FORMATS = [mesh_ascii, gltf, iqe, obj, pmx, smd, fbx]


__all__ = [
    "FORMATS",
    "mesh_ascii",
    "gltf",
    "iqe",
    "obj",
    "pmx",
    "smd",
    "fbx",
]
