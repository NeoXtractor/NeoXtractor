import os

from PySide6 import QtCore, QtWidgets

from core.file import IFile, SimpleFile
from core.wem.bnk import BnkContainer
from gui.widgets.viewer import Viewer
from gui.widgets.viewers.audio_viewer import AudioViewer
from gui.windows.viewer_tab_window import ViewerTabWindow


class BnkViewer(Viewer):
    """Widget that displays a BNK file and allows saving WEM files."""

    name = "BNK Viewer"
    accepted_extensions = {"bnk"}

    def __init__(self):
        super().__init__()

        self._file: IFile | None = None
        self._wem_window: ViewerTabWindow | None = None

        # self.setWindowTitle("BNK Viewer")
        # self.resize(400, 300)
        self.container = None

        self.v_layout = QtWidgets.QVBoxLayout(self)
        self.list_widget = QtWidgets.QListWidget()
        self.msg_box = QtWidgets.QLabel(self)
        self.msg_box.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.msg_box.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Minimum
        )
        self.top_frame = QtWidgets.QFrame()
        hlayout = QtWidgets.QHBoxLayout()

        self.save_all_button = QtWidgets.QPushButton("Save All WEM Files")
        self.save_all_button.clicked.connect(self.save_all_wem_files)
        hlayout.addWidget(self.msg_box)
        hlayout.addWidget(self.save_all_button)
        self.v_layout.addLayout(hlayout)
        self.v_layout.addWidget(self.list_widget)

        self.list_widget.itemDoubleClicked.connect(self.open_wem_file)
        self.list_widget.setContextMenuPolicy(
            QtCore.Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.list_widget.customContextMenuRequested.connect(self.show_context_menu)

    def read_bnk(self, data: bytes, extension: str):
        """Read a BNK file and populate the list widget with WEM files."""
        self.clear_all()

        if extension.lower() not in self.accepted_extensions:
            raise ValueError(f"Unsupported file extension: {extension}")

        self.container = BnkContainer(data)

        if self.container.files:
            self.msg_box.setText(f"{len(self.container.files)} WEM files.")
            self.populate_list()
        else:
            self.msg_box.setText("No WEM files found in the BNK container.")

    def set_file(self, file: IFile) -> None:
        self._file = file
        self.read_bnk(file.data, file.extension)

    def get_file(self) -> IFile | None:
        return self._file

    def unload_file(self):
        self._file = None
        self.clear_all()

    def clear_all(self):
        """Clear the list widget and reset the container."""
        if hasattr(self, "container") and self.container:
            self.container = None

        self.list_widget.clear()
        self.msg_box.setText("No BNK file loaded.")

    def populate_list(self):
        """Populate the list widget with WEM file names from the container."""
        self.list_widget.clear()
        if self.container:
            for fname in self.container.list_files():
                self.list_widget.addItem(fname)

    def _get_wem_content(self, item: QtWidgets.QListWidgetItem) -> bytes | None:
        if not self.container:
            return None
        return self.container.get_file_content(item.text())

    def open_wem_file(self, item: QtWidgets.QListWidgetItem):
        """Open the selected WEM in the audio viewer on double-click."""
        content = self._get_wem_content(item)
        if content is None:
            QtWidgets.QMessageBox.critical(
                self, "Not found", "File content not found."
            )
            return

        if self._wem_window is None:
            self._wem_window = ViewerTabWindow(AudioViewer, self.window())

        self._wem_window.load_file(SimpleFile(item.text() + ".wem", content))
        self._wem_window.show()
        self._wem_window.raise_()
        self._wem_window.activateWindow()

    def show_context_menu(self, position: QtCore.QPoint):
        """Offer saving the WEM under the cursor."""
        item = self.list_widget.itemAt(position)
        if item is None:
            return

        menu = QtWidgets.QMenu(self)
        open_action = menu.addAction("Open in Audio Viewer")
        open_action.triggered.connect(lambda: self.open_wem_file(item))
        save_action = menu.addAction("Save WEM File...")
        save_action.triggered.connect(lambda: self.save_wem_file(item))
        menu.exec(self.list_widget.viewport().mapToGlobal(position))

    def save_wem_file(self, item):
        """Save a WEM file to disk."""
        if self.container:
            content = self.container.get_file_content(item.text())
            if content is not None:
                path = QtWidgets.QFileDialog.getSaveFileName(
                    self, "Save WEM File", item.text() + ".wem", "WEM Files (*.wem)"
                )[0]
                if not path:
                    return
                with open(path, "wb") as handle:
                    handle.write(content)
                self.msg_box.setText(f"File '{item.text()}.wem' saved successfully.")
            else:
                QtWidgets.QMessageBox.critical(
                    self, "Not found", "File content not found."
                )

    def save_all_wem_files(self):
        """Save all WEM files to a selected directory."""
        if self.container:
            x = QtWidgets.QFileDialog.getExistingDirectory(
                self, "Select save directory"
            )
            if not x:
                return
            for fname in self.container.list_files():
                content = self.container.get_file_content(fname)
                if content is not None:
                    with open(os.path.join(x, fname + ".wem"), "wb") as handle:
                        handle.write(content)
                else:
                    QtWidgets.QMessageBox.critical(
                        self, "Not found", f"File content for '{fname}' not found."
                    )
            QtWidgets.QMessageBox.information(
                self, "Success", "All WEM files saved successfully."
            )
        else:
            self.msg_box.setText("Cannot save files, no BNK file loaded.")
