"""Cocos UI scene viewer package (single entry point re-exporting the contract)."""

from .cocos_viewer import (
    ACCEPTED_EXTENSIONS,
    FALLBACK,
    INPUT,
    NAME,
    PRIORITY,
    VIEWER_CLASS,
    CocosViewer,
)

__all__ = [
    "NAME",
    "VIEWER_CLASS",
    "ACCEPTED_EXTENSIONS",
    "INPUT",
    "FALLBACK",
    "PRIORITY",
    "CocosViewer",
]
