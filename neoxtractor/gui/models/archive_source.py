"""The single source of truth for one opened archive's entry list.

Holds the ``EntryInfo`` list (position == key) plus a per-key probe slot
filled by the background scan.  Nothing here is Qt: the main window drains
the scan's buffered results and calls :meth:`record_probe` per entry, then
refreshes the rows and re-checks visibility for the ones it touches.  Full
display paths and keys are kept for every entry, so a directory-tree model
can be layered over the same source later with no structural change.
"""

from __future__ import annotations

from dataclasses import dataclass

from neoxtractor.core.packages.base import Archive, EntryInfo, Status


@dataclass
class ProbeSlot:
    """What the background scan learned about one entry (all optional).

    ``unreadable`` is set when the entry's decoded content could not be opened
    for probing; see :meth:`ArchiveSource.is_unreadable` for the grouping it
    joins.
    """

    ext: str | None = None
    category: str | None = None
    is_text: bool | None = None
    unreadable: bool = False
    done: bool = False


class ArchiveSource:
    """One archive's entries plus per-key probe slots."""

    def __init__(self, archive: Archive) -> None:
        self._archive = archive
        self._entries: list[EntryInfo] = list(archive.entries())
        self._probes: dict[int, ProbeSlot] = {}

    @property
    def archive(self) -> Archive:
        return self._archive

    def __len__(self) -> int:
        return len(self._entries)

    def entry_at(self, row: int) -> EntryInfo:
        """The EntryInfo for a display row (row == position == key)."""
        return self._entries[row]

    def display_name(self, row: int) -> str:
        """The full display path for a row.

        Nameless entries show their hex signature (or content hash) plus the
        extension the background probe guessed from content, once it landed:
        ``0x12345678.dat``.  Before the probe arrives the bare hex name
        stands; display is cosmetic -- every read addresses entries by key,
        never by this string.
        """
        info = self._entries[row]
        if info.name is not None:
            return info.name
        base = _hex_name(info)
        slot = self._probes.get(info.key)
        if slot is not None and slot.ext:
            return f"{base}.{slot.ext}"
        return base

    def probe(self, row: int) -> ProbeSlot:
        """The probe slot for a row (a fresh unset slot if not yet probed)."""
        return self._probes.get(self._entries[row].key, ProbeSlot())

    def is_probed(self, row: int) -> bool:
        return self._probes.get(self._entries[row].key, ProbeSlot()).done

    def record_probe(
        self,
        key: int,
        *,
        ext: str | None,
        category: str | None,
        is_text: bool,
        unreadable: bool = False,
    ) -> ProbeSlot:
        """Store one entry's probe result; returns the filled slot."""
        slot = ProbeSlot(
            ext=ext,
            category=category,
            is_text=is_text,
            unreadable=unreadable,
            done=True,
        )
        self._probes[key] = slot
        return slot

    def usable_extension(self, row: int) -> str:
        """The routing extension: from the name, else the probed extension.

        Empty when neither is known -- the caller then waits on the probe or
        falls back to content-kind selection.
        """
        info = self._entries[row]
        if info.ext_hint:
            return info.ext_hint
        slot = self._probes.get(info.key)
        if slot is not None and slot.ext:
            return slot.ext
        return ""

    def is_encrypted(self, row: int) -> bool:
        return self._entries[row].status is Status.ENCRYPTED

    def is_missing(self, row: int) -> bool:
        return self._entries[row].status is Status.MISSING

    def is_unreadable(self, row: int) -> bool:
        """True when the row belongs in the "cannot read this content" group.

        From the index that is an ENCRYPTED, UNRECOGNIZED, or CORRUPTED
        status; from the scan it is an entry whose decoded content would not
        open.  MISSING stays its own group -- the bytes are simply not here,
        not unreadable.
        """
        info = self._entries[row]
        if info.status in (Status.ENCRYPTED, Status.UNRECOGNIZED, Status.CORRUPTED):
            return True
        slot = self._probes.get(info.key)
        return slot is not None and slot.unreadable


def _hex_name(info: EntryInfo) -> str:
    if info.signature is not None:
        return hex(info.signature)
    if info.content_hash is not None:
        return info.content_hash.hex()
    return f"entry_{info.key}"
