"""
Text over a 3D view in the style of NeoXtractor's mesh viewer (gui/widgets/viewers/mesh_viewer/render_widget.py):
information rows at the top left, the camera keys at the bottom left (Ctrl shows each key's other view), Roboto at
14 px with labels in (0.5, 1.0, 1.0) at x 20 and values in white at x 90, 20 px from the edges. Used by the
Animations player and the Scene Viewer.
"""

from __future__ import annotations

import os

from PySide6 import QtCore, QtGui, QtWidgets

from core.logger import get_logger

MARGIN = 20
LABEL_WIDTH = 70
LABEL_COLOR = "#80ffff"
INSTRUCTIONS = [
    ("Key F", "Focus Object"),
    ("M-Right", "Orbit"),
    ("M-Left", "Pan"),
    ("M-Middle", "Dolly"),
    ("W, A, S, D", "Move Camera"),
    ("Shift", "Sprint"),
    ("Key 1", ("Front View", "Back View")),
    ("Key 3", ("Right View", "Left View")),
    ("Key 7", ("Top View", "Bottom View")),
    ("Ctrl", "Alternative Actions"),
]
_FONT: list = []


def overlay_font() -> QtGui.QFont:
    """NeoXtractor's viewer font (data/fonts/Roboto-Regular.ttf) at 14 px."""
    if not _FONT:
        family = None
        try:
            from core.utils import get_application_path

            font_id = QtGui.QFontDatabase.addApplicationFont(
                os.path.join(str(get_application_path()), "data", "fonts", "Roboto-Regular.ttf")
            )
            families = QtGui.QFontDatabase.applicationFontFamilies(font_id) if font_id >= 0 else []
            family = families[0] if families else None
        except Exception:
            get_logger().debug("Could not load the viewer font", exc_info=True)
        font = QtGui.QFont(family) if family else QtGui.QFont()
        font.setPixelSize(14)
        _FONT.append(font)
    return QtGui.QFont(_FONT[0])


class InfoTable(QtWidgets.QWidget):
    """Rows of "Label:" and a value drawn over the view; clicks pass through to the view."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        self.grid = QtWidgets.QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(0)
        self.grid.setVerticalSpacing(0)
        self.grid.setColumnMinimumWidth(0, LABEL_WIDTH)
        self.grid.setSizeConstraint(QtWidgets.QLayout.SizeConstraint.SetFixedSize)  # always as big as its rows

    def set_rows(self, rows):
        while self.grid.count():
            widget = self.grid.takeAt(0).widget()
            if widget is not None:
                widget.deleteLater()
        for row, (label, value) in enumerate(rows):
            for column, (text, color) in enumerate(((f"{label}:", LABEL_COLOR), (str(value), "#ffffff"))):
                cell = QtWidgets.QLabel(text)
                cell.setFont(overlay_font())
                cell.setStyleSheet(f"color: {color}; background: transparent;")
                self.grid.addWidget(cell, row, column)
        self.setVisible(bool(rows))


class ViewOverlays(QtCore.QObject):
    """The info rows and the camera keys over `view`, kept in place as it resizes; `corner` goes top right."""

    def __init__(self, view: QtWidgets.QWidget, corner: QtWidgets.QWidget | None = None):
        super().__init__(view)
        self.view, self.corner = view, corner
        self.info = InfoTable(view)
        self.info.hide()
        self.keys = InfoTable(view)
        self._alternative = False
        self._show_keys()
        view.installEventFilter(self)

    def set_info(self, rows):
        self.info.set_rows(rows)
        self.place()

    def _show_keys(self):
        self.keys.set_rows(
            [
                (key, action[1 if self._alternative else 0] if isinstance(action, tuple) else action)
                for key, action in INSTRUCTIONS
            ]
        )
        self.place()

    def place(self):
        width, height = self.view.width(), self.view.height()
        self.info.move(MARGIN, MARGIN)
        self.keys.move(MARGIN, height - self.keys.height() - MARGIN)
        widgets = [self.info, self.keys]
        if self.corner is not None:
            self.corner.adjustSize()
            self.corner.move(width - self.corner.width() - MARGIN, MARGIN)
            widgets.append(self.corner)
        for widget in widgets:
            widget.raise_()

    def eventFilter(self, obj, event):
        if obj is self.view:
            kind = event.type()
            if kind in (QtCore.QEvent.Type.Resize, QtCore.QEvent.Type.Show):
                self.place()
            elif (
                kind in (QtCore.QEvent.Type.KeyPress, QtCore.QEvent.Type.KeyRelease)
                and event.key() == QtCore.Qt.Key.Key_Control
            ):
                alternative = kind == QtCore.QEvent.Type.KeyPress  # Ctrl shows the other view of each key
                if alternative != self._alternative:
                    self._alternative = alternative
                    self._show_keys()
        return False
