"""Provides a shared class for viewers to have a tabbed window."""

import os
from typing import Type, TypeVar

from PySide6 import QtCore, QtWidgets

from core.file import IFile, SimpleFile
from gui.widgets.viewer import Viewer

T = TypeVar("T", bound=Viewer)


class ViewerTabWindow(QtWidgets.QMainWindow):
    """
    A window that manages viewer tabs for displaying files.
    This window provides a tabbed interface for viewing files with a specific viewer. It allows
    opening multiple files, each in its own tab, and manages the visibility of tabs and a message
    when no files are open.
    Methods:
        load_file(data: bytes, filename: str): Loads file data into a new viewer tab.
    """

    def __init__(self, viewer: Type[T], parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)

        self._viewer_factory = viewer
        self._viewer_name = viewer.name

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
            )
        )
        self.central_layout.addWidget(self.no_tab_label)
        self.central_layout.addWidget(self.tab_widget)

        def file_menu() -> QtWidgets.QMenu:
            menu = QtWidgets.QMenu("File", self)

            open_file_action = menu.addAction("Open File")
            open_file_action.setIcon(
                self.style().standardIcon(
                    QtWidgets.QStyle.StandardPixmap.SP_DirOpenIcon
                )
            )
            open_file_action.setShortcut("Ctrl+O")

            def open_file_dialog():
                file_filter = "All Files (*)"
                if hasattr(self._viewer_factory, "accepted_extensions"):
                    extensions = getattr(self._viewer_factory, "accepted_extensions")
                    file_filter = f"Supported Files (*.{' *.'.join(extensions)})"
                    if hasattr(
                        self._viewer_factory, "allow_unsupported_extensions"
                    ) and getattr(self._viewer_factory, "allow_unsupported_extensions"):
                        file_filter += " ;; All Files (*)"
                file_paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
                    self, "Open File", "", file_filter
                )
                for i, file_path in enumerate(file_paths):
                    if file_path:
                        with open(file_path, "rb") as file:
                            data = file.read()
                            filename = os.path.basename(file_path)
                            file = SimpleFile(filename, data)
                            self.load_file(file, i == 0)

            open_file_action.triggered.connect(open_file_dialog)

            close_all_action = menu.addAction("Close All")
            close_all_action.setIcon(
                self.style().standardIcon(
                    QtWidgets.QStyle.StandardPixmap.SP_DockWidgetCloseButton
                )
            )
            close_all_action.triggered.connect(
                lambda: (
                    self.tab_widget.clear(),
                    self.setWindowTitle(self._viewer_name),
                    self.tab_widget.setVisible(False),
                    self.no_tab_label.setVisible(True),
                )
            )

            return menu

        self.menuBar().addMenu(file_menu())

        if issubclass(self._viewer_factory, ICustomTabWindow):
            self._viewer_factory.setup_tab_window(self)

    def _close_tab(self, index: int):
        """Release the viewer resources before dropping its tab."""
        widget = self.tab_widget.widget(index)
        self.tab_widget.removeTab(index)
        if widget is not None:
            if isinstance(widget, Viewer):
                widget.unload_file()
            widget.deleteLater()
        if self.tab_widget.count() == 0:
            self.setWindowTitle(self._viewer_name)
            self.tab_widget.setVisible(False)
            self.no_tab_label.setVisible(True)

    def load_file(self, file: IFile, take_focus=True):
        """
        Load a file.

        :param data: The file data to load.
        :param extension: The file extension.
        """
        viewer = self._viewer_factory()
        try:
            viewer.set_file(file)
        except ValueError as e:
            QtWidgets.QMessageBox.critical(self, "Error", str(e))
            return
        idx = self.tab_widget.addTab(viewer, file.name)
        if take_focus:
            self.tab_widget.setCurrentIndex(idx)
        self.no_tab_label.setVisible(False)
        self.tab_widget.setVisible(True)


class ICustomTabWindow:
    """Interface for viewers that need custom tab window setup."""

    @staticmethod
    def setup_tab_window(window: ViewerTabWindow) -> None:
        """Setup custom tab window features.

        Args:
            window: The tab window to setup

        Raises:
            NotImplementedError: If subclass doesn't implement this method
        """
        raise NotImplementedError("Subclasses must implement setup_tab_window()")
