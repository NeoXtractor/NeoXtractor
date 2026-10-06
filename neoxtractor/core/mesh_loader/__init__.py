"""
Mesh Loader Module

A modular mesh loading system for parsing .mesh files using multiple parsing strategies.
Supports adaptive parsing with fallback mechanisms for robust file handling.
"""

from . import parsers
from .exceptions import MeshParsingError

# Import the loader components after defining parsers
from .loader import MeshLoader
from .types import MeshData

__all__ = ["parsers", "MeshLoader", "MeshData", "MeshParsingError"]
