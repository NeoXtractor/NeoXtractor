"""QListView host for one archive's flat entry list.

It owns an :class:`ArchiveListModel` over an :class:`ArchiveSource` and turns
view interactions into neutral signals -- row, key, and display name -- so the
main window never re-derives them.  The context menu lists the discovered
viewers (Open in ...) and the two extract actions; extraction is the main
window's ``gui.utils.export`` path, emitted here as a request.  There is no
rename action: name resolution belongs to the naming layer, not this widget.
"""

from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from neoxtractor.gui.models.archive_list_model import ArchiveListModel
from neoxtractor.gui.models.archive_source import ArchiveSource
from neoxtractor.gui.viewers import registry

# Pixel distance for one full wheel notch (120 angle units).  Sub-notch
# deltas accumulate their fraction across events.
_WHEEL_PIXELS_PER_NOTCH = 60.0


def _open_handler(widget: "ArchiveList", viewer_name: str, rows: list[int]):
    """Bind one viewer name + row set into a no-arg menu callback."""

    def handler() -> None:
        widget._open_with(viewer_name, rows)

    return handler


class _UniformWidthDelegate(QtWidgets.QStyledItemDelegate):
    """Report every row at the model's widest natural width.

    With ``uniformItemSizes`` QListView sizes all rows from one sampled
    hint, so this returns the model's tracked maximum as that width, leaving
    no visible name truncated.  Height comes from the base style hint.
    """

    def sizeHint(
        self,
        option: QtWidgets.QStyleOptionViewItem,
        index: QtCore.QModelIndex | QtCore.QPersistentModelIndex,
    ) -> QtCore.QSize:
        size = super().sizeHint(option, index)
        model = index.model()
        if isinstance(model, ArchiveListModel):
            size.setWidth(max(size.width(), model.content_width()))
        return size


class ArchiveList(QtWidgets.QListView):
    """A list of archive entries with preview / open / extract actions."""

    preview_entry = QtCore.Signal(int, int, str)  # row, key, name
    open_entry = QtCore.Signal(int, int, str)  # row, key, name
    open_entry_with = QtCore.Signal(int, int, str, str)  # row, key, name, viewer
    extract = QtCore.Signal(list, bool)  # rows, decoded

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._source: ArchiveSource | None = None
        # Fractional pixels carried between wheel events, per axis
        # (0 = horizontal, 1 = vertical).
        self._wheel_remainder = [0.0, 0.0]

        self.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection
        )
        # Uniform row heights: without this, every setRowHidden/dataChanged
        # makes QListView re-measure the whole column.
        self.setUniformItemSizes(True)
        # Scroll by pixels, not rows: the scrollbar value is a pixel offset,
        # so wheelEvent's per-notch pixel math maps directly instead of one
        # step jumping a whole row.
        self.setVerticalScrollMode(
            QtWidgets.QAbstractItemView.ScrollMode.ScrollPerPixel
        )
        self.setHorizontalScrollMode(
            QtWidgets.QAbstractItemView.ScrollMode.ScrollPerPixel
        )
        self.setDragEnabled(False)
        self.setAcceptDrops(False)
        self.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)
        self.doubleClicked.connect(self._on_double_clicked)

    # --- click handling -------------------------------------------------------

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        """Click-select without Qt's delayed scroll-to-pressed-row.

        QAbstractItemView arms a ``doubleClickInterval + 100 ms`` timer on
        every press and then calls ``scrollTo(pressedIndex, EnsureVisible)``
        when it fires.  With hidden rows (an active filter), QListView's
        logical-vs-visual index bookkeeping disagrees across that path's
        geometry math, so rows that are already fully visible jump a little
        -- in an arbitrary direction, one second after the click.

        The timer is gated by the same ``autoScroll`` flag that drives the
        useful edge auto-scroll during drag selection, so the flag is
        flipped only around the base press handler: the delayed timer never
        starts, while drag-selection scrolling stays enabled.
        """
        self.setAutoScroll(False)
        try:
            super().mousePressEvent(event)
        finally:
            self.setAutoScroll(True)

    # --- scrolling ----------------------------------------------------------

    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:
        """Scroll by pixels on the ScrollPerPixel bars, from either channel.

        Windows reports wheels and trackpads alike through ``angleDelta`` (a
        wheel's a multiple of 120, a trackpad's fine-grained); ``pixelDelta``
        appears only on platforms with a pixel API such as macOS.  Here
        ``pixelDelta`` maps 1:1 per axis and ``angleDelta`` scales by
        ``_WHEEL_PIXELS_PER_NOTCH`` per 120 units, carrying its fraction per
        axis so small deltas glide instead of jumping rows.  A direction flip
        drops the stale fraction; a null delta falls through to the base.
        """
        pixels = event.pixelDelta()
        if not pixels.isNull():
            dx, dy = pixels.x(), pixels.y()
            if event.inverted():
                dx, dy = -dx, -dy
            self._scroll_pixels(dx, dy)
            event.accept()
            return

        angle = event.angleDelta()
        if angle.isNull():
            super().wheelEvent(event)
            return

        scale = _WHEEL_PIXELS_PER_NOTCH / 120.0
        if event.inverted():
            scale = -scale
        steps = [0, 0]
        for axis, delta in enumerate((angle.x(), angle.y())):
            if delta == 0:
                continue
            remainder = self._wheel_remainder[axis]
            if remainder and delta * remainder < 0:
                remainder = 0.0
            remainder += delta * scale
            steps[axis] = int(remainder)
            self._wheel_remainder[axis] = remainder - steps[axis]
        self._scroll_pixels(steps[0], steps[1])
        event.accept()

    def _scroll_pixels(self, dx: int, dy: int) -> None:
        """Move the pixel-valued scrollbars by a signed pixel offset."""
        if dx:
            bar = self.horizontalScrollBar()
            bar.setValue(bar.value() - dx)
        if dy:
            bar = self.verticalScrollBar()
            bar.setValue(bar.value() - dy)

    # --- binding ------------------------------------------------------------

    def set_source(self, source: ArchiveSource | None) -> None:
        """Show one archive's entries, or clear the view when *source* is None."""
        self._source = source
        if source is None:
            self.setModel(None)
            return
        model = ArchiveListModel(source, self)
        model.max_width_changed.connect(self._relayout_uniform_width)
        self.setItemDelegate(_UniformWidthDelegate(self))
        self.setModel(model)
        # setModel builds the selection model; connect only after it exists.
        selection = self.selectionModel()
        if selection is not None:
            selection.currentChanged.connect(self._on_current_changed)

    def _relayout_uniform_width(self) -> None:
        """Re-sample the uniform row width after the tracked maximum moves.

        ``doItemsLayout`` clears QListView's cached item size, so the next
        layout re-reads the delegate hint -- now the model's new widest
        width -- and the horizontal range follows.
        """
        self.doItemsLayout()
        self.viewport().update()

    def changeEvent(self, event: QtCore.QEvent) -> None:
        """Re-measure every row width when the view's font changes."""
        if event.type() == QtCore.QEvent.Type.FontChange:
            model = self.model()
            if isinstance(model, ArchiveListModel):
                model.remeasure_all()
        super().changeEvent(event)

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
