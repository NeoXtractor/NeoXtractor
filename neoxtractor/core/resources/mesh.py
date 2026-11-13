"""Resource protocol entry for NeoX mesh files.

FORMAT  -> "mesh"
probe   -> exact-magic (0BBC8034) + header sanity, file-like contract
read    -> canonical ``MeshData`` IR (``CANONICAL_TYPE`` marks it directly
           displayable; no ``view`` convert target)
convert -> target names below (all kind="file": OBJ/glTF/IQE/PMX/SMD/FBX/ASCII)

The canonical IR is produced once by ``read``; ``convert`` consumes that IR
(not the file), so re-exporting to several formats never re-parses.
Implementation lives in the private ``_mesh`` package.
"""

from __future__ import annotations

from typing import Optional

from neoxtractor.core.utils.filelike import read_all, read_head
from neoxtractor.core.resources._mesh.converter.formats import (
    ascii as _ascii,
    fbx as _fbx,
    gltf as _gltf,
    iqe as _iqe,
    obj as _obj,
    pmx as _pmx,
    smd as _smd,
)
from neoxtractor.core.resources._mesh.loader import MeshData, MeshLoader
from neoxtractor.core.utils.result import Category, ProbeResult
from neoxtractor.core.utils.target import Target

# ``MeshData`` is this module's canonical product (``CANONICAL_TYPE``), so it
# is re-exported here: callers name it through ``resources.mesh`` and never
# reach into the private ``_mesh`` package.
__all__ = [
    "CANONICAL_TYPE",
    "FORMAT",
    "MESH_MAGIC",
    "TARGETS",
    "MeshData",
    "convert",
    "probe",
    "read",
]

FORMAT = "mesh"
CANONICAL_TYPE = "MeshData"
MESH_MAGIC = bytes([0x34, 0x80, 0xC8, 0xBB])

# (target name, format module, ext).  fbx is listed but may raise at runtime
# if its dependencies are unavailable; convert() surfaces the error.
_CONVERTERS = {
    "obj": (_obj, ".obj"),
    "gltf": (_gltf, ".gltf"),
    "iqe": (_iqe, ".iqe"),
    "pmx": (_pmx, ".pmx"),
    "smd": (_smd, ".smd"),
    "fbx": (_fbx, ".fbx"),
    "txt": (_ascii, ".txt"),
}

TARGETS = tuple(
    Target(name, "file", ext, mod.NAME) for name, (mod, ext) in _CONVERTERS.items()
)


def probe(f) -> ProbeResult:
    """Score how confidently *f* is a NeoX mesh (header only)."""
    head = read_head(f, 12)
    if len(head) < 12 or head[:4] != MESH_MAGIC:
        return ProbeResult(0)
    # Bytes 6-7 are the constant tag 0x0500, compared as raw bytes.
    if head[6:8] != b"\x05\x00":
        return ProbeResult(40, "mesh", Category.MESH)  # layout unexpected
    return ProbeResult(100, "mesh", Category.MESH)


def read(f) -> Optional[MeshData]:
    """Parse *f* into the canonical MeshData IR.

    Exception contract: returns None only when *f* is not a mesh at all;
    if the magic says it is a mesh but parsing fails, raise so the caller
    can record the failure.
    """
    score = probe(f).score
    if score == 0:
        return None
    mesh = MeshLoader().load_from_bytes(read_all(f))
    if mesh is None:
        raise ValueError("mesh magic matched but no parser could decode the payload")
    return mesh


def convert(mesh: MeshData, target: str, **kwargs) -> bytes:
    """Convert a MeshData IR to an exported mesh file's bytes."""
    entry = _CONVERTERS.get(target)
    if entry is None:
        raise ValueError(f"Unsupported mesh target: {target!r}")
    module, _ext = entry
    return module.convert(mesh, **kwargs)
