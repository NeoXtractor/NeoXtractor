from bitstring import ConstBitStream
from PySide6 import QtCore, QtWidgets

from neoxtractor.core.file import IFile
from neoxtractor.gui.widgets.viewer import Viewer


class BNKExtractor:
    """A class to extract WEM files from a BNK file."""

    files = []

    def __init__(self, data: bytes):
        self.files.clear()
        try:
            f = ConstBitStream(data)
            # BNKH
            f.pos = 32
            f.pos += int(f.read("uintle:32")) * 8 + 32

            extract_list = []

            # THIS ONE HAS WEM FILES
            if f.read("bytes:4") == b"DIDX":
                didx_len = f.read("uintle32")
                files = didx_len // 12
                for x in range(files):
                    extract_list.append(f.readlist("3*uintle32"))

                # DATA
                f.pos += 64
                start_of_data = f.pos

                for id, offset, len in extract_list:
                    f.pos = start_of_data + (offset * 8)
                    self.files.append((str(id), f.read(f"bytes{len}")))
            else:
                self.clear_files()

        except Exception:
            self.clear_files()

    def clear_files(self):
        """Clear the list of files."""
        self.files.clear()

    def list_files(self):
        """List the IDs of the files in the BNK."""
        return [_id for _id, _ in self.files]

    def get_file_content(self, id):
        """Get the content of a file by its ID."""
        for _id, content in self.files:
            if _id == id:
                return content
        return None


class BnkViewer(Viewer):
    """Widget that displays a BNK file and allows saving WEM files."""

    name = "BNK Viewer"
    accepted_extensions = {"bnk"}

    def __init__(self):
        super().__init__()

        self._file: IFile | None = None

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

        self.list_widget.itemDoubleClicked.connect(self.save_wem_file)

    def read_bnk(self, data: bytes, extension: str):
        """Read a BNK file and populate the list widget with WEM files."""
        self.clear_all()

        if extension.lower() not in self.accepted_extensions:
            raise ValueError(f"Unsupported file extension: {extension}")

        self.container = BNKExtractor(data)

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

    def save_wem_file(self, item):
        """Save a WEM file to disk when an item is double-clicked."""
        if self.container:
            content = self.container.get_file_content(item.text())
            if content is not None:
                open(
                    QtWidgets.QFileDialog.getSaveFileName(
                        self, "Save WEM File", item.text() + ".wem", "WEM Files (*.wem)"
                    )[0],
                    "wb",
                ).write(content)
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
            for fname in self.container.list_files():
                content = self.container.get_file_content(fname)
                if content is not None:
                    open(x + "\\" + fname + ".wem", "wb").write(content)
                else:
                    QtWidgets.QMessageBox.critical(
                        self, "Not found", f"File content for '{fname}' not found."
                    )
            QtWidgets.QMessageBox.information(
                self, "Success", "All WEM files saved successfully."
            )
        else:
            self.msg_box.setText("Cannot save files, no BNK file loaded.")
