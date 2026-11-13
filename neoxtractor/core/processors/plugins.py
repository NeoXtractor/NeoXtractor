"""External plugin channel for the v2 processors registry.

A plugin is a single importable file (``.py``/``.pyc``/``.pyd``/``.so``/
``.dylib``) that satisfies the same protocol as the built-in entries under
``core/resources/`` (``FORMAT`` + ``probe`` returning a
:class:`~neoxtractor.core.utils.result.ProbeResult`, optional
``read``/``convert``/``TARGETS``).  There is no plugin base class.

Loading discipline:

- Plugin modules are executed **fresh on every load** via
  ``importlib.util.spec_from_file_location`` and are **never inserted into
  ``sys.modules``**; the registry keeps the only reference, so unloading
  drops the module and a later reload re-reads the file from disk.
  Third-party libraries imported *by* a plugin do land in ``sys.modules``
  normally.
- The OS keeps a loaded ``.pyd``/``.so``/``.dylib`` mapped for the life of
  the process and cannot truly unload it: re-reading on reload holds only
  for source/bytecode; swapping a compiled plugin requires a restart.
- Classes defined in a plugin are not importable/picklable by name from
  outside the module object.

Discovery directories (first match wins on dedup):

1. ``$NEOXTRACTOR_PLUGIN_DIR`` (explicit user override)
2. ``plugins/format_processors`` beside the program directory (see
   :func:`candidate_dirs`).
"""

from __future__ import annotations

import importlib
import importlib.machinery
import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType

from neoxtractor.core.processors.errors import PluginError
from neoxtractor.core.utils.app import get_application_path

__all__ = [
    "ENV_VAR",
    "candidate_dirs",
    "exec_module_from_path",
    "importable_suffixes",
    "is_importable",
    "iter_plugin_files",
    "scan_plugin_dirs",
]

ENV_VAR = "NEOXTRACTOR_PLUGIN_DIR"
_SUBDIR = ("plugins", "format_processors")


def importable_suffixes() -> tuple[str, ...]:
    """Interpreter-loadable suffixes, longest first for correct stripping.

    Read live from :mod:`importlib.machinery` so ABI-tagged names
    (``.cp313-win_amd64.pyd``) and platform variants are honoured.  The
    longest-first order lets :func:`_module_name` strip the full
    ABI-tagged suffix instead of just ``.pyd``.
    """
    return tuple(
        sorted(importlib.machinery.all_suffixes(), key=len, reverse=True)
    )


def _module_name(path: Path) -> str:
    """Real module name = file name with its importable suffix stripped.

    A compiled extension must keep this name: the loader looks it up as
    ``PyInit_<name>``.  Source and bytecode use the same stripped name.
    """
    name = path.name
    for suffix in importable_suffixes():
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return path.stem


def is_importable(path: Path) -> bool:
    """True when *path* has a suffix the interpreter can load."""
    lowered = path.name.lower()
    return any(lowered.endswith(suffix) for suffix in importable_suffixes())


def iter_plugin_files(directory: Path) -> list[Path]:
    """Non-recursive, deterministic list of importable plugin candidates.

    Skips subdirectories (``__pycache__`` included), ``_``-prefixed files,
    and non-importable suffixes.  Deduplicates by module name so a stray
    ``foo.pyc`` beside ``foo.py`` is offered only once (first in sorted
    order wins).
    """
    by_name: dict[str, Path] = {}
    for entry in sorted(directory.iterdir()):
        if not entry.is_file():
            continue
        if entry.name.startswith("_"):
            continue
        if not is_importable(entry):
            continue
        by_name.setdefault(_module_name(entry), entry)
    return list(by_name.values())


def candidate_dirs() -> list[Path]:
    """Plugin search directories, in priority order (deduplicated).

    The program directory holds bundled parsers beside the user's own
    drops: the executable's folder when frozen, the resource root
    otherwise.  ``sys._MEIPASS`` is never searched (temporary directory,
    no user-facing plugin slot).
    """
    raw: list[Path] = []
    env = os.environ.get(ENV_VAR)
    if env:
        raw.append(Path(env))
    if getattr(sys, "frozen", False):
        program_dir = Path(sys.executable).resolve().parent
    else:
        program_dir = Path(get_application_path())
    raw.append(program_dir.joinpath(*_SUBDIR))

    unique: list[Path] = []
    seen: set[str] = set()
    for directory in raw:
        key = str(directory)
        if key in seen:
            continue
        seen.add(key)
        unique.append(directory)
    return unique


def exec_module_from_path(path: Path) -> ModuleType:
    """Execute *path* as a fresh module (never registered in sys.modules).

    Raises:
        PluginError: missing file, unloadable spec, or an exception while
            executing the module body (import-time errors are reported with
            the original class and message so the UI can show them verbatim).
    """
    path = Path(path)
    if not path.is_file():
        raise PluginError("file not found", str(path))
    if not is_importable(path):
        raise PluginError(
            "not an importable file type (expected one of: "
            + ", ".join(importable_suffixes())
            + ")",
            str(path),
        )
    # Same-second edits can share an mtime; make the path finder re-stat.
    importlib.invalidate_caches()
    # Real module name: a compiled extension must match PyInit_<name>.
    name = _module_name(path)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise PluginError("cannot build an import spec for this file", str(path))
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # plugin code is untrusted: report, don't crash
        raise PluginError(
            f"execution failed: {exc.__class__.__name__}: {exc}", str(path)
        ) from exc
    return module


def scan_plugin_dirs() -> list[tuple[Path, ModuleType | None, str | None]]:
    """Passive environment scan: exec every candidate plugin file.

    Returns one ``(path, module, error)`` triple per file; nothing raises --
    the registry logs failures and skips them (only the explicit
    ``load_plugin`` path surfaces :class:`PluginError` to the caller).
    """
    found: list[tuple[Path, ModuleType | None, str | None]] = []
    seen: set[str] = set()
    for directory in candidate_dirs():
        if not directory.is_dir():
            continue
        for path in iter_plugin_files(directory):
            key = str(path.resolve())
            if key in seen:
                continue
            seen.add(key)
            try:
                found.append((path, exec_module_from_path(path), None))
            except PluginError as exc:
                found.append((path, None, exc.reason))
    return found
