"""slot_file location and cached directory indexing."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from core.logger import get_logger
from core.npk.class_types import NPKEntry


@dataclass
class SlotDirectoryIndex:
    """Cached lookup tables for one slot_file directory."""

    exact_name: dict[str, Path] = field(default_factory=dict)
    exact_stem: dict[str, list[Path]] = field(default_factory=dict)
    ordered_files: list[Path] = field(default_factory=list)


class SlotFileResolver:
    """Resolve IDX slot_file entries against a directory on disk."""

    def __init__(self, reader):
        self.reader = reader
        self._dir_index_cache: dict[Path, SlotDirectoryIndex] = {}

    def read_slot_file_data(self, entry: NPKEntry) -> bytes | None:
        # Entries merged from the other (base / hot-update) IDX keep their own
        # directory, so their slot files must be resolved next to that package.
        slot_dir = self.reader._get_slot_file_dir(
            getattr(entry, "_alt_dir", None)
        )
        if not slot_dir.is_dir():
            return None

        dir_index = self._get_dir_index(slot_dir)
        raw_hash_hex = (
            getattr(entry, "raw_hash_hex", "") or entry.filename.split(".", 1)[0]
        )

        candidate_paths: list[Path] = []
        for key in (raw_hash_hex, entry.filename):
            path = dir_index.exact_name.get(key)
            if path is not None:
                candidate_paths.append(path)

        candidate_paths.extend(dir_index.exact_stem.get(raw_hash_hex, []))

        seen = set()
        for candidate in candidate_paths:
            candidate_str = str(candidate)
            if candidate_str in seen:
                continue
            seen.add(candidate_str)
            if candidate.is_file():
                get_logger().debug(
                    "Loading slot_file from %s for pkg_id=%s name=%s",
                    candidate,
                    getattr(entry, "pkg_id", None),
                    raw_hash_hex,
                )
                return candidate.read_bytes()

        for path in dir_index.ordered_files:
            if path.stem.startswith(raw_hash_hex):
                get_logger().debug(
                    "Loading slot_file by prefix from %s for pkg_id=%s name=%s",
                    path,
                    getattr(entry, "pkg_id", None),
                    raw_hash_hex,
                )
                return path.read_bytes()

        return None

    def _get_dir_index(self, slot_dir: Path) -> SlotDirectoryIndex:
        cached = self._dir_index_cache.get(slot_dir)
        if cached is not None:
            return cached

        exact_name: dict[str, Path] = {}
        exact_stem: dict[str, list[Path]] = {}
        ordered_files: list[Path] = []

        for path in sorted(slot_dir.iterdir()):
            if not path.is_file():
                continue
            ordered_files.append(path)
            exact_name.setdefault(path.name, path)
            exact_stem.setdefault(path.stem, []).append(path)

        dir_index = SlotDirectoryIndex(
            exact_name=exact_name,
            exact_stem=exact_stem,
            ordered_files=ordered_files,
        )
        self._dir_index_cache[slot_dir] = dir_index
        return dir_index
