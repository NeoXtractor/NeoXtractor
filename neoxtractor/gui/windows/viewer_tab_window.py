"""A standalone tabbed window hosting one viewer class over many files.

Opened from the Tools menu (external files) or the entry list (archive
entries).  It builds fresh viewer instances per tab through the registry's
contract -- each tab owns its own :class:`ViewSource` and stream.  The
:class:`ICustomTabWindow` hook lets a viewer add its own menus (Save As,
etc.) after construction.
"""

from __future__ import annotations

import os
from typing import cast

from PySide6 import QtCore, QtWidgets

from neoxtractor.gui.viewers import registry
from neoxtractor.gui.viewers.base import (
    ICustomTabWindow,
    ViewerBase,
    ViewSource,
    make_file_source,
)


class ViewerTabWindow(QtWidgets.QMainWindow):
    """Manages viewer tabs for one viewer type."""

    def __init__(
        self, info: registry.ViewerInfo, parent: QtWidgets.QWidget | None = None
    ):
        super().__init__(parent)

        self._info = info
        self._viewer_name = info.display_name

        self.setWindowTitle(self._viewer_name)
        self.setMinimumSize(800, 600)

        self.central_widget = QtWidgets.QWidget(self)
        self.setCentralWidget(self.central_widget)
        self.central_layout = QtWidgets.QVBoxLayout(self.central_widget)

        self.no_tab_label = QtWidgets.QLabel("No file opened.")
        self.no_tab_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.no_tab_label.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )

        self.tab_widget = QtWidgets.QTabWidget(self)
        self.tab_widget.setTabsClosable(True)
        self.tab_widget.setVisible(False)
        self.tab_widget.tabCloseRequested.connect(self._close_tab)
        self.tab_widget.currentChanged.connect(
            lambda index: self.setWindowTitle(
                f"{self.tab_widget.tabText(index)} - {self._viewer_name}"
                if index >= 0
                else self._viewer_name
            )
        )
        self.central_layout.addWidget(self.no_tab_label)
        self.central_layout.addWidget(self.tab_widget)

        self.menuBar().addMenu(self._file_menu())

        if issubclass(self._info.viewer_class, ICustomTabWindow):
            self._info.viewer_class.setup_tab_window(self)

    def _file_menu(self) -> QtWidgets.QMenu:
        menu = QtWidgets.QMenu("File", self)

        open_action = menu.addAction("Open File")
        open_action.setIcon(
            self.style().standardIcon(
                QtWidgets.QStyle.StandardPixmap.SP_DirOpenIcon
            )
        )
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self._open_files)

        close_all = menu.addAction("Close All")
        close_all.setIcon(
            self.style().standardIcon(
                QtWidgets.QStyle.StandardPixmap.SP_DockWidgetCloseButton
            )
        )
        close_all.triggered.connect(self._close_all)
        return menu

    def _open_files(self) -> None:
        file_filter = self._dialog_filter()
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self, "Open File", "", file_filter
        )
        for i, path in enumerate(paths):
            if not path:
                continue
            with open(path, "rb") as handle:
                data = handle.read()
            name = os.path.basename(path)
            self.load_source(make_file_source(name, data), take_focus=i == 0)

    def _dialog_filter(self) -> str:
        extensions = sorted(self._info.accepted_extensions)
        if not extensions:
            return "All Files (*)"
        pattern = f"Supported Files (*.{' *.'.join(extensions)})"
        if self._info.fallback is not None:
            pattern += " ;; All Files (*)"
        return pattern

    def load_source(self, source: ViewSource, take_focus: bool = True) -> None:
        """Open a new tab with a fresh viewer instance showing ``source``."""
        viewer: ViewerBase = self._info.viewer_class()
        try:
            viewer.load(source)
        except ValueError as exc:
            QtWidgets.QMessageBox.critical(
                self, "Error", cast(str, exc.args[0] or str(exc))
            )
            return
        index = self.tab_widget.addTab(viewer, source.name)
        if take_focus:
            self.tab_widget.setCurrentIndex(index)
        self.no_tab_label.setVisible(False)
        self.tab_widget.setVisible(True)

    def _close_tab(self, index: int) -> None:
        widget = self.tab_widget.widget(index)
        if isinstance(widget, ViewerBase):
            widget.unload()
        self.tab_widget.removeTab(index)
        if self.tab_widget.count() == 0:
            self.setWindowTitle(self._viewer_name)
            self.tab_widget.setVisible(False)
            self.no_tab_label.setVisible(True)

    def _close_all(self) -> None:
        for index in range(self.tab_widget.count()):
            widget = self.tab_widget.widget(index)
            if isinstance(widget, ViewerBase):
                widget.unload()
        self.tab_widget.clear()
        self.setWindowTitle(self._viewer_name)
        self.tab_widget.setVisible(False)
        self.no_tab_label.setVisible(True)
