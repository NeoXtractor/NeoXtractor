"""Flat list model over an ArchiveSource.

One column: the full display path, an icon, and a status color mapped from
``EntryInfo.status``.  Content is fetched on selection, so a row's appearance
is fixed at load time plus probe updates.  Rows are addressed by
position == key.

The view runs with ``uniformItemSizes``, sizing every row from one sampled
hint, so that single width must equal the widest name.  This model measures
each row's natural width once at load and tracks the maximum (``_max_width``
plus the ``_max_rows`` that reach it); ``content_width()`` hands that maximum
to the delegate.  A probe re-measures its row and updates the maximum,
rescanning only when the unique widest row shrinks; ``max_width_changed``
tells the view to re-sample.

``rowsInserted``/``rowsRemoved``/``rowsMoved`` keep the cache exact through
the model's own signals: new rows are measured in and folded into the
maximum, removed rows drop out (rescanning when the widest-row set empties),
and a move permutes the cache in place.  Filtering hides rows and changes no
row count.
"""

from __future__ import annotations

from typing import Any

from PySide6 import QtCore, QtGui, QtWidgets

from neoxtractor.core.packages.base import Status
from neoxtractor.gui.models.archive_source import ArchiveSource

STATUS_COLORS: dict[Status, QtGui.QColor] = {
    Status.ENCRYPTED: QtGui.QColor("goldenrod"),
    Status.UNRECOGNIZED: QtGui.QColor("goldenrod"),
    Status.MISSING: QtGui.QColor("indianred"),
    Status.CORRUPTED: QtGui.QColor("goldenrod"),
}


class ArchiveListModel(QtCore.QAbstractListModel):
    """A QListView model showing every entry of one archive."""

    KeyRole = int(QtCore.Qt.ItemDataRole.UserRole)

    max_width_changed = QtCore.Signal()

    def __init__(
        self, source: ArchiveSource, parent: QtCore.QObject | None = None
    ) -> None:
        super().__init__(parent)
        self._source = source

        icons_from = parent if isinstance(parent, QtWidgets.QWidget) else None
        style = None
        if icons_from is not None:
            style = icons_from.style()
        self._file_icon = (
            style.standardIcon(QtWidgets.QStyle.StandardPixmap.SP_FileIcon)
            if style is not None
            else QtGui.QIcon()
        )
        self._warn_icon = (
            style.standardIcon(
                QtWidgets.QStyle.StandardPixmap.SP_MessageBoxWarning
            )
            if style is not None
            else QtGui.QIcon()
        )
        self._error_icon = (
            style.standardIcon(
                QtWidgets.QStyle.StandardPixmap.SP_MessageBoxCritical
            )
            if style is not None
            else QtGui.QIcon()
        )

        # Row-width tracking for the uniform-size view (see module docstring).
        self._view = (
            parent if isinstance(parent, QtWidgets.QAbstractItemView) else None
        )
        self._measure = QtWidgets.QStyledItemDelegate()
        self._widths: list[int] = []
        self._max_width = 0
        self._max_rows: set[int] = set()
        self._measure_all()
        self.rowsInserted.connect(self._on_rows_inserted)
        self.rowsRemoved.connect(self._on_rows_removed)
        self.rowsMoved.connect(self._on_rows_moved)

    # --- width tracking -----------------------------------------------------

    def content_width(self) -> int:
        """The widest row's natural width, for the view's uniform sizing."""
        return self._max_width

    def _option(self) -> QtWidgets.QStyleOptionViewItem:
        option = QtWidgets.QStyleOptionViewItem()
        if self._view is not None:
            # Match the view's own render option (font, icon size,
            # decoration size) so a measured width equals the drawn one.
            self._view.initViewItemOption(option)
        option.rect = QtCore.QRect(0, 0, 100, option.fontMetrics.height())
        return option

    def _width_of(self, row: int, option: QtWidgets.QStyleOptionViewItem) -> int:
        return self._measure.sizeHint(option, self.index(row)).width()

    def _measure_all(self) -> None:
        option = self._option()
        self._widths = [self._width_of(row, option) for row in range(len(self._source))]
        self._max_width = max(self._widths, default=0)
        self._max_rows = {
            row for row, width in enumerate(self._widths) if width == self._max_width
        }

    def _recompute_max(self) -> None:
        self._max_width = max(self._widths, default=0)
        self._max_rows = {
            row for row, width in enumerate(self._widths) if width == self._max_width
        }

    def _rescan_max(self) -> None:
        self._recompute_max()
        self.max_width_changed.emit()

    def remeasure_all(self) -> None:
        """Recompute every row width and the maximum (a font change)."""
        before = self._max_width
        self._measure_all()
        if self._max_width != before:
            self.max_width_changed.emit()

    def _on_rows_inserted(
        self, parent: QtCore.QModelIndex, first: int, last: int
    ) -> None:
        """Measure new rows into the cache and fold them into the maximum.

        Runs after the model has grown, so the new rows are addressable.
        Widest indices at or below the insertion point shift down by the new
        count before the fresh widths compete for the maximum.
        """
        count = last - first + 1
        option = self._option()
        new_widths = [self._width_of(row, option) for row in range(first, last + 1)]
        self._widths[first:first] = new_widths
        self._max_rows = {
            row + count if row >= first else row for row in self._max_rows
        }
        for row, width in enumerate(new_widths, start=first):
            if width > self._max_width:
                self._max_width = width
                self._max_rows = {row}
            elif width == self._max_width:
                self._max_rows.add(row)

    def _on_rows_removed(
        self, parent: QtCore.QModelIndex, first: int, last: int
    ) -> None:
        """Drop removed rows from the cache, rescanning if the max left.

        Runs after the model has shrunk.  Widest indices inside the removed
        range are dropped and those above it shift up.  The widest-row set
        lists every holder of the maximum, so an empty set after removal
        means a removed row was the last holder and the remainder is rescanned.
        """
        count = last - first + 1
        del self._widths[first : last + 1]
        self._max_rows = {
            row - count if row > last else row
            for row in self._max_rows
            if not first <= row <= last
        }
        if not self._max_rows:
            self._recompute_max()

    def _on_rows_moved(
        self,
        parent: QtCore.QModelIndex,
        start: int,
        end: int,
        destination: QtCore.QModelIndex,
        row: int,
    ) -> None:
        """Permute the width cache and widest-row indices along with rows.

        A move reorders rows without touching their content, so the width
        values and the maximum are unchanged and only their positions move.
        ``row`` is Qt's pre-removal insertion anchor, shifted up by the block
        size when it sat after the moved range.  Widest indices follow the
        permutation: in-block rows land at the destination, rows above the
        range shift up, rows the block jumped over shift down.
        """
        del destination
        count = end - start + 1
        block = self._widths[start : end + 1]
        del self._widths[start : end + 1]
        insert_at = row if row < start else row - count
        self._widths[insert_at:insert_at] = block
        self._max_rows = {
            insert_at + (r - start)
            if start <= r <= end
            else r - count
            if r > end
            else r + count
            if r >= insert_at
            else r
            for r in self._max_rows
        }

    def rowCount(
        self,
        parent: QtCore.QModelIndex
        | QtCore.QPersistentModelIndex = QtCore.QModelIndex(),
    ) -> int:
        if parent.isValid():
            return 0
        return len(self._source)

    def data(
        self,
        index: QtCore.QModelIndex | QtCore.QPersistentModelIndex,
        role: int = QtCore.Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if not index.isValid():
            return None
        row = index.row()
        info = self._source.entry_at(row)

        if role == QtCore.Qt.ItemDataRole.DisplayRole:
            return self._source.display_name(row)
        if role == self.KeyRole:
            return info.key
        if role == QtCore.Qt.ItemDataRole.DecorationRole:
            return self._icon_for(row)
        if role == QtCore.Qt.ItemDataRole.ForegroundRole:
            color = STATUS_COLORS.get(info.status)
            if color is None and self._source.is_unreadable(row):
                # Content faults the index could not predict arrive through
                # is_unreadable; share the status group's warn color.
                color = STATUS_COLORS[Status.ENCRYPTED]
            if color is None:
                return None
            return QtGui.QBrush(color)
        return None

    def _icon_for(self, row: int) -> QtGui.QIcon:
        info = self._source.entry_at(row)
        if info.status is Status.MISSING:
            return self._error_icon
        if (
            info.status
            in (Status.ENCRYPTED, Status.CORRUPTED, Status.UNRECOGNIZED)
            or self._source.is_unreadable(row)
        ):
            return self._warn_icon
        return self._file_icon

    def refresh_row(self, row: int) -> None:
        """Tell views one row's data changed (probe arrival) and retrack width."""
        idx = self.index(row)
        self.dataChanged.emit(idx, idx)
        self._track_width(row)

    def _track_width(self, row: int) -> None:
        width = self._width_of(row, self._option())
        old = self._widths[row]
        if width == old:
            return
        self._widths[row] = width

        if width > self._max_width:
            self._max_width = width
            self._max_rows = {row}
        elif width == self._max_width:
            self._max_rows.add(row)
        elif row in self._max_rows:
            self._max_rows.discard(row)
            if not self._max_rows:  # this row held the maximum alone
                self._rescan_max()
                return
        else:
            return  # a non-widest row: the maximum stands
        self.max_width_changed.emit()
