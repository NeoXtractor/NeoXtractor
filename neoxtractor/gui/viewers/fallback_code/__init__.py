"""Text fallback viewer package (single entry point re-exporting the contract)."""

from .code_editor import (
    ACCEPTED_EXTENSIONS,
    FALLBACK,
    INPUT,
    NAME,
    PRIORITY,
    VIEWER_CLASS,
    CodeEditor,
)

__all__ = [
    "NAME",
    "VIEWER_CLASS",
    "ACCEPTED_EXTENSIONS",
    "INPUT",
    "FALLBACK",
    "PRIORITY",
    "CodeEditor",
]
