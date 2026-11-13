"""Entry extraction helpers: filename sanitizing and the save dialogs.

The byte source is always the archive: ``decoded=True`` writes what
``get_res`` decodes, ``decoded=False`` writes the stored bytes.  The pure
functions (:func:`sanitize_filename`, :func:`safe_relpath`,
:func:`write_entry`) stay dialog-free so they are testable headless; the two
``export_*`` wrappers own the Qt file dialogs and the user report.

A single save gets one flat name (:func:`sanitize_filename`); a batch keeps
the directory structure carried by entry names (:func:`safe_relpath`) so
same-named entries across directories do not collide.
"""

from __future__ import annotations

import os
import re

from PySide6 import QtWidgets

from neoxtractor.core.packages.base import Archive
from neoxtractor.core.utils.logger import get_logger

# Windows-reserved characters plus control codes; each function applies
# them at its own granularity (see their docstrings).
_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize_filename(name: str) -> str:
    """A single safe flat filename from a display path (never empty).

    Directory prefixes are dropped: one leaf name, for the save dialog
    where the user picks the destination.
    """
    base = os.path.basename(name.replace("\\", "/"))
    base = _INVALID.sub("_", base).strip(" .")
    return base or "unnamed"


def safe_relpath(name: str) -> str:
    r"""A sanitized relative path that keeps the directory structure.

    Both ``\`` and ``/`` separate segments (name tables mix them).  Every
    segment loses reserved characters and edge dots, and empty/``.``/``..``
    segments are dropped, so the result is always a plain relative path
    that cannot escape its destination directory (or start as absolute:
    drive letters lose their colon to the sweep).  Never empty.
    """
    parts: list[str] = []
    for part in name.replace("\\", "/").split("/"):
        if part in ("", ".", ".."):
            continue
        cleaned = _INVALID.sub("_", part).strip(" .")
        if cleaned:
            parts.append(cleaned)
    return os.path.join(*parts) if parts else "unnamed"


def write_entry(
    archive: Archive, key: int, dest_path: str, decoded: bool = True
) -> int:
    """Write one entry's bytes to *dest_path*; returns the byte count."""
    with archive.get_res(key, decoded=decoded) as stream:
        data = stream.read()
    with open(dest_path, "wb") as handle:
        handle.write(data)
    return len(data)


def unique_in(dir_path: str, rel: str, used: set[str]) -> str:
    """First free relative path under dir_path (collision with disk or batch).

    Uniqueness applies inside the file's own directory: a clash renames
    only the final segment (``stem_N.ext``) and leaves the leading
    directories untouched, so same-named files in different directories
    both keep their place.  The *used* set carries the batch's claimed
    names (normcase keys); the caller owns its lifetime.
    """
    head, tail = os.path.split(rel)
    stem, ext = os.path.splitext(tail)
    candidate = rel
    key = os.path.normcase(candidate)
    index = 1
    while key in used or os.path.exists(os.path.join(dir_path, candidate)):
        tail = f"{stem}_{index}{ext}"
        candidate = os.path.join(head, tail) if head else tail
        key = os.path.normcase(candidate)
        index += 1
    used.add(key)
    return candidate


def unique_name(dir_path: str, name: str, used: set[str]) -> str:
    """First free flat file name in *dir_path* (one batch, no directories).

    The single-directory counterpart of :func:`unique_in`, for callers
    whose items all land in one folder (e.g. config exports).
    """
    return unique_in(dir_path, sanitize_filename(name), used)


def export_single(
    parent: QtWidgets.QWidget,
    archive: Archive,
    key: int,
    name: str,
    decoded: bool,
) -> None:
    """Save-dialog extraction of one entry."""
    log = get_logger()
    suggested = sanitize_filename(name)
    path, _ = QtWidgets.QFileDialog.getSaveFileName(
        parent, "Extract File", suggested, "All Files (*)"
    )
    if not path:
        return
    try:
        count = write_entry(archive, key, path, decoded)
    except Exception as exc:
        log.error("export failed for %r: %s", name, exc)
        QtWidgets.QMessageBox.critical(
            parent, "Error", f"Failed to extract {name!r}: {exc}"
        )
    else:
        log.info("exported %r -> %s (%d bytes)", name, path, count)
        QtWidgets.QMessageBox.information(
            parent, "Success", f"Extracted {count} bytes to {path}"
        )


def export_many(
    parent: QtWidgets.QWidget,
    archive: Archive,
    pairs: list[tuple[int, str]],
    decoded: bool,
) -> None:
    """Directory-dialog extraction of several entries (key, name) pairs.

    Entry names keep their directory structure under the chosen root
    (:func:`safe_relpath`); missing parent folders are created on demand.
    """
    dir_path = QtWidgets.QFileDialog.getExistingDirectory(
        parent,
        "Select Directory to Extract Files",
        "",
        QtWidgets.QFileDialog.Option.ShowDirsOnly,
    )
    if not dir_path:
        return
    log = get_logger()
    used: set[str] = set()
    success = 0
    failures: list[str] = []
    for key, name in pairs:
        rel = unique_in(dir_path, safe_relpath(name), used)
        target = os.path.join(dir_path, rel)
        try:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            write_entry(archive, key, target, decoded)
        except Exception as exc:
            log.error("export failed for %r: %s", name, exc)
            failures.append(f"{name}: {exc}")
        else:
            success += 1
            if os.path.normcase(rel) == os.path.normcase(name):
                log.debug("exported %r -> %s", name, target)
            else:
                # The landed name differs (sanitized or de-collided): an
                # audit line mapping the original entry name to disk.
                log.info("exported %r -> %s (renamed)", name, target)
    message = f"Extracted {success} files to {dir_path}"
    log.info("batch export finished: %d file(s) to %s", success, dir_path)
    if failures:
        message += "\n" + "\n".join(failures)
    if failures:
        QtWidgets.QMessageBox.warning(
            parent, "Extraction Complete", message
        )
    else:
        QtWidgets.QMessageBox.information(
            parent, "Extraction Complete", message
        )
