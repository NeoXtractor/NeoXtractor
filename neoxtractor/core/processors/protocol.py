"""Protocol validation for resource / plugin format modules.

A format module -- built-in or user plugin -- is plain duck-typed: it must
expose ``FORMAT`` and ``probe``, may expose ``read`` / ``convert`` /
``TARGETS`` / ``CANONICAL_TYPE``.  There is no base class to inherit.
Both discovery channels validate identically through this module.

Stateless discipline: built-ins live in ``sys.modules`` for the whole
process, so ``probe``/``read``/``convert`` must touch only the file-like
they are handed and return fresh objects; per-call data never lives at
module level (immutable constants may).  A plugin file is re-executed on
every load: it may hold state within one load but must not assume that
state survives a reload.
"""

from __future__ import annotations

from types import ModuleType

from neoxtractor.core.utils.target import Target, as_target

__all__ = [
    "REQUIRED_ATTRS",
    "OPTIONAL_ATTRS",
    "is_format_module",
    "validate_module",
    "module_format_name",
    "module_targets",
    "module_canonical_type",
]

# FORMAT + probe are the hard requirements; everything else is optional and
# probed with getattr at call time.
REQUIRED_ATTRS = ("FORMAT", "probe")
OPTIONAL_ATTRS = ("read", "convert", "TARGETS", "CANONICAL_TYPE")


def is_format_module(module: ModuleType) -> bool:
    """Cheap boolean check used by the built-in discovery scan."""
    return (
        isinstance(getattr(module, "FORMAT", None), str)
        and bool(module.FORMAT)
        and callable(getattr(module, "probe", None))
    )


def validate_module(module: ModuleType, path: str = "") -> str:
    """Strict check used by plugin loading; returns the FORMAT name.

    Raises:
        PluginError: on a missing/ill-typed FORMAT, a non-callable probe, or
            an optional attribute of the wrong shape (so a half-written plugin
            fails with a precise message instead of at first use).
    """
    from neoxtractor.core.processors.errors import PluginError

    for attr in REQUIRED_ATTRS:
        if not hasattr(module, attr):
            raise PluginError(f"module lacks required attribute {attr!r}", path)

    fmt = module.FORMAT
    if not isinstance(fmt, str) or not fmt.strip():
        raise PluginError("FORMAT must be a non-empty string", path)
    if not callable(module.probe):
        raise PluginError("probe must be callable", path)

    for attr in ("read", "convert"):
        value = getattr(module, attr, None)
        if value is not None and not callable(value):
            raise PluginError(f"{attr} must be callable when present", path)

    targets = getattr(module, "TARGETS", None)
    if targets is not None:
        if not isinstance(targets, (tuple, list)):
            raise PluginError("TARGETS must be a tuple/list", path)
        for t in targets:
            if not isinstance(t, (str, Target)):
                raise PluginError("TARGETS entries must be str or Target", path)

    return fmt


def module_format_name(module: ModuleType) -> str:
    return module.FORMAT.strip()


def module_targets(module: ModuleType) -> tuple[Target, ...]:
    """Normalised TARGETS of a module (empty when not declared)."""
    return tuple(as_target(t) for t in getattr(module, "TARGETS", ()))


def module_canonical_type(module: ModuleType) -> str:
    """Human-readable canonical type name for Resolution display."""
    return str(getattr(module, "CANONICAL_TYPE", ""))
