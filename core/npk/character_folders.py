"""Discover character-family folders from decoded NeoX asset metadata.

NeoX NPK indices often contain only hashed filenames. The decoded metadata
inside material and model-description entries still contains paths such as
``model\\canon\\weisilai_qiaozhi\\qiaozhi.tga``. This module turns those
explicit path references into an evidence-backed report without guessing
ownership from meshes or executing game scripts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from core.npk.metadata_references import model_paths

DEFAULT_CHARACTER_FOLDER_ROOTS = ("model\\canon", "model\\partner")


def normalize_character_folder_roots(
    roots: Iterable[str] | None,
) -> tuple[str, ...]:
    """Return unique, normalized metadata roots in stable order."""
    values = DEFAULT_CHARACTER_FOLDER_ROOTS if roots is None else roots
    result = []
    seen = set()
    for root in values:
        normalized = str(root).strip().replace("/", "\\").rstrip("\\").lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return tuple(result)


@dataclass(frozen=True)
class CharacterFolderReference:
    """One explicit asset path found in one decoded NPK entry."""

    asset_path: str
    source_index: int
    source_signature: int
    source_name: str
    source_extension: str


@dataclass
class CharacterFolder:
    """A metadata-derived folder and all references supporting it."""

    root: str
    name: str
    references: list[CharacterFolderReference] = field(default_factory=list)


@dataclass
class CharacterFolderReport:
    """Result of scanning the entries currently loaded in an NPK file."""

    roots: tuple[str, ...]
    scanned_entries: int
    referenced_entries: int
    folders: list[CharacterFolder]


def _entry_source_name(entry: Any, index: int, npk_file: Any) -> str:
    loaded_name = getattr(entry, "filename", "")
    if loaded_name:
        return str(loaded_name)
    indices = getattr(npk_file, "indices", ())
    if 0 <= index < len(indices):
        return str(getattr(indices[index], "filename", ""))
    return f"entry_{index}"


def scan_character_folders(
    npk_file: Any,
    roots: Iterable[str] | None = None,
    *,
    progress=None,
    cancelled=None,
) -> CharacterFolderReport:
    """Scan loaded NPK entries for explicit model-folder metadata references."""
    normalized_roots = normalize_character_folder_roots(roots)
    root_set = set(normalized_roots)
    grouped = {}
    seen_references = set()
    referenced_indices = set()
    entries = getattr(npk_file, "metadata_entries", getattr(npk_file, "entries", {}))

    from core.npk.scan_control import checkpoint, report_progress

    report_progress(progress, "Scanning character metadata", 0, len(entries))
    for current, (index, entry) in enumerate(sorted(entries.items()), 1):
        checkpoint(cancelled)
        if current % 64 == 0 or current == len(entries):
            report_progress(progress, "Scanning character metadata", current, len(entries))
        flags = getattr(entry, "data_flags", None)
        if flags is not None:
            from core.npk.class_types import NPKEntryDataFlags

            if not flags & NPKEntryDataFlags.TEXT and getattr(entry, "extension", "") not in {
                "mtg",
                "gim",
                "xml",
                "dat",
                "mtl",
            }:
                continue
        if hasattr(npk_file, "get_character_folder_paths"):
            paths = npk_file.get_character_folder_paths(index)
        else:
            paths = model_paths(getattr(entry, "data", b""))

        source_name = _entry_source_name(entry, index, npk_file)
        source_signature = int(getattr(entry, "file_signature", 0))
        source_extension = str(getattr(entry, "extension", "") or "")
        for asset_path in paths:
            parts = asset_path.split("\\")
            if len(parts) < 4:
                continue
            root = "\\".join(parts[:2])
            if not any(root == allowed or root.startswith(allowed + "\\") for allowed in root_set):
                continue

            folder_name = parts[2]
            group_key = (root, folder_name)
            reference_key = (group_key, asset_path, index)
            if reference_key in seen_references:
                continue
            seen_references.add(reference_key)
            referenced_indices.add(index)
            folder = grouped.setdefault(
                group_key,
                CharacterFolder(root=root, name=folder_name),
            )
            folder.references.append(
                CharacterFolderReference(
                    asset_path=asset_path,
                    source_index=index,
                    source_signature=source_signature,
                    source_name=source_name,
                    source_extension=source_extension,
                )
            )

    folders = sorted(grouped.values(), key=lambda item: (item.root, item.name))
    return CharacterFolderReport(
        roots=normalized_roots,
        scanned_entries=len(entries),
        referenced_entries=len(referenced_indices),
        folders=folders,
    )
