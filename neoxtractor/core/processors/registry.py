"""Processors registry: discovery, scoring, and the facade algorithms.

The registry keys everything by ``FORMAT`` name and merges two channels:

- **built-ins**: lazily discovered from ``neoxtractor.core.resources`` (top
  level, non-underscore ``.py`` files exposing ``FORMAT`` + ``probe``), via
  ``pkgutil`` so PyInstaller frozen builds work too.
- **plugins**: user-supplied files from the environment directories (passive
  scan) or an explicit :func:`load_plugin` call (the UI/CLI entry point).

Scoring normalises raw probe confidence to ``raw * 2`` for built-ins and
``raw * 2 + 1`` for plugins: raw confidence dominates, plugins win ties,
and equal kind + equal score ties break on the FORMAT name for determinism.

Facade exception contract:

- :func:`resolve` in automatic mode never raises for "no format claims
  this" -- it returns ``None``.  :func:`read` returns ``None`` only when
  the best claimant had a ``read`` and declined; when the best claimant is
  probe-only it raises :class:`ProcessorNoReader` naming the format, so
  the caller learns *what* the payload is even when nothing can decode it.
- a forced ``format=`` raises :class:`UnknownFormat` when unknown and
  :class:`FormatMismatch` when its probe rejects the source or its ``read``
  returns ``None`` despite the probe hit; there is *no* fallback to the
  runner-up once the caller asserted a format.
- in automatic mode a candidate whose ``read`` raises is logged and the next
  candidate is tried; if every candidate fails the highest-confidence
  exception is re-raised for the caller to record (failure bucket).
- a payload that claims to be a format but is corrupt propagates the
  module's ``read`` exception untouched.
"""

from __future__ import annotations

import importlib
import pkgutil
import sys
import threading
from pathlib import Path
from types import ModuleType
from typing import NamedTuple

from neoxtractor.core.processors import plugins as _plugins
from neoxtractor.core.processors.errors import (
    FormatMismatch,
    PluginError,
    ProcessorError,
    ProcessorNoReader,
    UnknownFormat,
    UnknownTarget,
)
from neoxtractor.core.processors.protocol import (
    is_format_module,
    module_canonical_type,
    module_targets,
    validate_module,
)
from neoxtractor.core.utils.filelike import FileLike
from neoxtractor.core.utils.logger import get_logger
from neoxtractor.core.utils.result import ProbeResult, ReadResult, as_probe_result, as_read_result
from neoxtractor.core.utils.target import target_names

__all__ = [
    "FormatInfo",
    "ProbeHit",
    "Resolution",
    "SourceInfo",
    "convert",
    "convert_source",
    "guess",
    "info",
    "list_sources",
    "load_plugin",
    "read",
    "reload_environment",
    "resolve",
    "unload_plugin",
]

_KIND_BUILTIN = "builtin"
_KIND_PLUGIN = "plugin"
_MEMORY_PATH = "<memory>"

_RESOURCES_PKG = "neoxtractor.core.resources"


class ProbeHit(NamedTuple):
    """One scored candidate from a probe pass."""

    format: str
    score: int  # effective (weighted) score used for ordering
    raw_score: int  # the module's own 0~100 confidence
    ext: str
    category: str
    kind: str  # "builtin" | "plugin"


class Resolution(NamedTuple):
    """The winning format for a source, with everything the UI needs."""

    format: str
    score: int
    raw_score: int
    ext: str
    category: str
    targets: tuple
    canonical_type: str
    kind: str
    candidates: list[ProbeHit]  # all score>0 hits, best first


class SourceInfo(NamedTuple):
    """Registry bookkeeping entry surfaced to plugin-management UIs."""

    format: str
    kind: str
    path: str
    active: bool  # False when shadowed by an override
    can_read: bool
    can_convert: bool
    targets: tuple


class FormatInfo(NamedTuple):
    """Static metadata for one registered format -- no source, no probing.

    The counterpart to :class:`Resolution` for callers that already know the
    format name (a menu built from ``FORMAT``, a viewer declaring
    ``INPUT="read"``) and need the module's capabilities without re-reading
    a file to probe it.  ``targets``/``canonical_type`` mirror what
    :func:`resolve` would report; ``can_read``/``can_convert`` say whether the
    module provides those entry points at all.
    """

    format: str
    canonical_type: str
    targets: tuple
    can_read: bool
    can_convert: bool


class _Entry(NamedTuple):
    module: ModuleType
    kind: str
    path: str
    shadowed: "_Entry | None" = None  # previous entry replaced by an override


_lock = threading.RLock()
_entries: dict[str, _Entry] = {}
_discovered = False


# ---------------------------------------------------------------- discovery

def _discover_builtins() -> None:
    """Import resources entry modules once and register valid ones.

    PyInstaller note: dynamic discovery here only works in a frozen build if
    the resource modules are actually in the bundle; main.spec must keep them
    via ``collect_submodules('neoxtractor.core.resources')`` or explicit
    hiddenimports (GUI statically imports most of them already).
    """
    global _discovered
    package = importlib.import_module(_RESOURCES_PKG)
    seen_formats: dict[str, str] = {}
    for modinfo in pkgutil.iter_modules(package.__path__):
        name = modinfo.name
        if name.startswith("_"):
            continue
        full = f"{_RESOURCES_PKG}.{name}"
        try:
            module = importlib.import_module(full)
        except Exception as exc:
            get_logger().warning("Skipping resource module %s: %s", full, exc)
            continue
        if not is_format_module(module):
            continue
        fmt = module.FORMAT
        if fmt in seen_formats:
            get_logger().warning(
                "Duplicate FORMAT %r from %s ignored (kept %s)",
                fmt,
                full,
                seen_formats[fmt],
            )
            continue
        seen_formats[fmt] = full
        if fmt in _entries:
            # Discovery must never clobber an existing registration
            # (public paths ensure_loaded() first).  Park the built-in at
            # the bottom of the shadow chain so unload still reveals it.
            builtin_entry = _Entry(module, _KIND_BUILTIN, full, None)
            _entries[fmt] = _chain_with_leaf(_entries[fmt], builtin_entry)
            get_logger().warning(
                "Built-in FORMAT %r from %s registered below existing entry",
                fmt,
                full,
            )
            continue
        _entries[fmt] = _Entry(module, _KIND_BUILTIN, full, None)


def _chain_with_leaf(entry: _Entry, leaf: _Entry) -> _Entry:
    """Copy *entry* with *leaf* appended to the end of its shadow chain."""
    if entry.shadowed is None:
        return entry._replace(shadowed=leaf)
    return entry._replace(shadowed=_chain_with_leaf(entry.shadowed, leaf))


def _scan_environment() -> None:
    """Passive plugin-directory scan; shared by the lazy load and
    :func:`reload_environment`.

    Files registered anywhere in the chain are left untouched; new files
    get the same strict validation as explicit loading (malformed plugins
    are reported, not silently ignored); collisions never override;
    failures log and skip.
    """
    with _lock:
        known = _registered_plugin_paths()
        for path, module, error in _plugins.scan_plugin_dirs():
            # Resolved so the key matches load_plugin()'s stored paths.
            key = str(Path(path).resolve())
            if key in known:
                continue
            if module is None:
                get_logger().warning("Skipping plugin %s: %s", path, error)
                continue
            try:
                fmt = validate_module(module, key)
            except PluginError as exc:
                get_logger().warning("Skipping plugin %s: %s", path, exc.reason)
                continue
            if fmt in _entries:
                get_logger().warning(
                    "Plugin %s claims FORMAT %r already registered; skipped "
                    "(use the explicit override load to replace it)",
                    path,
                    fmt,
                )
                continue
            _entries[fmt] = _Entry(module, _KIND_PLUGIN, key, None)
            known.add(key)
            get_logger().info("Loaded format plugin: %s (%s)", fmt, path)


def _registered_plugin_paths() -> set[str]:
    """Every plugin *file* path in the registry, shadowed members included.

    The in-memory sentinel is excluded: it is not a file, and keeping it out
    means a scan can never mistake it for an already-registered plugin.
    """
    paths: set[str] = set()
    for entry in _entries.values():
        walk: _Entry | None = entry
        while walk is not None:
            if walk.kind == _KIND_PLUGIN and walk.path != _MEMORY_PATH:
                paths.add(walk.path)
            walk = walk.shadowed
    return paths


def ensure_loaded() -> None:
    """Lazily build the registry: built-ins and environment plugins at once."""
    global _discovered
    with _lock:
        if not _discovered:
            _discover_builtins()
            _scan_environment()
            _discovered = True


# ----------------------------------------------------------------- scoring

def _weight(kind: str, raw: int) -> int:
    """Normalised 0~100 score -> comparable weight (plugins win ties)."""
    if raw <= 0:
        return 0
    return raw * 2 if kind == _KIND_BUILTIN else raw * 2 + 1


def _probe_entry(entry: _Entry, f) -> ProbeResult:
    """Run one module's probe defensively: it must never break the pass."""
    ensure_loaded()
    try:
        return as_probe_result(entry.module.probe(f))
    except Exception as exc:
        get_logger().debug(
            "probe() failed for format %r: %s", entry.module.FORMAT, exc
        )
        return ProbeResult(0)


def _probe_all(f) -> list[tuple[_Entry, ProbeResult]]:
    """Probe every *active* registration (``_entries`` never holds a shadowed
    entry -- those are only reachable through an override's ``shadowed``)."""
    ensure_loaded()
    return [(entry, _probe_entry(entry, f)) for entry in _entries.values()]


def _hit(entry: _Entry, pr: ProbeResult) -> ProbeHit:
    """Build the scored candidate for one entry's probe result."""
    return ProbeHit(
        format=entry.module.FORMAT,
        score=_weight(entry.kind, pr.score),
        raw_score=pr.score,
        ext=pr.ext,
        category=pr.category,
        kind=entry.kind,
    )


def _sorted_hits(f) -> list[ProbeHit]:
    pairs = _probe_all(f)
    hits = [_hit(entry, pr) for entry, pr in pairs if pr.score > 0]
    hits.sort(key=lambda h: (-h.score, h.format))
    return hits


def _entry_for(fmt: str) -> _Entry:
    ensure_loaded()
    entry = _entries.get(fmt)
    if entry is None:
        raise UnknownFormat(fmt, list(_entries))
    return entry


def _resolution(hit: ProbeHit, entry: _Entry, hits: list[ProbeHit]) -> Resolution:
    return Resolution(
        format=hit.format,
        score=hit.score,
        raw_score=hit.raw_score,
        ext=hit.ext,
        category=hit.category,
        targets=module_targets(entry.module),
        canonical_type=module_canonical_type(entry.module),
        kind=hit.kind,
        candidates=hits,
    )


# -------------------------------------------------------------------- API

def resolve(f: FileLike, *, name: str | None = None) -> Resolution | None:
    """Identify *f* by probing every registered format.

    Automatic mode returns the best :class:`Resolution`, or ``None`` when no
    format claims the source.  ``name=`` asserts a format: unknown ->
    :class:`UnknownFormat`; rejected by that format's probe ->
    :class:`FormatMismatch` (no runner-up fallback).
    """
    if name is not None:
        entry = _entry_for(name)
        pr = _probe_entry(entry, f)
        if pr.score <= 0:
            raise FormatMismatch(name, pr.score)
        hit = _hit(entry, pr)
        return _resolution(hit, entry, [hit])
    hits = _sorted_hits(f)
    if not hits:
        return None
    hit = hits[0]
    return _resolution(hit, _entries[hit.format], hits)


def read(f: FileLike, format: str | None = None) -> ReadResult | None:
    """Decode *f* to its canonical object.

    Automatic mode tries candidates best-first (skipping probe-only modules
    without ``read``) and returns ``ReadResult`` with the winning
    format/ext/category echoed back.  A candidate whose ``read`` returns None
    or raises moves on to the next (failures are logged).  When nothing
    decodes, the highest-scoring candidate decides the outcome: it raised ->
    its exception is re-raised; it is probe-only -> :class:`ProcessorNoReader`
    identifying the format; it merely declined -> ``None`` ("nothing could
    decode this").  Forced mode (``format=``) raises :class:`UnknownFormat` /
    :class:`FormatMismatch` and propagates decode failures of the claimed
    format so the caller can record them.
    """
    if format is not None:
        entry = _entry_for(format)
        pr = _probe_entry(entry, f)
        if pr.score <= 0:
            raise FormatMismatch(format, pr.score)
        reader = getattr(entry.module, "read", None)
        if reader is None:
            raise ProcessorNoReader(format, pr.ext, pr.score)
        result = reader(f)
        if result is None:
            raise FormatMismatch(format, pr.score)
        if isinstance(result, ReadResult) and result.format:
            return result
        return as_read_result(
            result, format=format, ext=pr.ext, category=pr.category
        )

    # When nothing decodes, the highest-scoring candidate decides the
    # outcome (see the docstring).  The re-raise logs format-level detail
    # here; the caller adds entry-level context for the failure bucket.
    hits = _sorted_hits(f)
    first_error: Exception | None = None
    for hit in hits:
        entry = _entries[hit.format]
        reader = getattr(entry.module, "read", None)
        if reader is None:
            continue
        # A previous candidate may have consumed the stream; rewind so each
        # candidate sees the payload from byte 0.  Bytes-like input has no
        # seek and closed streams fail seek -- both are left to the read
        # attempt itself.
        if not isinstance(f, (bytes, bytearray, memoryview)):
            try:
                f.seek(0)
            except (OSError, ValueError):
                pass
        try:
            result = reader(f)
        except Exception as exc:
            get_logger().warning(
                "read() failed for format %r (score %d): %s: %s",
                hit.format,
                hit.raw_score,
                exc.__class__.__name__,
                exc,
            )
            if first_error is None:
                first_error = exc
            continue
        if result is None:
            get_logger().debug(
                "format %r claimed %r at probe but read() declined",
                hit.format,
                hit.ext,
            )
            continue
        if isinstance(result, ReadResult) and result.format:
            return result
        return as_read_result(
            result, format=hit.format, ext=hit.ext, category=hit.category
        )
    if first_error is not None:
        raise first_error
    if hits:
        top = hits[0]
        if getattr(_entries[top.format].module, "read", None) is None:
            raise ProcessorNoReader(top.format, top.ext, top.raw_score)
    return None


def convert(canonical, target: str, *, format: str):
    """Convert a canonical object to one of the format's declared targets.

    ``format`` is mandatory: canonical objects do not self-describe, so the
    facade cannot know which module's ``convert`` to call.  When the format
    declares ``TARGETS`` the target is pre-checked against them
    (:class:`UnknownTarget`); formats without ``TARGETS`` (canonical already
    saveable, or probe-only) are not pre-checked -- their ``convert`` raises
    on its own.  Failures always raise (the canonical already came from this
    format -- there is no "not mine" branch).
    """
    entry = _entry_for(format)
    converter = getattr(entry.module, "convert", None)
    if converter is None:
        raise ProcessorError(
            f"format {format!r} provides no convert() (canonical type "
            f"{module_canonical_type(entry.module)!r}); its canonical object "
            f"is the final product"
        )
    declared = getattr(entry.module, "TARGETS", None)
    if declared:
        names = target_names(declared)
        if target not in names:
            raise UnknownTarget(format, target, names)
    return converter(canonical, target)


def convert_source(f: FileLike, format: str, target: str):
    """One-shot ``read`` + ``convert``; all three arguments are required."""
    result = read(f, format=format)
    assert result is not None  # read() in forced mode raises instead
    return convert(result.data, target, format=format)


def guess(f: FileLike) -> list[ProbeHit]:
    """Probe-only candidate list (never calls ``read``).

    Consumers: the content-signature gate and the failure-bucket extension
    guess.  Every registered format scores the header and the sorted hits
    come back, best first.
    """
    return _sorted_hits(f)


def info(format: str) -> FormatInfo:
    """Static metadata for *format* -- no file, no probing.

    See :class:`FormatInfo` for the caller-facing contract.  Unknown
    *format* raises :class:`UnknownFormat`, matching :func:`convert`.
    """
    entry = _entry_for(format)
    module = entry.module
    return FormatInfo(
        format=format,
        canonical_type=module_canonical_type(module),
        targets=module_targets(module),
        can_read=callable(getattr(module, "read", None)),
        can_convert=callable(getattr(module, "convert", None)),
    )


# ----------------------------------------------------------- plugin control

def _register_module(
    module: ModuleType,
    *,
    path: str = "",
    override: bool = False,
) -> SourceInfo:
    """Validate and register a protocol module (internal primitive).

    Takes an already-built module object, no file IO.  :func:`load_plugin`
    is the public path (executes a file, then calls this); tests and
    programmatic integrators may call this directly.
    """
    fmt = validate_module(module, path)
    # Discovery must have run before we consult _entries, otherwise a plugin
    # registered as the very first call would see an empty registry, skip
    # the collision check, and later be clobbered by lazy discovery.
    ensure_loaded()
    stored_path = path or _MEMORY_PATH
    with _lock:
        existing = _entries.get(fmt)
        if (
            existing is not None
            and path
            and existing.kind == _KIND_PLUGIN
            and existing.path == stored_path
        ):
            # The *same file* again is a reload, not a collision: swap the
            # module in place, keep its shadow.
            _entries[fmt] = _Entry(module, _KIND_PLUGIN, stored_path, existing.shadowed)
            return _source_info(fmt)
        if existing is not None and not override:
            raise PluginError(
                f"format {fmt!r} is already registered from "
                f"{existing.path!r}; load with override to replace it",
                path,
            )
        if existing is None or not override:
            shadow = None
        elif existing.kind == _KIND_PLUGIN:
            # Overriding a plugin replaces it: the newcomer inherits the
            # old plugin's shadow, so unload restores what the old plugin
            # shadowed (e.g. the built-in), not the old plugin itself.
            shadow = existing.shadowed
        else:
            # Overriding a built-in: shadow it so unload brings it back.
            shadow = existing
        _entries[fmt] = _Entry(module, _KIND_PLUGIN, stored_path, shadow)
    return _source_info(fmt)


def load_plugin(path: str | Path, *, override: bool = False) -> SourceInfo:
    """Explicitly load one plugin file (the UI/CLI entry point).

    The file is executed fresh (never via ``sys.modules``), validated against
    the protocol, and registered.  With ``override=True`` it shadows an
    existing registration of the same FORMAT; :func:`unload_plugin` restores
    the shadowed entry.

    Raises:
        PluginError: unreadable file, import-time exception, protocol
            violation, or a FORMAT collision without ``override``.
    """
    resolved = Path(path).resolve()
    module = _plugins.exec_module_from_path(resolved)
    # Store the resolved path so the scan dedup recognises this file even
    # when it is reached through a relative env dir or a relative argument.
    return _register_module(module, path=str(resolved), override=override)


def unload_plugin(format_name: str) -> bool:
    """Remove a plugin registration; restore anything it shadowed.

    Returns True when a plugin was removed.  Built-ins cannot be unloaded.
    The dropped module is GC'd (never in ``sys.modules``).
    """
    with _lock:
        entry = _entries.get(format_name)
        if entry is None or entry.kind != _KIND_PLUGIN:
            return False
        if entry.shadowed is not None:
            _entries[format_name] = entry.shadowed
        else:
            del _entries[format_name]
    return True


def reload_environment() -> list[SourceInfo]:
    """Re-scan the environment plugin directories (UI refresh button).

    Same rules as the first-time scan (see :func:`_scan_environment`).
    Files that disappeared are *not* evicted: an open viewer may still
    hold their canonical objects -- unload explicitly instead.
    """
    ensure_loaded()  # first call also performs the initial scan
    _scan_environment()
    return list_sources()


def list_sources() -> list[SourceInfo]:
    """Every registration, shadowed originals included (inactive)."""
    ensure_loaded()
    out: list[SourceInfo] = []
    for fmt, entry in _entries.items():
        out.append(_source_info(fmt, entry))
        walk = entry.shadowed
        while walk is not None:
            out.append(_source_info(fmt, walk, active=False))
            walk = walk.shadowed
    out.sort(key=lambda s: (s.kind != _KIND_BUILTIN, s.format))
    return out


def _source_info(fmt: str, entry: _Entry | None = None, active: bool = True) -> SourceInfo:
    """Build a :class:`SourceInfo`.  Entries living in ``_entries`` are always
    the active registration; shadowed predecessors are passed explicitly with
    ``active=False``."""
    if entry is None:
        entry = _entries[fmt]
    return SourceInfo(
        format=fmt,
        kind=entry.kind,
        path=entry.path,
        active=active,
        can_read=callable(getattr(entry.module, "read", None)),
        can_convert=callable(getattr(entry.module, "convert", None)),
        targets=module_targets(entry.module),
    )


def _reset_registry() -> None:
    """Drop all state (built-in discovery + plugins).  For tests only."""
    global _discovered
    with _lock:
        _entries.clear()
        _discovered = False
