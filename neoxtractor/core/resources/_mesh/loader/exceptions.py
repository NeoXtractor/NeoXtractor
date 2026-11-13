"""
Custom exception classes for mesh loading operations.
"""

from typing import Optional


class MeshParsingError(Exception):
    """Base exception for mesh parsing errors."""

    def __init__(self, message: str, parser_name: Optional[str] = None):
        self.parser_name = parser_name
        super().__init__(message)


class InvalidMeshFormatError(MeshParsingError):
    """Raised when the mesh format is invalid or unsupported."""

    def __init__(
        self, message: str = "Invalid mesh format", parser_name: Optional[str] = None
    ):
        super().__init__(message, parser_name)


class MeshDataCorruptedError(MeshParsingError):
    """Raised when mesh data appears to be corrupted."""

    def __init__(
        self,
        message: str = "Mesh data appears to be corrupted",
        parser_name: Optional[str] = None,
    ):
        super().__init__(message, parser_name)


class InsufficientDataError(MeshParsingError):
    """Raised when there isn't enough data to parse the mesh."""

    def __init__(
        self,
        message: str = "Insufficient data for mesh parsing",
        parser_name: Optional[str] = None,
    ):
        super().__init__(message, parser_name)
