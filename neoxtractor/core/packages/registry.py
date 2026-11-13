"""Container discovery, registration, and the on-disk PackageFile.

Discovery enumerates the first-level subpackages of ``neoxtractor.core.packages``
(each a container directory holding an ``interface.py``) via ``pkgutil``, so
frozen builds work.  A directory is a candidate only when it is a real package
(has ``__init__.py``) and its name does not start with ``_``; the neutral
vocabulary modules (base/errors/protocol) are plain ``.py`` files and are
skipped automatically.  A candidate whose
``interface`` lacks ``FORMAT`` is skipped silently (a placeholder); one that
declares ``FORMAT`` but fails :func:`validate_module` is logged and skipped, so
misconfiguration surfaces as a warning instead of vanishing.

Registration keys everything by ``FORMAT``.  ``FORMAT`` must equal the directory
name (enforced in validate), so the directory is the identity and a duplicate
across directories is a bug: first wins, later duplicates warn.

:class:`LocalPackageFile` is the concrete :class:`~base.PackageFile` for files
already on disk; it is the only IO grant a module ever receives.
"""

from __future__ import annotations

import fnmatch
import glob as _glob
import importlib
import os
import pkgutil
import threading
from collections import deque
from types import ModuleType
from typing import Callable, NamedTuple, Protocol

from neoxtractor.core.packages.base import (
    Accepted,
    Archive,
    Claim,
    CompanionState,
    PackageFile,
    ParentPref,
    ReadOptions,
    Unit,
    Want,
)
from neoxtractor.core.packages.errors import (
    NoPackageReader,
    OperationCancelled,
    OptionsTypeMismatch,
    PackageFormatMismatch,
    ProtocolError,
    UnknownPackageFormat,
)
from neoxtractor.core.packages.protocol import (
    is_container_module,
    validate_claim,
    validate_module,
)
from neoxtractor.core.utils.filelike import Seekable
from neoxtractor.core.utils.logger import get_logger

__all__ = [
    "ContainerEntry",
    "LocalPackageFile",
    "PackageResult",
    "PackageSourceInfo",
    "Progress",
    "ReadAsk",
    "ensure_loaded",
    "list_sources",
    "lookup",
    "detect_format",
    "open_file",
    "ordered_modules",
]

_PACKAGES_PKG = "neoxtractor.core.packages"


class ContainerEntry(NamedTuple):
    """One registered container module and its declared identity."""

    module: ModuleType
    format: str
    priority: int
    options_type: type[ReadOptions]
    module_path: str          # dotted import path, stable under frozen builds


class PackageSourceInfo(NamedTuple):
    """A container module described for UI display."""

    format: str
    priority: int
    kind: str                    # always "builtin"
    path: str
    active: bool


_lock = threading.RLock()
_entries: dict[str, ContainerEntry] = {}
_discovered = False


# ---------------------------------------------------------------- discovery

def _discover_builtins() -> None:
    """Import each container ``interface`` once and register valid modules.

    Candidate rules are in the module docstring.
    """
    global _discovered
    package = importlib.import_module(_PACKAGES_PKG)
    seen: dict[str, str] = {}
    for modinfo in pkgutil.iter_modules(package.__path__):
        if not modinfo.ispkg or modinfo.name.startswith("_"):
            continue
        full = f"{_PACKAGES_PKG}.{modinfo.name}.interface"
        try:
            module = importlib.import_module(full)
        except ModuleNotFoundError:
            # No interface.py in the directory: a placeholder, skipped quietly.
            continue
        except Exception as exc:
            get_logger().warning("Skipping container %s: %s", full, exc)
            continue
        if not is_container_module(module):
            continue
        try:
            fmt = validate_module(module, modinfo.name, full)
        except ProtocolError as exc:
            get_logger().warning("Invalid container %s: %s", full, exc)
            continue
        if fmt in seen:
            get_logger().warning(
                "Duplicate FORMAT %r from %s ignored (kept %s)",
                fmt, full, seen[fmt],
            )
            continue
        seen[fmt] = full
        if fmt in _entries:
            get_logger().warning(
                "Container FORMAT %r from %s not registered (slot held by %s)",
                fmt, full, _entries[fmt].module_path,
            )
            continue
        _entries[fmt] = ContainerEntry(
            module=module,
            format=fmt,
            priority=module.PRIORITY,
            options_type=module.OPTIONS_TYPE,
            module_path=full,
        )


def ensure_loaded() -> None:
    """Run discovery once; safe to call from every public entry point."""
    global _discovered
    with _lock:
        if not _discovered:
            _discover_builtins()
            _discovered = True


def reset_registry() -> None:
    """Clear all registrations (test support only)."""
    global _discovered
    with _lock:
        _entries.clear()
        _discovered = False


def lookup(format_name: str) -> ContainerEntry | None:
    """The registered module for a FORMAT name, or ``None``."""
    ensure_loaded()
    return _entries.get(format_name)


def ordered_modules() -> list[ContainerEntry]:
    """All containers in ask order: PRIORITY desc, ties by FORMAT asc.

    Every scan loop iterates this single ordering.
    """
    ensure_loaded()
    return sorted(_entries.values(), key=lambda e: (-e.priority, e.format))


def list_sources() -> list[PackageSourceInfo]:
    """Describe every container module for UI display."""
    ensure_loaded()
    return [
        PackageSourceInfo(
            format=e.format,
            priority=e.priority,
            kind="builtin",
            path=e.module_path,
            active=True,
        )
        for e in ordered_modules()
    ]


# ---------------------------------------------------------------- PackageFile

class LocalPackageFile(PackageFile):
    """A real on-disk file the driver has authorised a module to read.

    ``path`` is absolute and normcased.  ``read_head`` grows one cached prefix
    so the same file probed by many modules reads from disk at most once per
    high-water mark; ``open`` hands the caller a fresh ``Seekable`` it owns and
    must close.  No verb takes a path, so this object is the sole IO grant.

    Thread safety: the head/size caches are mutable instance state, so the
    cache-growing verbs hold a per-instance lock; ``open()`` needs none --
    every call returns an independent handle with its own position.
    """

    def __init__(self, path: str) -> None:
        self.path = os.path.normcase(os.path.abspath(path))
        self._lock = threading.Lock()
        self._head: bytes = b""
        self._size: int | None = None

    def read_head(self, n: int) -> bytes:
        with self._lock:
            if n > len(self._head):
                with open(self.path, "rb") as stream:
                    stream.seek(len(self._head))
                    self._head += stream.read(n - len(self._head))
            return self._head[:n]

    def size(self) -> int:
        with self._lock:
            if self._size is None:
                self._size = os.path.getsize(self.path)
            return self._size

    def open(self) -> Seekable:
        return open(self.path, "rb")


# ---------------------------------------------------------------- facade types

class PackageResult(NamedTuple):
    """A loaded unit plus its neutral summary."""

    archive: Archive
    format: str
    entry: str
    members: tuple[str, ...]   # the single source of truth for absorption
    warnings: tuple[str, ...]


class ReadAsk(Protocol):
    """Driver-side consent hook; never reachable from matches()."""

    def __call__(self, path: str, reason: str) -> bool: ...
    # reason: "outside_anchor"


class Progress(Protocol):
    """Driver-side progress hook; plain callable, core never imports Qt.

    ``cancel`` is a zero-arg callable the driver hands to the UI; invoking
    it signals cancellation and the driver stops at the next safe point
    raising OperationCancelled.  emit's return value is ignored.
    """

    def emit(
        self, stage: str, done: int, total: int, cancel: Callable[[], None]
    ) -> None: ...


# ---------------------------------------------------------------- scan helpers

def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


class _CancelFlag:
    """The cancel callable handed to Progress.emit."""

    def __init__(self) -> None:
        self.cancelled = False

    def __call__(self) -> None:
        self.cancelled = True


def _check_cancel(flag: _CancelFlag, stage: str) -> None:
    if flag.cancelled:
        raise OperationCancelled(stage)


def _safe_ask(
    ask: ReadAsk | None, path: str, reason: str
) -> bool:
    """Invoke the consent hook without letting a callback fault the scan.

    A missing hook denies (the safe default); a hook that raises denies this
    path and is logged.
    """
    if ask is None:
        return False
    try:
        return bool(ask(path, reason))
    except Exception as exc:
        get_logger().warning("ask(%s) raised %s: %s", path, type(exc).__name__, exc)
        return False


def _safe_emit(
    progress: Progress | None,
    stage: str,
    done: int,
    total: int,
    cancel: Callable[[], None],
) -> None:
    """Report progress without letting a callback fault the scan.

    A hook that raises is logged and ignored: reporting is informational and
    must never abort the operation it observes.
    """
    if progress is None:
        return
    try:
        progress.emit(stage, done, total, cancel)
    except Exception as exc:
        get_logger().warning(
            "progress.emit(%s) raised %s: %s",
            stage, type(exc).__name__, exc,
        )


def _resolve_want(
    want: Want,
    entry_path: str,
    anchor: str,
    pool: dict[str, PackageFile],
    ask: ReadAsk | None,
    denied: set[str],
    report: "Callable[[int], None] | None" = None,
) -> int:
    """Expand one want glob relative to the entry's directory.

    A hit outside the anchor (the seed's own directory) crosses the consent
    boundary and routes through ask -- default deny, cached per path.  Only
    plain files enter the pool; glob-matched directories are skipped.
    Returns how many files the pool gained.

    While candidates await consent, *report* fires before each ask with the
    count still undecided (the file being asked included); a pass that asked
    but gained nothing ends with report(0) to drop the pending count (a pass
    that gained files is covered by the caller's growth report).
    """
    directory = os.path.dirname(entry_path)
    matches = _glob.glob(os.path.join(directory, want.rel), recursive=False)
    eligible: list[tuple[str, bool]] = []
    for raw in matches:
        path = _norm(raw)
        if not os.path.isfile(path):
            continue
        if want.pattern is not None and not want.pattern.fullmatch(
            os.path.basename(path)
        ):
            continue
        if path in pool or path in denied:
            continue
        eligible.append((path, not path.startswith(anchor + os.sep)))
    undecided = sum(1 for _path, above in eligible if above)
    added = 0
    asked = 0
    for path, above in eligible:
        if above:
            if report is not None:
                report(undecided - asked)
            if not _safe_ask(ask, path, "outside_anchor"):
                denied.add(path)
                asked += 1
                continue
            asked += 1
        pool[path] = LocalPackageFile(path)
        added += 1
        if added >= want.max_files:
            break
    if asked and added == 0 and report is not None:
        report(0)
    return added


def _ask_file(
    path: str,
    file_obj: PackageFile,
    entry: ContainerEntry,
    pool: dict[str, PackageFile],
    anchor: str,
    ask: ReadAsk | None,
    denied: set[str],
    accepted: dict[str, Accepted],
    claimed: set[str],
    report: "Callable[[int], None] | None" = None,
) -> None:
    """Ask one module about one unclaimed file; accept its claim if any.

    The caller guarantees ``path`` is not yet claimed -- the pass loop is the
    single enforcement point.  A rejected companion stays available for other
    modules.  Any exception from the module is a module bug: log and skip
    this module for this file.
    """
    try:
        claim = entry.module.matches(file_obj)
    except ProtocolError as exc:
        get_logger().warning("matches(%s) in %s: %s", path, entry.format, exc)
        return
    except Exception as exc:  # a buggy module must not sink the scan
        get_logger().warning(
            "matches(%s) in %s raised %s: %s",
            path, entry.format, type(exc).__name__, exc,
        )
        return
    if claim is None:
        return
    try:
        validate_claim(claim, entry.format, path)
    except ProtocolError as exc:
        get_logger().warning("Rejecting bad claim on %s: %s", path, exc)
        return
    _accept(claim, entry, pool, anchor, ask, denied, accepted, claimed, report)


def _fold_companions(
    claim: Claim,
    entry: ContainerEntry,
    pool: dict[str, PackageFile],
    claimed: set[str],
) -> tuple[tuple[PackageFile, ...], dict[str, CompanionState],
           list[tuple[str, Want]]]:
    """Close the companion set under the module's own matches() verdict.

    Declared siblings are offered to the same module: a pass folds the file
    in and adds its claim's companions to the closure; absent, declined, or
    already-claimed siblings land in companion_states and stay out of
    consumed.  The caller has already claimed the entry path; this function
    claims only what it folds.  Same-directory siblings are never
    consent-gated.

    Membership is all this function decides: it returns the consumed tuple,
    the per-companion states, and every want the unit declared, paired with
    the declaring file, for the caller to record and expand.  A TAKEN or
    REJECTED sibling's wants are excluded; they surface when that file is
    claimed on its own.
    """
    states: dict[str, CompanionState] = {}
    entry_file = pool[claim.entry]
    consumed: list[PackageFile] = [entry_file]
    wants: list[tuple[str, Want]] = [(claim.entry, w) for w in claim.wants]
    queue = deque(claim.companions)
    while queue:
        raw = queue.popleft()
        cpath = _norm(raw)
        if cpath in states or cpath == claim.entry:
            continue
        if cpath in claimed:
            states[cpath] = CompanionState.TAKEN
            continue
        if not os.path.isfile(cpath):
            states[cpath] = CompanionState.ABSENT
            continue
        cfile = pool.get(cpath) or LocalPackageFile(cpath)
        try:
            cclaim = entry.module.matches(cfile)
        except Exception as exc:
            get_logger().warning(
                "companion matches(%s) in %s raised: %s",
                cpath, entry.format, exc,
            )
            cclaim = None
        if cclaim is None:
            states[cpath] = CompanionState.REJECTED
            continue
        try:
            validate_claim(cclaim, entry.format, cpath)
        except ProtocolError as exc:
            get_logger().warning("Rejecting bad companion claim %s: %s", cpath, exc)
            states[cpath] = CompanionState.REJECTED
            continue
        states[cpath] = CompanionState.FOLDED
        consumed.append(cfile)
        claimed.add(cpath)
        pool[cpath] = cfile
        wants.extend((cclaim.entry, w) for w in cclaim.wants)
        queue.extend(cclaim.companions)
    return tuple(consumed), states, wants


def _accept(
    claim: Claim,
    entry: ContainerEntry,
    pool: dict[str, PackageFile],
    anchor: str,
    ask: ReadAsk | None,
    denied: set[str],
    accepted: dict[str, Accepted],
    claimed: set[str],
    report: "Callable[[int], None] | None" = None,
) -> Accepted:
    """Turn a validated claim into an Accepted and grow the pool.

    The driver owns the verdict: the fold decides membership, this function
    claims the entry, records the Accepted, and expands the unit's wants as
    paired by :meth:`_fold_companions`, so the footprint is the same
    whichever member was the seed.  A report fires *before* wants expand --
    a consent dialog then always sees a current progress bar -- and a second
    on pool growth, so the UI sees the footprint the moment it changes
    instead of waiting for a pass boundary.
    """
    before = len(pool)
    claimed.add(claim.entry)
    consumed, states, wants = _fold_companions(claim, entry, pool, claimed)
    notes = tuple(
        f"companion {path}: {state.value}"
        for path, state in sorted(states.items())
        if state is not CompanionState.FOLDED
    )
    acc = Accepted(
        claim=claim,
        consumed=consumed,
        companion_states=states,
        notes=notes,
    )
    accepted[claim.entry] = acc
    if report is not None:
        report(0)  # current state visible before any ask popup
    for base, want in dict.fromkeys(wants):   # dedup, keep declaration order
        _resolve_want(want, base, anchor, pool, ask, denied, report)
    if report is not None and len(pool) > before:
        report(0)
    return acc


def _attach(accepted: dict[str, Accepted]) -> list[Unit]:
    """Group Accepted records into Units by each child's parents order.

    A child attaches to the highest-priority claim whose entry matches one
    of its parent globs (fnmatch against absolute paths) with a matching
    role.  Attachment is transitive -- a child of a child joins the root --
    so units stay two-level.  A child naming no live parent stands alone.
    """
    parent_of: dict[str, str] = {}
    for path, acc in accepted.items():
        for pref in acc.claim.parents:
            target = _match_parent(pref, path, accepted)
            if target is not None and target != path:
                parent_of[path] = target
                break

    def root_of(path: str) -> str:
        seen = {path}
        while path in parent_of:
            nxt = parent_of[path]
            if nxt in seen:  # defensive: a cycle would mean an attach bug
                break
            seen.add(nxt)
            path = nxt
        return path

    roots: dict[str, list[str]] = {}
    for path in accepted:
        roots.setdefault(root_of(path), []).append(path)
    units: list[Unit] = []
    for root_path, members in sorted(roots.items()):
        children = tuple(
            accepted[m] for m in sorted(members) if m != root_path
        )
        units.append(Unit(root=accepted[root_path], children=children))
    return units


def _match_parent(
    pref: ParentPref, child_path: str, accepted: dict[str, Accepted]
) -> str | None:
    """The accepted entry matching one parent preference, or None."""
    directory = os.path.dirname(child_path)
    pattern = _norm(os.path.join(directory, pref.rel))
    for path, acc in accepted.items():
        if acc.claim.role == pref.role and _fnmatch(path, pattern):
            return path
    return None


def _fnmatch(path: str, pattern: str) -> bool:
    return fnmatch.fnmatch(path, pattern)


# ---------------------------------------------------------------- facade

def detect_format(path: str) -> str | None:
    """Answer which registered format claims this file, without loading.

    Modules are tried in PRIORITY order; the first claim passing
    :func:`validate_claim` names the format.  A module whose ``matches``
    raises is logged and skipped, like the seed loop.  Returns ``None``
    when nothing claims the file (it may still be a companion); nothing is
    read past what a ``matches`` implementation chooses to inspect.
    """
    ensure_loaded()
    norm = _norm(path)
    if not os.path.isfile(norm):
        return None
    file_obj = LocalPackageFile(norm)
    for entry in ordered_modules():
        try:
            claim = entry.module.matches(file_obj)
        except Exception as exc:
            get_logger().warning(
                "matches(%s) in %s raised: %s", norm, entry.format, exc
            )
            continue
        if claim is None:
            continue
        try:
            validate_claim(claim, entry.format, norm)
        except ProtocolError as exc:
            get_logger().warning("Rejecting bad claim on %s: %s", norm, exc)
            continue
        return entry.format
    return None


def open_file(
    path: str,
    *,
    ask: ReadAsk | None = None,
    format: str | None = None,
    options: ReadOptions | None = None,
    progress: Progress | None = None,
) -> PackageResult:
    """Single-file mode: seed claim, priority retirement, one pass per module.

    The seed is asked to modules in PRIORITY desc until claimed (all refuse:
    NoPackageReader; a forced format that refuses: PackageFormatMismatch).
    With winner priority P every module PRIORITY >= P is retired for the rest
    of the scan (no upward re-anchoring).  Remaining modules take one pass
    each in desc order over pool members not yet tested by them; claims'
    companions fold and wants expand the pool (above-anchor hits go through
    ask, default deny).  Then attach and load.

    ``format`` pins only the seed's identity: unit assembly is unchanged --
    lower-priority modules still claim the winner's wants and attach, so a
    forced idx still gathers its wpk parts and thd names.
    """
    ensure_loaded()
    norm = _norm(path)
    if not os.path.isfile(norm):
        raise NoPackageReader(norm, [])
    anchor_dir = os.path.dirname(norm)
    all_modules = ordered_modules()

    if format is not None:
        forced = lookup(format)
        if forced is None:
            raise UnknownPackageFormat(format, [m.format for m in all_modules])
        seed_modules = [forced]
    else:
        seed_modules = all_modules

    pool: dict[str, PackageFile] = {norm: LocalPackageFile(norm)}
    accepted: dict[str, Accepted] = {}
    denied: set[str] = set()
    claimed: set[str] = set()
    flag = _CancelFlag()

    def _report(pending: int = 0) -> None:
        # One progress line per footprint event: done tracks claimed units,
        # total = pool + undecided asks (files a consent popup may still
        # refuse) -- the bar holds while files are accepted and shrinks
        # only as files are refused (the live pool size revises upward as
        # wants pull companions in; the scan has no upfront total).
        _safe_emit(
            progress, "open_file", len(claimed),
            max(len(pool) + pending, 1), flag,
        )

    winner = None
    seed_file = pool[norm]
    for entry in seed_modules:
        _check_cancel(flag, "open_file:seed")
        try:
            claim = entry.module.matches(seed_file)
        except Exception as exc:
            get_logger().warning(
                "matches(%s) in %s raised: %s", norm, entry.format, exc
            )
            claim = None
        if claim is None:
            continue
        try:
            validate_claim(claim, entry.format, norm)
        except ProtocolError as exc:
            if format is not None:
                raise PackageFormatMismatch(format, norm) from exc
            get_logger().warning("Rejecting bad claim on %s: %s", norm, exc)
            continue
        winner = entry
        _accept(
            claim, entry, pool, anchor_dir, ask, denied,
            accepted, claimed, _report,
        )
        break

    if format is not None and winner is None:
        raise PackageFormatMismatch(format, norm)
    if winner is None:
        raise NoPackageReader(norm, [m.format for m in seed_modules])

    # One pass per remaining module over the pool snapshot taken at the
    # pass start; retirement and forced-mode rules are in the docstring.
    remaining = [m for m in all_modules if m.priority < winner.priority]
    for entry in remaining:
        _check_cancel(flag, f"open_file:pass:{entry.format}")
        for path2 in sorted(pool):
            if path2 in claimed:
                continue
            _ask_file(
                path2, pool[path2], entry, pool,
                anchor_dir, ask, denied, accepted, claimed, _report,
            )
        _report()  # pass boundary: fold any pending back into pool-only

    units = _attach(accepted)
    root_unit = next(
        (u for u in units if u.root.claim.entry == norm), None
    )
    if root_unit is None:  # the seed was folded as someone's companion
        root_unit = next(
            u for u in units
            if any(c.claim.entry == norm for c in (u.root, *u.children))
        )
    result = _load_unit(root_unit, options)
    return result


# ---------------------------------------------------------------- load core

def _load_unit(unit: Unit, options: ReadOptions | None) -> PackageResult:
    entry = lookup(unit.root.claim.format)
    assert entry is not None  # accepted units only reference registered formats
    if options is None:
        options = entry.options_type()
    elif not isinstance(options, entry.options_type):
        raise OptionsTypeMismatch(
            entry.format,
            entry.options_type.__name__,
            type(options).__name__,
        )
    archive: Archive = entry.module.load(unit, options)
    warnings = tuple(
        note
        for acc in (unit.root, *unit.children)
        for note in acc.notes
    )
    return PackageResult(
        archive=archive,
        format=entry.format,
        entry=unit.root.claim.entry,
        members=unit.all_paths,
        warnings=warnings,
    )

