"""Container-module contract validation for the packages layer.

A container module (``packages/<dir>/interface.py``) is duck-typed, with no
base class: it must expose ``FORMAT``, ``PRIORITY``, ``OPTIONS_TYPE``,
``matches`` and ``load``.  Discovery and the driver validate against the
single standard defined here.

``matches(file, want_ask)`` always takes two arguments.  ``want_ask`` is
the declared context ``(declaring format, child_ask)`` when the file was
pulled by another module's want, else ``None`` (seed, standalone, or
companion).  ``child_ask`` is a read-only query over the declarer's
:attr:`Claim.private` pre-parse cache; it never triggers IO on the
declaring file, and a child calling it with an incompatible signature or a
faulting callback degrades to ``None`` via the driver's guard.

Beyond the attribute checks, **modules are stateless**: a discovered module
lives in ``sys.modules`` for the whole process, so module-level mutable data
leaks past ``Archive.close()`` and races across concurrent scans.  Per-session
data (tables, handle LRU, caches) lives exclusively inside the Archive that
``load()`` returns; ``matches()`` is a pure function of the file's bytes and
``load()`` of its unit and options.  Immutable constants (magic tables, key
tables) may live at module level.  This is a review contract; no runtime check
enforces it.

:func:`validate_module` checks the static contract at discovery time, before
any file is read.  :func:`validate_claim` checks a Claim the instant the
driver receives it from ``matches()``: the declared format and entry match
what was asked, and every companion is a plain same-directory sibling.  A
:class:`Want` validates in its own constructor, so a malformed want cannot
exist to be claimed and wants need no re-check.  The companion rule is
defined in :mod:`base` (``companion_for`` and its predicate) and re-applied
here, so a module that bypassed the sanctioned helper is still refused.
"""

from __future__ import annotations

from types import ModuleType

from neoxtractor.core.packages.base import (
    Claim,
    ReadOptions,
    companion_is_exact,
)
from neoxtractor.core.packages.errors import ProtocolError

__all__ = [
    "REQUIRED_ATTRS",
    "is_container_module",
    "validate_module",
    "validate_claim",
]

# All five are hard requirements; each backs one stage: FORMAT = discovery,
# PRIORITY = ordering, OPTIONS_TYPE = option checking, matches = claiming,
# load = loading.
REQUIRED_ATTRS = ("FORMAT", "PRIORITY", "OPTIONS_TYPE", "matches", "load")


def is_container_module(module: ModuleType) -> bool:
    """Cheap presence check used by the discovery scan.

    Only ``FORMAT`` is probed: a directory without it is a placeholder or a
    private helper package and is skipped silently.  A FORMAT that is present
    but wrong is left for :func:`validate_module` to report, so misconfiguration
    surfaces as a warning instead of vanishing.
    """
    return isinstance(getattr(module, "FORMAT", None), str) and bool(module.FORMAT)


def validate_module(module: ModuleType, dirname: str, path: str = "") -> str:
    """Strict static check of a container module; returns its FORMAT name.

    Raises:
        ProtocolError: a required attribute is missing or mistyped, FORMAT does
            not equal the directory name, PRIORITY is not a plain int (a bool is
            rejected), OPTIONS_TYPE is not a :class:`ReadOptions` subclass, or
            ``matches``/``load`` are not callable.
    """
    for attr in REQUIRED_ATTRS:
        if not hasattr(module, attr):
            raise ProtocolError(f"module lacks required attribute {attr!r} ({path})")

    fmt = module.FORMAT
    if not isinstance(fmt, str) or not fmt.strip():
        raise ProtocolError(f"FORMAT must be a non-empty string ({path})")
    if fmt != dirname:
        raise ProtocolError(
            f"FORMAT {fmt!r} must equal its directory name {dirname!r} ({path})"
        )

    priority = module.PRIORITY
    if isinstance(priority, bool) or not isinstance(priority, int):
        raise ProtocolError(
            f"PRIORITY must be an int, not {type(priority).__name__} ({path})"
        )

    options_type = module.OPTIONS_TYPE
    if not (isinstance(options_type, type) and issubclass(options_type, ReadOptions)):
        raise ProtocolError(f"OPTIONS_TYPE must be a ReadOptions subclass ({path})")

    for fn in ("matches", "load"):
        if not callable(getattr(module, fn)):
            raise ProtocolError(f"{fn} must be callable ({path})")

    return fmt


def validate_claim(claim: Claim, module_format: str, file_path: str) -> None:
    """Re-check a Claim the driver just received from ``matches()``.

    The driver calls this before consuming a claim, so a bad declaration is
    refused at the boundary instead of corrupting a unit later.

    Raises:
        ProtocolError: the claim names a format other than its module's FORMAT,
            points at a file other than the one it was shown, or declares a
            companion that is not a plain same-directory sibling of the entry.
    """
    if claim.format != module_format:
        raise ProtocolError(
            f"claim format {claim.format!r} != module FORMAT {module_format!r}"
        )
    if claim.entry != file_path:
        raise ProtocolError(
            f"claim entry {claim.entry!r} != file it was shown {file_path!r}"
        )
    for companion in claim.companions:
        if not companion_is_exact(companion, claim.entry):
            raise ProtocolError(
                f"companion {companion!r} is not a plain sibling of entry "
                f"{claim.entry!r}"
            )
