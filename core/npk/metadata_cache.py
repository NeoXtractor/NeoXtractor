"""Saves what the Characters scan read from the packs' metadata, so rescanning unchanged packs is quicker.

The saved file is stamped with the packs' and resdb's path, size and modification time, and is rebuilt when
any of them change.
"""

from __future__ import annotations

import json
import os
import re
import threading
import zlib
from pathlib import Path

CACHE_VERSION = 1
_LOCK = threading.Lock()
_RES_FOLDER = re.compile(r"res\d*", re.IGNORECASE)


def _cache_file(opened: str) -> Path:
    try:
        from core.utils import get_application_path
        folder = Path(get_application_path()) / "scan_cache"
    except Exception:
        folder = Path(os.getcwd()) / "scan_cache"
    name = re.sub(r"[^A-Za-z0-9]+", "_", str(Path(opened).name)).strip("_") or "pack"
    return folder / f"metadata_{name}_{zlib.crc32(str(opened).lower().encode("utf-8")):08x}.json"


def _file_stamp(path: Path):
    stat = path.stat()
    return [str(path), stat.st_size, stat.st_mtime_ns]


def stamp(archive_set) -> list | None:
    """The packs of the set and the resdb manifest of each one's res folder; None when one can't be read."""
    files = []
    try:
        for archive in archive_set.archives:
            path = Path(str(archive.file_path))
            files.append(_file_stamp(path))
            res = next((p for p in path.parents if _RES_FOLDER.fullmatch(p.name)), None)
            if res is not None and (res / "resdb").is_file():
                files.append(_file_stamp(res / "resdb"))
    except (OSError, AttributeError):
        return None
    return sorted(map(list, {tuple(f) for f in files}))


def load(archive_set) -> int:
    """Put the saved references of named metadata entries into the set; how many were given."""
    current = stamp(archive_set)
    if current is None:
        return 0
    path = _cache_file(str(archive_set.file_path))
    try:
        with _LOCK:
            saved = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    if saved.get("version") != CACHE_VERSION or saved.get("stamp") != current:
        return 0
    from core.npk.metadata_references import MetadataReferences
    references = archive_set._metadata_references
    given = 0
    for index, (assets, folders) in saved.get("named", {}).items():
        index = int(index)
        if index not in references:
            references[index] = MetadataReferences(tuple(assets), tuple(folders))
            given += 1
    return given


def save(archive_set, named: set) -> None:
    """Write the references of the named metadata entries prepare read (or was given)."""
    current = stamp(archive_set)
    if current is None:
        return
    references = archive_set._metadata_references
    data = {"version": CACHE_VERSION, "stamp": current,
            "named": {str(i): [list(references[i].assets), list(references[i].folders)]
                      for i in sorted(named) if i in references}}
    path = _cache_file(str(archive_set.file_path))
    try:
        with _LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix(".tmp")
            temp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
            os.replace(temp, path)
    except OSError:
        pass


def install() -> bool:
    """Wrap RelatedArchiveSet.prepare (and _read, to learn which entries were named); False if already done."""
    from core.npk import related_archives
    cls = related_archives.RelatedArchiveSet
    if getattr(cls.prepare, "_metadata_cache", False):
        return False
    original_prepare, original_read = cls.prepare, cls._read

    def _read(self, index, *args, **kwargs):
        named = getattr(self, "_named_metadata", None)
        if named is not None and index not in self._metadata_references:
            entry = self.entries.get(index)
            if entry is not None and getattr(entry, "extension", ""):
                named.add(index)
        return original_read(self, index, *args, **kwargs)

    def prepare(self, *args, **kwargs):
        if getattr(self, "prepared", False):
            return original_prepare(self, *args, **kwargs)
        try:
            given = load(self)
        except Exception:
            given = 0
        self._named_metadata = set(self._metadata_references) if given else set()
        try:
            result = original_prepare(self, *args, **kwargs)
        except BaseException:
            self._named_metadata = None
            raise
        named, self._named_metadata = self._named_metadata, None
        if getattr(self, "prepared", False) and named:
            try:
                save(self, named)
            except Exception:
                pass
        return result

    prepare._metadata_cache = True
    cls.prepare, cls._read = prepare, _read
    return True
