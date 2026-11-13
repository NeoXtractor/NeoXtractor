"""Mesh viewer package (single entry point re-exporting the contract)."""

from .render_widget import MeshRenderWidget
from .viewer_widget import (
    ACCEPTED_EXTENSIONS,
    FALLBACK,
    INPUT,
    NAME,
    PRIORITY,
    VIEWER_CLASS,
    MeshViewer,
)

__all__ = [
    "NAME",
    "VIEWER_CLASS",
    "ACCEPTED_EXTENSIONS",
    "INPUT",
    "FALLBACK",
    "PRIORITY",
    "MeshRenderWidget",
    "MeshViewer",
]
