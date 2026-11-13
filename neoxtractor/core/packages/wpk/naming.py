"""Naming rules for the wpk storage family (wpk parts, idx index, slot stores).

How one logical package names its sibling files: the single lowercase-hex
suffix carrying the package id, and the slot directory holding per-file
stores.  idx builds its wants from these rules; wpk parses its own name
with them.
"""

from __future__ import annotations

from neoxtractor.core.packages.base import MAX_POOL, Role, Want

_HEX = "0123456789abcdef"


def id_globs(stem: str, ext: str) -> tuple[Want, ...]:
    """Globs for the single-hex-suffix sibling family of one logical store.

    The trailing token in each glob is one lowercase hex digit of the package
    id, optionally preceded by ``_``.
    """
    return (
        Want(Role.DATA, f"{stem}[0-9a-f]{ext}"),
        Want(Role.DATA, f"{stem}_[0-9a-f]{ext}"),
    )


def slot_want(stem: str) -> Want:
    """Want for candidate slot stores of one logical package (idx -> slot dirs).

    The glob is loose (``{stem}/*``); the ``[0-9a-f]{32}`` pattern does the
    narrowing -- a slot file is named by its 32-hex content hash -- so
    unrelated files never reach matches().  Remaining filters: wpk.matches
    (1DPW header) and the load-time hash join.  An absent directory costs one
    failed glob.
    """
    return Want(
        Role.DATA, f"{stem}/*", max_files=MAX_POOL, pattern="[0-9a-f]{32}"
    )


def strip_trailing_hex(stem: str) -> str:
    """Drop the package-id token from a store name, and any ``_`` it exposes.

    A store name is ``{base}{hex}`` or ``{base}_{hex}``, where ``hex`` is the
    single package-id digit; exactly one trailing ``[0-9a-f]`` is removed, and
    a ``_`` left dangling at the end is the token's separator, not part of the
    base, so it goes too.  A name not ending in a lowercase hex digit carries
    no token and is returned unchanged.  For slot-directory naming only.
    """
    if stem and stem[-1] in _HEX:
        stem = stem[:-1]
        if stem.endswith("_"):
            stem = stem[:-1]
    return stem
