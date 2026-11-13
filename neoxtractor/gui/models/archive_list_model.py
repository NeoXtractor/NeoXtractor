"""Flat list model over an ArchiveSource.

One column: the full display path, an icon, and a status color mapped from
``EntryInfo.status``.  Content is fetched on selection, so a row's appearance
is fixed at load time plus probe updates.  Rows are addressed by
position == key.
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
        """Tell views that one row's data changed (probe arrival)."""
        idx = self.index(row)
        self.dataChanged.emit(idx, idx)
