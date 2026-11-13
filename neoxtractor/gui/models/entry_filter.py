"""Row visibility for the archive list: path-substring + category filters.

Matching runs on the entry's **full display path** (substring,
case-insensitive), so a query hitting a directory segment keeps every file
under it.  Category/text predicates evaluate on probed fields; entries not
yet probed pass category filters (unknown == include) and are re-evaluated
when their probe lands.  Filtered rows are hidden by the view, never
removed from the model, so hidden rows stay refreshable by the probe scan.
"""

from __future__ import annotations

from neoxtractor.core.packages.base import Status
from neoxtractor.gui.models.archive_source import ArchiveSource


class EntryFilter:
    """The active filter conditions, evaluated per row."""

    def __init__(self) -> None:
        self.text = ""
        self.categories: set[str] = set()   # empty = all categories pass
        self.show_text = True
        self.show_binary = True
        self.show_missing = False
        self.show_encrypted = True

    def configure(
        self,
        *,
        text: str | None = None,
        categories: set[str] | None = None,
        show_text: bool | None = None,
        show_binary: bool | None = None,
        show_missing: bool | None = None,
        show_encrypted: bool | None = None,
    ) -> None:
        if text is not None:
            self.text = text
        if categories is not None:
            self.categories = categories
        if show_text is not None:
            self.show_text = show_text
        if show_binary is not None:
            self.show_binary = show_binary
        if show_missing is not None:
            self.show_missing = show_missing
        if show_encrypted is not None:
            self.show_encrypted = show_encrypted

    def content_can_hide(self) -> bool:
        """True when content predicates could hide a not-yet-probed row.

        The text substring and the missing switch are fixed at load; the
        encrypted switch is partly status-fixed and partly probe-derived (an
        OK entry whose content turns out unreadable).  When every content
        predicate here is open -- no category set, text and binary both shown,
        unreadable shown -- a probe landing can never change a row's
        visibility, so a scan may skip re-checking visibility for unprobed
        rows: the basis for batching visibility updates.
        """
        return (
            bool(self.categories)
            or not self.show_text
            or not self.show_binary
            or not self.show_encrypted
        )

    def matches(self, source: ArchiveSource, row: int) -> bool:
        info = source.entry_at(row)

        needle = self.text.strip().lower()
        if needle and needle not in source.display_name(row).lower():
            return False

        if info.status is Status.MISSING and not self.show_missing:
            return False
        if not self.show_encrypted and source.is_unreadable(row):
            # One switch covers the whole unreadable group (see
            # ArchiveSource.is_unreadable): load-fixed statuses plus
            # scan-detected content faults.
            return False

        slot = source.probe(row)
        if not slot.done:
            # Unprobed: unknown == include for the content predicates.
            return True

        if self.categories and slot.category not in self.categories:
            return False
        if slot.is_text is True and not self.show_text:
            return False
        if slot.is_text is False and not self.show_binary:
            return False
        return True
