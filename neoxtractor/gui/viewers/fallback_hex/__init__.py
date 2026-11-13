"""Binary fallback viewer package (single entry point re-exporting the contract)."""

from .hex_area import HexArea
from .hex_viewer import (
    ACCEPTED_EXTENSIONS,
    FALLBACK,
    INPUT,
    NAME,
    PRIORITY,
    VIEWER_CLASS,
    HexViewer,
)

__all__ = [
    "NAME",
    "VIEWER_CLASS",
    "ACCEPTED_EXTENSIONS",
    "INPUT",
    "FALLBACK",
    "PRIORITY",
    "HexArea",
    "HexViewer",
]
