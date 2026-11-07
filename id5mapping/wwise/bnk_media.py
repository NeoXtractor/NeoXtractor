"""Expand Wwise `.bnk` entries of an opened archive into their contained WEMs.

Banks store small, pre-rendered media inline (`DIDX` table + `DATA` chunk).
Those media are what the game actually plays, so the file list shows them as
individual `.wem` entries instead of the opaque `.bnk` container.

Streamed sources are the exception: the bank only carries a short prefetch
(the game streams the rest from a separate `.wem` resource). Such a prefetch is
a RIFF whose declared length is larger than the payload, so it cannot be played
as-is; `expand_banks` swaps it for the complete `.wem` of the same archive and
drops the now duplicated standalone row. A prefetch with no complete counterpart
is left out entirely, so truncated audio is never listed or exported.

Names are resolved through the active :class:`WwiseEventIndex` when available
(event name first, otherwise `<shortID>.wem`).
"""

from __future__ import annotations

import hashlib
import os
import struct

from core.logger import get_logger
from core.npk.class_types import NPKEntry, NPKEntryDataFlags, State
from core.npk.detection import get_file_category
from core.wem.bnk import extract_wem_media  # noqa: F401  (re-exported)


def expand_banks(archive_file, wwise_index=None, dedupe_media: bool = True) -> int:
    """Replace every expandable `.bnk` entry with its contained WEM entries.

    The archive's `indices` / `entries` lists are rebuilt in place so all the
    row based UI (filter, preview, extract) keeps working. Returns the number
    of WEM entries that were added.

    `dedupe_media` keeps one row per unique medium: identical audio stored in
    several banks, and standalone `.wem` rows that only repeat a bank medium,
    are dropped. With it off every reference stays, so the list and the export
    contain duplicates of the same audio.
    """
    indices = getattr(archive_file, "indices", None)
    entries = getattr(archive_file, "entries", None)
    if not indices or entries is None:
        return 0

    # Pass 1: expand the banks and resolve their streamed media, remembering
    # which standalone `.wem` rows are exact copies of a bank's medium.
    plans: dict[int, list] = {}
    used_rows: set[int] = set()
    dropped_rows: set[int] = set()
    claimed_digests: set[bytes] = set()
    seen_media: set[tuple] = set()
    expanded_banks = 0
    dropped_banks = 0
    added = 0
    resolved_streams = 0
    skipped_truncated = 0
    duplicate_media = 0

    for old_row, old_index in enumerate(list(indices)):
        entry = entries.get(old_row)
        if entry is None or (entry.extension or "").lower() != "bnk":
            continue
        media = extract_wem_media(entry.data)
        if not media:
            # Banks without inline media are never shown: their external
            # WEMs already exist as their own rows.
            dropped_banks += 1
            dropped_rows.add(old_row)
            continue

        expanded_banks += 1
        planned = []
        for sid, payload in media:
            total = _riff_total(payload)
            if total and total > len(payload):
                row, full = _stream_copy(archive_file, payload, total)
                if full is None:
                    # A prefetch without its full copy would decode as
                    # "RIFF truncated", so it is not listed at all.
                    skipped_truncated += 1
                    continue
                used_rows.add(row)
                resolved_streams += 1
                payload = full
            digest = _content_digest(payload)
            claimed_digests.add(digest)
            if dedupe_media and (sid, digest) in seen_media:
                # Banks may repeat the same medium (and shortID); one row is
                # enough for the list and the export.
                duplicate_media += 1
                continue
            seen_media.add((sid, digest))
            planned.append((sid, payload))
        plans[old_row] = planned

    if not expanded_banks and not dropped_banks:
        return 0

    # Pass 2: rebuild the row tables, leaving out consumed stream copies and
    # any RIFF whose declared size exceeds its data.
    new_indices = []
    new_entries: dict[int, NPKEntry] = {}
    deduped_streams = 0
    dropped_truncated_rows = 0

    for old_row, old_index in enumerate(list(indices)):
        entry = entries.get(old_row)
        if entry is None:
            continue
        if old_row in plans:
            for sid, payload in plans[old_row]:
                wem = _make_wem_entry(sid, payload, wwise_index)
                new_entries[len(new_indices)] = wem
                new_indices.append(wem)
                added += 1
            continue
        if old_row in used_rows and dedupe_media:
            # The bank now carries this medium; keep only the named row.
            deduped_streams += 1
            continue
        if old_row in dropped_rows:
            continue
        if _is_truncated_riff(getattr(entry, "data", None)):
            dropped_truncated_rows += 1
            continue
        if (
            dedupe_media
            and not getattr(entry, "wwise_sid", None)
            and (entry.extension or "").lower() == "wem"
            and _content_digest(entry.data) in claimed_digests
        ):
            # The external md5 row carries the same audio as a bank medium,
            # which already has the shortID used for naming; keep that one.
            deduped_streams += 1
            continue
        new_entries[len(new_indices)] = entry
        new_indices.append(old_index)

    archive_file.indices = new_indices
    archive_file.entries = new_entries
    archive_file._wwise_stream_index = None
    if hasattr(archive_file, "file_count"):
        archive_file.file_count = len(new_indices)

    get_logger().info(
        "Wwise: expanded %d bank(s) into %d wem entr(ies), %d streamed media completed "
        "from external .wem, %d duplicate media row(s) removed (dedupe=%s), %d truncated "
        "prefetch(es) and %d truncated row(s) skipped, dropped %d media-less bank(s), "
        "%d total rows",
        expanded_banks,
        added,
        resolved_streams,
        deduped_streams + duplicate_media,
        "on" if dedupe_media else "off",
        skipped_truncated,
        dropped_truncated_rows,
        dropped_banks,
        len(new_indices),
    )
    return added


#: Bank media that is only a prefetch of a streamed source: RIFF declares more
#: bytes than the inline payload carries. The remainder lives in a separate
#: `.wem` resource of the same archive.
_RIFF_HEAD = 12
_KEY_SLICE = slice(12, 76)


def _riff_total(payload: bytes) -> int:
    if len(payload) < _RIFF_HEAD or payload[:4] != b"RIFF":
        return 0
    return struct.unpack_from("<I", payload, 4)[0] + 8


def _stream_key(payload: bytes, total: int) -> tuple:
    """Identity of a streamed medium: declared length + start of its body.

    A prefetch and its full copy share the header and the first bytes after it,
    so the pair (declared total size, digest of the body head) matches them
    without hashing megabytes of audio.
    """
    return total, hashlib.blake2b(bytes(payload[_KEY_SLICE]), digest_size=16).digest()


def _is_truncated_riff(payload: bytes | None) -> bool:
    """True when a RIFF declares more bytes than it actually carries."""
    total = _riff_total(payload or b"")
    return bool(total) and total > len(payload)


def _content_digest(payload: bytes | None) -> bytes:
    """Digest of the whole payload, used to spot the same audio stored twice."""
    return hashlib.blake2b(bytes(payload or b""), digest_size=32).digest()


def _stream_media_index(archive_file) -> dict:
    """Complete external `.wem` payloads of this archive, keyed by `_stream_key`.

    Values are ``(row, data)`` so the caller can retire the duplicated row once
    the bank carries the same medium.
    """
    cached = getattr(archive_file, "_wwise_stream_index", None)
    if cached is not None:
        return cached
    index: dict[tuple, tuple[int, bytes]] = {}
    entries = getattr(archive_file, "entries", None) or {}
    for row, entry in list(entries.items()):
        data = getattr(entry, "data", None)
        if not data or len(data) < _KEY_SLICE.stop or data[:4] != b"RIFF":
            continue
        total = _riff_total(data)
        if total != len(data):
            # Prefetch copies and truncated resources are not usable sources.
            continue
        index.setdefault(_stream_key(data, total), (row, data))
    archive_file._wwise_stream_index = index
    return index


def _stream_copy(archive_file, payload: bytes, total: int) -> tuple[int, bytes] | tuple[None, None]:
    """Complete copy of a streamed medium, as ``(row, data)``; ``(None, None)`` if absent."""
    hit = _stream_media_index(archive_file).get(_stream_key(payload, total))
    if hit is None:
        get_logger().debug(
            "Wwise: streamed media prefetch (%d of %d bytes) has no external copy",
            len(payload),
            total,
        )
        return None, None
    return hit


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
