"""HexViewer widget: the declared binary fallback.

Shows an entry's decoded bytes with a raw/decoded toggle.  Stored bytes (no
decryption, no decompression) arrive through the source's neutral ``raw()``
face, so the viewer never names a format or an archive.
"""

from PySide6 import QtCore, QtGui, QtWidgets

from neoxtractor.gui.theme.theme_manager import ThemeManager
from neoxtractor.gui.viewers.base import ViewerBase, ViewSource

from .data_inspector import DATA_INSPECTOR_TYPES
from .hex_area import HexArea, HexAreaColors


class HexViewer(ViewerBase):
    """Main widget for the Hex Viewer."""

    name = "Hex Viewer"

    _data_inspector_labels: dict[str, QtWidgets.QLabel] = {}
    _data_inspector: QtWidgets.QWidget

    def __init__(self, parent=None):
        super().__init__(parent)

        self._decoded_bytes: bytes = b""
        self._raw_bytes: bytes | None = None
        self._showing_raw = False

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

        # Raw / decoded view toggle (archive sources only)
        self._raw_action = QtGui.QAction("Show Raw", self)
        self._raw_action.setCheckable(True)
        self._raw_action.setEnabled(False)

        def toggle_raw_view():
            self._showing_raw = self._raw_action.isChecked()
            self._apply_view()

        self._raw_action.toggled.connect(toggle_raw_view)
        self._toolbar.addAction(self._raw_action)

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

        def on_cursor_position_changed():
            self._cursor_location.setText(
                f"Line: {self.area.cursor_pos[0]}, Column: {self.area.cursor_pos[1]}"
            )
            self._update_data_inspector()

        self.area.cursorPositionChanged.connect(on_cursor_position_changed)

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

    def load(self, source: ViewSource) -> None:
        """Show the source's decoded bytes; raw stays one toggle away."""
        super().load(source)
        self._decoded_bytes = source.content()
        self._raw_bytes = None
        self._showing_raw = False
        self._raw_action.blockSignals(True)
        self._raw_action.setChecked(False)
        self._raw_action.blockSignals(False)
        self._raw_action.setEnabled(source.raw_available)
        self.set_data(bytearray(self._decoded_bytes))

    def _apply_view(self) -> None:
        """Switch the display between decoded and stored bytes."""
        if self._showing_raw and self._raw_bytes is None:
            if self.source is not None and self.source.raw_available:
                self._raw_bytes = self.source.raw()
            else:
                self._showing_raw = False
        data = self._raw_bytes if self._showing_raw else self._decoded_bytes
        self.set_data(bytearray(data or b""))

    def unload(self) -> None:
        super().unload()
        self._decoded_bytes = b""
        self._raw_bytes = None
        self._showing_raw = False
        self._raw_action.setEnabled(False)
        self.set_data(bytearray())


# Contract members read by the viewers registry.  The dispatcher references
# this module directly; every otherwise-unclaimed binary entry lands here.
NAME = HexViewer.name
VIEWER_CLASS = HexViewer
ACCEPTED_EXTENSIONS: frozenset[str] = frozenset()
INPUT = "stream"
FALLBACK = "binary"
PRIORITY = 0
