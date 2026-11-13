"""Types for Mesh Loader"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import numpy as np

MAX_VERTEX_COUNT = 500000
MAX_FACE_COUNT = 250000
MAX_BONE_COUNT = 20000


@dataclass
class Mesh:
    """
    Represents the mesh part of a full mesh file.
    """

    vertexes: int = 0
    faces: int = 0
    position: list[tuple[float, float, float]] = field(
        default_factory=list[tuple[float, float, float]]
    )
    normal: list[tuple[float, float, float]] = field(
        default_factory=list[tuple[float, float, float]]
    )
    face: list[tuple[int, int, int]] = field(default_factory=list[tuple[int, int, int]])
    uv: list[tuple[float, float]] = field(default_factory=list[tuple[float, float]])


@dataclass
class Bones:
    """
    Represents the bone/skeleton part of a full mesh file.
    """

    # Bone/skeleton data
    has_bones: int = 0
    parents: list[int] = field(default_factory=list[int])
    names: list[str] = field(default_factory=list[str])
    matrix: list[np.ndarray] = field(default_factory=list[np.ndarray])
    count: int = 0

    # Vertex bone assignments
    joints: list[tuple[int, int, int, int]] = field(
        default_factory=list[tuple[int, int, int, int]]
    )
    weights: list[tuple[float, float, float, float]] = field(
        default_factory=list[tuple[float, float, float, float]]
    )


@dataclass
class MeshData:
    """
    Standardized mesh data structure containing all parsed mesh information.

    This dataclass provides a consistent interface for mesh data across all parsers,
    ensuring type safety and clear documentation of the expected data structure.
    """

    # Metadata
    version: int
    type: int
    mesh: Mesh = field(default_factory=Mesh)
    bones: Bones = field(default_factory=Bones)

    # Core mesh geometry

    @property
    def vertex_count(self) -> int:
        """Get the number of vertices in the mesh."""
        return self.mesh.vertexes

    @property
    def face_count(self) -> int:
        """Get the number of faces in the mesh."""
        return self.mesh.faces

    @property
    def uv_count(self) -> int:
        """Get the number of UV coordinates in the mesh."""
        return len(self.mesh.uv)

    @property
    def has_bones(self) -> bool:
        """Check if the mesh has bone data."""
        return self.bones.has_bones == 1 or self.bones.has_bones == 4

    @property
    def has_uvs(self) -> bool:
        """Check if the mesh has UV coordinate data."""
        return len(self.mesh.uv) == len(self.mesh.position)

    def validate(self) -> bool:
        """
        Validate the consistency of mesh data.

        Returns:
            True if the mesh data is consistent, False otherwise
        """

        # Check that face indices are valid
        if self.mesh.face:
            max_index = max(max(face) for face in self.mesh.face)
            if max_index >= self.mesh.faces:
                return False

        # Check bone data consistency
        if self.bones.has_bones:
            if (
                len(self.bones.joints) != self.mesh.vertexes
                or len(self.bones.weights) != self.mesh.vertexes
            ):
                return False

            # Check bone indices are valid
            if self.bones.joints:
                max_bone_index = max(max(bones) for bones in self.bones.joints)
                if max_bone_index >= len(self.bones.names):
                    return False

        return True


class BaseMeshParser(ABC):
    """Abstract base class for mesh parsers."""

    @abstractmethod
    def parse(self, data: bytes) -> MeshData:
        """
        Parse mesh data.

        Args:
            data: Raw mesh data as bytes

        Returns:
            MeshData object containing parsed mesh data

        Raises:
            MeshParsingError: If parsing fails
        """
        raise NotImplementedError

    def _standardize_mesh_data(self, model: dict[str, Any]) -> MeshData:
        """
        Convert raw parsed data to standardized MeshData object.

        Args:
            model: Raw parsed mesh data dictionary

        Returns:
            Standardized MeshData object with unified field names and structure
        """
        # Create MeshData with unified field mapping
        mesh_data = MeshData(
            # Metadata
            version=model["version"],
            type=model["type"],
            # Core mesh data
            mesh=Mesh(
                vertexes=model["mesh"]["data"][0],
                faces=model["mesh"]["data"][1],
                position=model["mesh"]["position"],
                normal=model["mesh"]["normal"],
                face=model["mesh"]["face"],
                uv=model["mesh"]["uv"],
            ),
            # Bone data
            bones=Bones(
                has_bones=model["bones"]["has_bones"],
                parents=model["bones"]["parent_connections"],
                names=model["bones"]["names"],
                matrix=model["bones"]["matrix"],
                count=model["bones"]["count"],
                joints=model["bones"]["joints"],
                weights=model["bones"]["weights"],
            )
            if model["bones"]["has_bones"] == 1 or model["bones"]["has_bones"] == 4
            else Bones(
                has_bones=model["bones"]["has_bones"],
                parents=[],
                names=[],
                matrix=[],
                count=0,
                joints=[],
                weights=[],
            ),
        )
        return mesh_data

    def _validate_vertex_count(self, vertex_count: int) -> None:
        """
        Validate vertex count against maximum limits.

        Args:
            vertex_count: Number of vertices in the mesh

        Raises:
            ValueError: If vertex count exceeds maximum limit
        """
        if vertex_count == 0:
            raise ValueError("Vertex count cannot be zero")
        if vertex_count > MAX_VERTEX_COUNT:
            raise ValueError(
                f"Vertex count {vertex_count} exceeds maximum limit of {MAX_VERTEX_COUNT}"
            )

    def _validate_face_count(self, face_count: int) -> None:
        """
        Validate face count against maximum limits.

        Args:
            face_count: Number of faces in the mesh

        Raises:
            ValueError: If face count exceeds maximum limit
        """
        if face_count == 0:
            raise ValueError("Face count cannot be zero")
        if face_count > MAX_FACE_COUNT:
            raise ValueError(
                f"Face count {face_count} exceeds maximum limit of {MAX_FACE_COUNT}"
            )

    def _validate_bone_count(self, bone_count: int) -> None:
        """
        Validate bone count against maximum limits.

        Args:
            bone_count: Number of bones in the mesh

        Raises:
            ValueError: If bone count exceeds maximum limit
        """
        if bone_count > MAX_BONE_COUNT:
            raise ValueError(
                f"Bone count {bone_count} exceeds maximum limit of {MAX_BONE_COUNT}"
            )
