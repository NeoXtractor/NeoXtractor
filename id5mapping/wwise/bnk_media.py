"""Expand Wwise `.bnk` entries of an opened archive into their contained WEMs.

Banks store small, pre-rendered media inline (`DIDX` table + `DATA` chunk).
Those media are what the game actually plays, so the file list shows them as
individual `.wem` entries instead of the opaque `.bnk` container.

Names are resolved through the active :class:`WwiseEventIndex` when available
(event name first, otherwise `<shortID>.wem`).
"""

from __future__ import annotations

import os
import struct

from core.logger import get_logger
from core.npk.class_types import NPKEntry, NPKEntryDataFlags, State
from core.npk.detection import get_file_category


def extract_wem_media(bank_data: bytes) -> list[tuple[int, bytes]]:
    """Return `(shortID, wem bytes)` for every inline media of a bank."""
    if len(bank_data) < 8 or bank_data[:4] != b"BKHD":
        return []

    didx: tuple[int, int] | None = None
    data_chunk: tuple[int, int] | None = None
    offset = 0
    while offset + 8 <= len(bank_data):
        tag = bank_data[offset:offset + 4]
        size = struct.unpack_from("<I", bank_data, offset + 4)[0]
        payload = offset + 8
        if tag == b"DIDX" and size >= 12:
            didx = (payload, size)
        elif tag == b"DATA":
            data_chunk = (payload, size)
        offset = payload + size

    if didx is None or data_chunk is None:
        return []

    data_start, data_size = data_chunk
    media: list[tuple[int, bytes]] = []
    for index in range(didx[1] // 12):
        record = didx[0] + 12 * index
        sid, rel_offset, length = struct.unpack_from("<III", bank_data, record)
        if not length or rel_offset + length > data_size:
            continue
        start = data_start + rel_offset
        media.append((sid, bank_data[start:start + length]))
    return media


def expand_banks(archive_file, wwise_index=None) -> int:
    """Replace every expandable `.bnk` entry with its contained WEM entries.

    The archive's `indices` / `entries` lists are rebuilt in place so all the
    row based UI (filter, preview, extract) keeps working. Returns the number
    of WEM entries that were added.
    """
    indices = getattr(archive_file, "indices", None)
    entries = getattr(archive_file, "entries", None)
    if not indices or entries is None:
        return 0

    new_indices = []
    new_entries: dict[int, NPKEntry] = {}
    expanded_banks = 0
    dropped_banks = 0
    added = 0

    for old_row, old_index in enumerate(list(indices)):
        entry = entries.get(old_row)
        is_bank = entry is not None and (entry.extension or "").lower() == "bnk"
        if is_bank:
            media = extract_wem_media(entry.data)
            if not media:
                # Banks without inline media are never shown: their external
                # WEMs already exist as their own rows.
                dropped_banks += 1
                continue
        else:
            media = []
        if not media:
            new_entries[len(new_indices)] = entry
            new_indices.append(old_index)
            continue

        expanded_banks += 1
        for sid, payload in media:
            wem = _make_wem_entry(sid, payload, wwise_index)
            new_entries[len(new_indices)] = wem
            new_indices.append(wem)
            added += 1

    if not expanded_banks and not dropped_banks:
        return 0

    archive_file.indices = new_indices
    archive_file.entries = new_entries
    if hasattr(archive_file, "file_count"):
        archive_file.file_count = len(new_indices)

    get_logger().info(
        "Wwise: expanded %d bank(s) into %d wem entr(ies), dropped %d media-less bank(s), %d total rows",
        expanded_banks,
        added,
        dropped_banks,
        len(new_indices),
    )
    return added


def apply_bank_labels(archive_file, wwise_index) -> int:
    """Rename WEM entries (bank media and external streams) by event name.

    Bank media already carry their shortID, while the external WEMs of an
    `.idx` are only known by their md5, so both paths are handled here. This
    keeps the exported filename equal to what the file list shows.
    """
    if wwise_index is None:
        return 0
    entries = getattr(archive_file, "entries", None)
    if not entries:
        return 0

    renamed = 0
    used_names: set[str] = set()
    for row in sorted(entries):
        entry = entries[row]
        if (entry.extension or "").lower() != "wem":
            continue
        sid = getattr(entry, "wwise_sid", None)
        if sid:
            label = wwise_index.label_for_sid(sid)
        else:
            md5 = getattr(entry, "raw_hash_hex", None)
            label = wwise_index.label_for_md5(md5) if md5 else None
        name = f"{label}.wem" if label else (entry.filename or "")
        if not name:
            continue
        # The same label can still land on two rows (an inline medium and the
        # streamed copy of the same md5, or a natural name that matches a
        # suffixed one); make the exported filename unique.
        stem, extension = os.path.splitext(name)
        candidate = name
        counter = 2
        while candidate.lower() in used_names:
            candidate = f"{stem}_{counter:02d}{extension}"
            counter += 1
        used_names.add(candidate.lower())
        if candidate != entry.filename:
            entry.filename = candidate
            renamed += 1
    return renamed


def mount_archive_banks(archive_file, wwise_index) -> int:
    """Mount the events of every bank found in the loaded package.

    The bank payloads are cached on the archive, because after `expand_banks`
    the `.bnk` rows no longer exist but a rebuild (the dialog's Reload) still
    needs them to resolve the media shortIDs again.
    """
    entries = getattr(archive_file, "entries", None) or {}
    banks = getattr(archive_file, "_wwise_banks", None)
    if not banks:
        banks = [
            entry.data
            for entry in entries.values()
            if (entry.extension or "").lower() == "bnk" and entry.data
        ]
        if banks:
            archive_file._wwise_banks = banks
    if not banks:
        return 0
    return wwise_index.mount_banks(banks)


def wem_display_name(sid: int, wwise_index=None) -> str:
    """Filename for a bank media entry: unique event label, else `<sid>.wem`."""
    label = wwise_index.label_for_sid(sid) if wwise_index is not None else None
    return f"{label}.wem" if label else f"{sid}.wem"


def _make_wem_entry(sid: int, payload: bytes, wwise_index) -> NPKEntry:
    entry = NPKEntry()
    entry.filename = wem_display_name(sid, wwise_index)
    entry._extension = "wem"
    entry.data = payload
    entry.file_signature = sid
    entry.file_length = len(payload)
    entry.file_original_length = len(payload)
    entry.data_flags = NPKEntryDataFlags.NONE
    entry.category = get_file_category("wem")
    entry.state = State.CACHED
    entry.wwise_sid = sid
    return entry
