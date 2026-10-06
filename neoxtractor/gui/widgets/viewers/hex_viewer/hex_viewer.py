"""Provides HexViewer widget."""

from PySide6 import QtCore, QtGui, QtWidgets

from neoxtractor.core.file import IFile
from neoxtractor.gui.theme.theme_manager import ThemeManager
from neoxtractor.gui.widgets.viewer import Viewer

from .data_inspector import DATA_INSPECTOR_TYPES
from .hex_area import HexArea, HexAreaColors


class HexViewer(Viewer):
    """Main widget for the Hex Viewer."""

    name = "Hex Viewer"
    allow_unsupported_extensions = True

    _data_inspector_labels: dict[str, QtWidgets.QLabel] = {}

    def __init__(self, parent=None):
        super().__init__(parent)

        self._file: IFile | None = None

        self.area = HexArea(self)

        layout = QtWidgets.QVBoxLayout(self)

        self._toolbar = QtWidgets.QToolBar(self)

        # ASCII view toggle
        self._ascii_action = QtGui.QAction("ASCII View", self)
        self._ascii_action.setCheckable(True)
        self._ascii_action.setChecked(self.area.show_ascii)

        def toggle_ascii_view():
            self.area.show_ascii = self._ascii_action.isChecked()

        self._ascii_action.toggled.connect(toggle_ascii_view)
        self._toolbar.addAction(self._ascii_action)

        self._toolbar.addSeparator()

        # Data Inspector area
        self._data_inspector_action = QtGui.QAction("Data Inspector", self)
        self._data_inspector_action.setCheckable(True)
        self._data_inspector_action.setChecked(True)
        self._data_inspector_action.toggled.connect(
            lambda: self._data_inspector.setVisible(
                self._data_inspector_action.isChecked()
            )
        )
        self._toolbar.addAction(self._data_inspector_action)

        self._toolbar.addSeparator()

        # Address base selector
        self._addr_base_label = QtWidgets.QLabel("Address Base:")
        self._toolbar.addWidget(self._addr_base_label)

        self._addr_base_combo = QtWidgets.QComboBox()
        self._addr_base_combo.addItems(["Hexadecimal", "Decimal", "Octal"])
        self._addr_base_combo.setCurrentIndex(0)

        def update_addressing_base(index):
            if index == 0:
                self.area.addressing_base = 16
            elif index == 1:
                self.area.addressing_base = 10
            else:
                self.area.addressing_base = 8

        self._addr_base_combo.currentIndexChanged.connect(update_addressing_base)
        self._toolbar.addWidget(self._addr_base_combo)

        self._toolbar.addSeparator()

        # Bytes per line selector
        self._bytes_per_line_label = QtWidgets.QLabel("Bytes per Line:")
        self._toolbar.addWidget(self._bytes_per_line_label)

        self._bytes_per_line_combo = QtWidgets.QComboBox()
        self._bytes_per_line_combo.addItems(["8", "16", "32", "64"])
        self._bytes_per_line_combo.setCurrentIndex(1)

        def update_bytes_per_line(index):
            self.area.bytes_per_line = int(self._bytes_per_line_combo.currentText())
            self.area.update()

        self._bytes_per_line_combo.currentIndexChanged.connect(update_bytes_per_line)
        self._toolbar.addWidget(self._bytes_per_line_combo)

        self._status_bar_layout = QtWidgets.QHBoxLayout()

        self._cursor_location = QtWidgets.QLabel("Line: 1, Column: 1")
        self._status_bar_layout.addWidget(self._cursor_location)

        self._status_bar_layout.addStretch()

        self._total_bytes_label = QtWidgets.QLabel("0 bytes in total")
        self._status_bar_layout.addWidget(self._total_bytes_label)

        center_layout = QtWidgets.QHBoxLayout()

        self._data_inspector = QtWidgets.QWidget(self)
        self._data_inspector_layout = QtWidgets.QVBoxLayout(self._data_inspector)
        self._data_inspector_layout.setAlignment(QtCore.Qt.AlignmentFlag.AlignTop)

        data_inspector_label = QtWidgets.QLabel("Data Inspector")
        data_inspector_label.setStyleSheet("font-weight: bold;")
        self._data_inspector_layout.addWidget(data_inspector_label)

        value_font = QtGui.QFont("Space Mono")

        for name in DATA_INSPECTOR_TYPES:
            name_layout = QtWidgets.QHBoxLayout()
            name_label = QtWidgets.QLabel(name + ": ")
            name_layout.addWidget(name_label)
            value_label = QtWidgets.QLabel()
            value_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
            value_label.setFont(value_font)
            name_layout.addWidget(value_label)
            self._data_inspector_labels[name] = value_label
            self._data_inspector_layout.addLayout(name_layout)

        self._data_inspector_little_endian = QtWidgets.QCheckBox("Little Endian")
        self._data_inspector_little_endian.setChecked(True)
        self._data_inspector_little_endian.checkStateChanged.connect(
            self._update_data_inspector
        )
        self._data_inspector_layout.addWidget(self._data_inspector_little_endian)

        center_layout.addWidget(self.area, stretch=1)
        center_layout.addSpacing(5)
        center_layout.addWidget(self._data_inspector)

        layout.addWidget(self._toolbar)
        layout.addLayout(center_layout, stretch=1)
        layout.addLayout(self._status_bar_layout)

        self.area.cursorPositionChanged.connect(
            lambda: (
                self._cursor_location.setText(
                    f"Line: {self.area.cursor_pos[0]}, Column: {self.area.cursor_pos[1]}"
                ),
                self._update_data_inspector(),
            )
        )

        self._theme_manager = ThemeManager.instance()
        self._theme_manager.theme_changed.connect(self._update_theme)
        self._update_theme()

    def _update_theme(self):
        """Update the widget's theme based on the current theme."""
        color = HexAreaColors()

        color_mapping = {
            "hex_viewer.header_color_begin": "header_color_begin",
            "hex_viewer.header_color_end": "header_color_end",
            "hex_viewer.header_separator_color": "header_separator_color",
            "hex_viewer.header_text_color": "header_text_color",
            "hex_viewer.address_color": "address_color",
            "hex_viewer.hex_color": "hex_color",
            "hex_viewer.ascii_color": "ascii_color",
            "hex_viewer.highlight_bg_color": "highlight_bg_color",
            "hex_viewer.highlight_fg_color": "highlight_fg_color",
            "hex_viewer.selection_bg_color": "selection_bg_color",
            "hex_viewer.selection_fg_color": "selection_fg_color",
            "hex_viewer.row_color": "row_color",
            "hex_viewer.alternate_row_color": "alternate_row_color",
        }
        for key, attr in color_mapping.items():
            clr = self._theme_manager.get_color(key)
            if clr is None:
                continue
            setattr(color, attr, QtGui.QColor(clr))
        self.area.colors = color

    def _update_data_inspector(self):
        """Update the data inspector labels based on the current cursor position."""

        if not self._data_inspector.isVisible():
            return

        if len(self.area.data) == 0:
            for label in self._data_inspector_labels.values():
                label.setText("")
            return

        little_endian = self._data_inspector_little_endian.isChecked()

        for name, label in self._data_inspector_labels.items():
            value = DATA_INSPECTOR_TYPES[name](
                self.area.data, self.area.cursor_byte_pos, little_endian
            )
            if value is not None:
                label.setText(str(value))
            else:
                label.setText("")

    def set_data(self, data: bytearray):
        """Set the data to be displayed in the hex viewer."""
        self._total_bytes_label.setText(f"{len(data)} bytes in total")
        self.area.data = data
        self._update_data_inspector()

    def set_file(self, file: IFile):
        self._file = file
        self.set_data(bytearray(file.data))

    def get_file(self) -> IFile | None:
        return self._file

    def unload_file(self):
        self._file = None
        self.set_data(bytearray())
