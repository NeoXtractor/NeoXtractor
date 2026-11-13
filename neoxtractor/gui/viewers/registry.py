"""Viewer discovery, selection, and lazy instantiation.

Discovery scans the top-level modules and packages of
``neoxtractor.gui.viewers`` with ``pkgutil`` (works in frozen builds).
A candidate's name ends with ``_viewer`` and does not start with ``_``;
a package form requires ``__init__.py`` re-exporting the contract members.
``base`` and ``registry`` are never candidates.

The declared fallbacks ``fallback_code`` and ``fallback_hex`` are exempt
from the name rule: imported directly here, otherwise ordinary contract
citizens.

A candidate violating the contract (field table at ``_REQUIRED_MEMBERS``)
is logged and skipped, so one broken viewer never removes the others.
Instantiation is lazy: a viewer class is built on first use and cached;
no widget is built at discovery time.  Shared widgets are reachable only
through :func:`get_viewer` / :func:`peek_viewer`, never through the
:class:`ViewerInfo` objects the public queries hand out.
"""

from __future__ import annotations

import importlib
import pkgutil
import threading
from types import ModuleType

from neoxtractor.core.utils.logger import get_logger
from neoxtractor.gui.viewers.base import FallbackKind, InputForm, ViewerBase

__all__ = [
    "ViewerInfo",
    "ensure_loaded",
    "find_specialist",
    "find_viewer_class",
    "get_viewer",
    "info_for",
    "list_viewers",
    "peek_viewer",
    "reset_registry",
    "viewer_names",
]

_VIEWERS_PKG = "neoxtractor.gui.viewers"
_SUFFIX = "_viewer"

# Declared fallbacks: imported directly, excluded from discovery.
_FALLBACK_MODULES = ("fallback_code", "fallback_hex")
_NON_CANDIDATES = frozenset({"base", "registry", *_FALLBACK_MODULES})

# The contract every viewer module must declare, checked by _validate:
#
#   NAME                 str, non-empty            display label of the viewer
#   VIEWER_CLASS         type, ViewerBase subclass widget built on first use
#   ACCEPTED_EXTENSIONS  collection of lowercase dotless str; the extensions
#                        this viewer specializes in ("" is allowed for a
#                        fallback-only viewer)
#   INPUT                "read" | "stream"         what the dispatcher feeds
#                        load(): a processors ReadResult or an EntryStream
#   FALLBACK             None | "text" | "binary"  the content kind this
#                        viewer is the last resort for; None = specialist only
#   PRIORITY             int (not bool)            selection order, higher
#                        wins when several accept one extension
#
# All members are required; a missing one reports as "missing X".
_REQUIRED_MEMBERS = (
    "NAME",
    "VIEWER_CLASS",
    "ACCEPTED_EXTENSIONS",
    "INPUT",
    "FALLBACK",
    "PRIORITY",
)


class ViewerInfo:
    """One registered viewer module and its declared contract."""

    def __init__(self, module: ModuleType, name: str) -> None:
        self.name: str = name              # module name (stable cache key)
        self.display_name: str = module.NAME
        self.viewer_class: type[ViewerBase] = module.VIEWER_CLASS
        self.accepted_extensions: frozenset[str] = frozenset(
            module.ACCEPTED_EXTENSIONS
        )
        self.input: InputForm = module.INPUT
        self.fallback: FallbackKind | None = module.FALLBACK
        self.priority: int = module.PRIORITY

    def accepts(self, extension: str) -> bool:
        return extension in self.accepted_extensions


_lock = threading.RLock()
# Insertion order of _entries is the registration order.
_entries: dict[str, ViewerInfo] = {}
_discovered = False
# Instance cache stays private: ViewerInfo is pure metadata.
_instances: dict[str, ViewerBase] = {}


def _validate(module: ModuleType, name: str) -> str | None:
    """Return an error message for a contract violation, else ``None``.

    Field requirements are in the ``_REQUIRED_MEMBERS`` table above;
    missing members and wrong types report distinct messages.
    """
    for member in _REQUIRED_MEMBERS:
        if not hasattr(module, member):
            return f"missing {member}"

    if not isinstance(module.NAME, str) or not module.NAME:
        return "NAME must be a non-empty str"

    viewer_class = module.VIEWER_CLASS
    if not isinstance(viewer_class, type) or not issubclass(
        viewer_class, ViewerBase
    ):
        return "VIEWER_CLASS must be a ViewerBase subclass"

    extensions = module.ACCEPTED_EXTENSIONS
    if not isinstance(extensions, (set, frozenset, tuple, list)):
        return "ACCEPTED_EXTENSIONS must be a collection of str"
    if not all(
        isinstance(e, str) and e == e.lower() and not e.startswith(".")
        for e in extensions
    ):
        return "ACCEPTED_EXTENSIONS entries must be lowercase, dotless"

    if module.INPUT not in ("read", "stream"):
        return 'INPUT must be "read" or "stream"'

    if module.FALLBACK not in (None, "text", "binary"):
        return 'FALLBACK must be None, "text", or "binary"'

    priority = module.PRIORITY
    if not isinstance(priority, int) or isinstance(priority, bool):
        return "PRIORITY must be an int (not bool)"

    return None


def _register(module: ModuleType, name: str) -> bool:
    problem = _validate(module, name)
    if problem is not None:
        get_logger().warning("Viewer %s skipped: %s", name, problem)
        return False
    if name in _entries:
        get_logger().warning("Viewer %s already registered; keeping first", name)
        return False
    _entries[name] = ViewerInfo(module, name)
    return True


def _load_fallbacks() -> None:
    for name in _FALLBACK_MODULES:
        try:
            module = importlib.import_module(f"{_VIEWERS_PKG}.{name}")
        except Exception as exc:
            get_logger().warning("Fallback viewer %s failed to import: %s", name, exc)
            continue
        _register(module, name)


def _discover() -> None:
    package = importlib.import_module(_VIEWERS_PKG)
    for info in pkgutil.iter_modules(package.__path__):
        name = info.name
        if name.startswith("_") or name in _NON_CANDIDATES:
            continue
        if not name.endswith(_SUFFIX):
            continue
        try:
            module = importlib.import_module(f"{_VIEWERS_PKG}.{name}")
        except Exception as exc:
            get_logger().warning("Viewer %s failed to import: %s", name, exc)
            continue
        _register(module, name)


def ensure_loaded() -> None:
    """Discover viewers once: the declared fallbacks, then the scan."""
    global _discovered
    if _discovered:
        return
    with _lock:
        if _discovered:
            return
        _load_fallbacks()
        _discover()
        _discovered = True


def list_viewers() -> list[ViewerInfo]:
    """All viewers in selection order: PRIORITY desc, then display name asc.

    The single ordering surface every consumer walks.
    """
    ensure_loaded()
    return sorted(
        _entries.values(),
        key=lambda v: (-v.priority, v.display_name),
    )


def find_specialist(extension: str) -> ViewerInfo | None:
    """Extension match among declared viewers only -- never a fallback.

    An entry with no usable extension must have its content inspected
    before a fallback is chosen.
    """
    if not extension:
        return None
    for info in list_viewers():
        if info.accepts(extension):
            return info
    return None


def find_viewer_class(
    extension: str,
    is_text: bool = False,
) -> ViewerInfo | None:
    """Pick the viewer for one source: extension specialist, else fallback.

    1. the highest-priority viewer whose ``ACCEPTED_EXTENSIONS`` holds
       ``extension``;
    2. otherwise the declared fallback for the content kind (text or
       binary).  A nameless entry before its probe lands simply reaches the
       fallback.
    """
    infos = list_viewers()
    if extension:
        for info in infos:
            if info.accepts(extension):
                return info
    want: FallbackKind = "text" if is_text else "binary"
    for info in infos:
        if info.fallback == want:
            return info
    return None


def get_viewer(name: str) -> ViewerBase | None:
    """The cached instance for one viewer module, built on first use."""
    ensure_loaded()
    info = _entries.get(name)
    if info is None:
        return None
    instance = _instances.get(name)
    if instance is None:
        instance = info.viewer_class()
        _instances[name] = instance
    return instance


def peek_viewer(name: str) -> ViewerBase | None:
    """The instance for one viewer only if it was already built.

    Never instantiates: used when releasing viewers on clear, where an
    unopened viewer has nothing to unload.
    """
    return _instances.get(name)


def info_for(name: str) -> ViewerInfo | None:
    """The registered info for one viewer module name, or None."""
    ensure_loaded()
    return _entries.get(name)


def viewer_names() -> list[str]:
    """Module names of all registered viewers, in selection order."""
    return [info.name for info in list_viewers()]


def reset_registry() -> None:
    """Forget discovery and instances (tests and reloads)."""
    global _discovered
    _entries.clear()
    _instances.clear()
    _discovered = False
