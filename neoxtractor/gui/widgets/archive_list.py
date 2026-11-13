"""QListView host for one archive's flat entry list.

It owns an :class:`ArchiveListModel` over an :class:`ArchiveSource` and turns
view interactions into neutral signals -- row, key, and display name -- so the
main window never re-derives them.  The context menu lists the discovered
viewers (Open in ...) and the two extract actions; extraction is the main
window's ``gui.utils.export`` path, emitted here as a request.  There is no
rename action: name resolution belongs to the naming layer, not this widget.
"""

from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from neoxtractor.gui.models.archive_list_model import ArchiveListModel
from neoxtractor.gui.models.archive_source import ArchiveSource
from neoxtractor.gui.viewers import registry


def _open_handler(widget: "ArchiveList", viewer_name: str, rows: list[int]):
    """Bind one viewer name + row set into a no-arg menu callback."""

    def handler() -> None:
        widget._open_with(viewer_name, rows)

    return handler


class ArchiveList(QtWidgets.QListView):
    """A list of archive entries with preview / open / extract actions."""

    preview_entry = QtCore.Signal(int, int, str)  # row, key, name
    open_entry = QtCore.Signal(int, int, str)  # row, key, name
    open_entry_with = QtCore.Signal(int, int, str, str)  # row, key, name, viewer
    extract = QtCore.Signal(list, bool)  # rows, decoded

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._source: ArchiveSource | None = None

        self.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection
        )
        # Uniform row heights: without this, every setRowHidden/dataChanged
        # makes QListView re-measure the whole column.
        self.setUniformItemSizes(True)
        self.setDragEnabled(False)
        self.setAcceptDrops(False)
        self.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)
        self.doubleClicked.connect(self._on_double_clicked)

    # --- binding ------------------------------------------------------------

    def set_source(self, source: ArchiveSource | None) -> None:
        """Show one archive's entries, or clear the view when *source* is None."""
        self._source = source
        if source is None:
            self.setModel(None)
            return
        self.setModel(ArchiveListModel(source, self))
        # setModel builds the selection model; connect only after it exists.
        selection = self.selectionModel()
        if selection is not None:
            selection.currentChanged.connect(self._on_current_changed)

    def _key_name(self, row: int) -> tuple[int, str]:
        assert self._source is not None
        return self._source.entry_at(row).key, self._source.display_name(row)

    # --- selection handlers -------------------------------------------------

    def _on_current_changed(
        self, current: QtCore.QModelIndex, previous: QtCore.QModelIndex
    ) -> None:
        del previous
        if not current.isValid() or self._source is None:
            return
        key, name = self._key_name(current.row())
        self.preview_entry.emit(current.row(), key, name)

    def _on_double_clicked(self, index: QtCore.QModelIndex) -> None:
        if not index.isValid() or self._source is None:
            return
        key, name = self._key_name(index.row())
        self.open_entry.emit(index.row(), key, name)

    # --- context menu -------------------------------------------------------

    def _selected_rows(self) -> list[int]:
        return sorted({index.row() for index in self.selectedIndexes()})

    def _show_context_menu(self, position: QtCore.QPoint) -> None:
        if self._source is None:
            return
        rows = self._selected_rows()
        if not rows:
            return

        menu = QtWidgets.QMenu(self)

        menu.addAction("Extract", lambda: self.extract.emit(rows, True))
        menu.addAction("Extract Raw", lambda: self.extract.emit(rows, False))
        menu.addSeparator()

        for viewer_name in registry.viewer_names():
            info = registry.info_for(viewer_name)
            label = info.display_name if info is not None else viewer_name
            menu.addAction(f"Open in {label}", _open_handler(self, viewer_name, rows))

        menu.exec(self.viewport().mapToGlobal(position))

    def _open_with(self, viewer_name: str, rows: list[int]) -> None:
        assert self._source is not None
        for row in rows:
            key, name = self._key_name(row)
            self.open_entry_with.emit(row, key, name, viewer_name)
