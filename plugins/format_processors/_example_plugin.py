"""Minimal example plugin for the processors facade.

This file is not a real format: it recognises a tiny fictional container
(magic "NXEP1") and uppercases its payload.  Copy it as the starting point
for your own parser/converter.  Files starting with "_" are skipped by the
scanner, so name your plugin something else.
"""

from __future__ import annotations

from neoxtractor.core.utils.filelike import read_all, read_head
from neoxtractor.core.utils.result import Category, ProbeResult

FORMAT = "nxexample"
CANONICAL_TYPE = "bytes"
TARGETS = ("txt",)  # bare strings coerce to kind="file", ext=".txt"

_MAGIC = b"NXEP1"


def probe(f) -> ProbeResult:
    """Header-only check; never raises."""
    if read_head(f, 5) == _MAGIC:
        return ProbeResult(100, "nxep", Category.OTHER)
    return ProbeResult(0)


def read(f):
    """Return the canonical payload, or None when this is not our format.

    A payload that starts with our magic but is too short raises: the
    caller's try/except records it (exception contract).
    """
    if read_head(f, 5) != _MAGIC:
        return None
    payload = read_all(f)[5:]
    if not payload:
        raise ValueError("NXEP1 container is empty")
    return payload


def convert(canonical: bytes, target: str) -> bytes:
    """Canonical -> target form.  Failures always raise."""
    if target != "txt":
        raise ValueError(f"Unsupported target: {target!r}")
    return canonical.upper()
