"""Schema-driven read-options form shared by the config dialogs.

Lists the registered formats (packages registry) and builds each one's
widgets from :func:`config_schema.options_fields` -- no format vocabulary
is hard-coded.  ``read_options`` is a per-format dict, so switching
formats preserves the other formats' values in an internal cache.
Integer fields carry an enable checkbox: unchecked means ``None``
(auto/unset), checked the spin value -- 0 and negatives stay usable
rather than overloading 0 as a sentinel.
"""

from __future__ import annotations

from typing import Any

from PySide6 import QtWidgets

from neoxtractor.core.packages import registry as package_registry
from neoxtractor.core.packages.config_schema import options_fields

INT_MIN = -(2**31)
INT_MAX = 2**31 - 1


class _IntRow(QtWidgets.QWidget):
    """An enable checkbox plus a spin box; disabled means ``None``."""

    def __init__(
        self, default: int | None, parent: QtWidgets.QWidget | None = None
    ) -> None:
        super().__init__(parent)
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._check = QtWidgets.QCheckBox("set")
        self._spin = QtWidgets.QSpinBox()
        self._spin.setRange(INT_MIN, INT_MAX)
        layout.addWidget(self._check)
        layout.addWidget(self._spin)
        layout.addStretch()

        self._check.toggled.connect(self._spin.setEnabled)
        if default is None:
            self._check.setChecked(False)
            self._spin.setEnabled(False)
        else:
            self._check.setChecked(True)
            self._spin.setEnabled(True)
            self._spin.setValue(int(default))

    def value(self) -> int | None:
        return self._spin.value() if self._check.isChecked() else None

    def set_value(self, value: int | None) -> None:
        if value is None:
            self._check.setChecked(False)
        else:
            self._check.setChecked(True)
            self._spin.setValue(int(value))


class OptionsForm(QtWidgets.QWidget):
    """Format selector + generated option fields for one config's read_options."""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._values: dict[str, dict[str, Any]] = {}
        self._widgets: dict[str, Any] = {}
        # The format whose widgets are on screen.  Store and load key off
        # this, never the combo's currentData(): when currentIndexChanged
        # fires, currentData() already returns the NEW format while
        # _widgets still holds the OLD one's fields.
        self._shown_fmt: str | None = None

        self._outer = QtWidgets.QVBoxLayout(self)
        self._outer.setContentsMargins(0, 0, 0, 0)

        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Format:"))
        self.format_combo = QtWidgets.QComboBox()
        for info in package_registry.list_sources():
            self.format_combo.addItem(info.format, info.format)
        row.addWidget(self.format_combo)
        row.addStretch()
        self._outer.addLayout(row)

        self._fields = QtWidgets.QWidget()
        self._fields_layout = QtWidgets.QFormLayout(self._fields)
        self._outer.addWidget(self._fields)

        self.format_combo.currentIndexChanged.connect(self._on_format_changed)
        if self.format_combo.count():
            self._rebuild(self.format_combo.currentData())

    # --- format handling ----------------------------------------------------

    def _on_format_changed(self, index: int) -> None:
        del index
        self._store_current()
        self._rebuild(self.format_combo.currentData())
        self._load_current()

    def _rebuild(self, fmt: str | None) -> None:
        while self._fields_layout.rowCount():
            self._fields_layout.removeRow(0)
        self._widgets = {}
        self._shown_fmt = fmt
        if not fmt:
            return
        for name, kind, default in options_fields(fmt):
            widget: QtWidgets.QWidget
            if kind == "bool":
                check = QtWidgets.QCheckBox()
                check.setChecked(bool(default))
                widget = check
            elif kind == "int":
                widget = _IntRow(default)
            else:
                edit = QtWidgets.QLineEdit("" if default is None else str(default))
                widget = edit
            self._fields_layout.addRow(name, widget)
            self._widgets[name] = (kind, widget)

    def _store_current(self) -> None:
        if self._shown_fmt is None:
            return
        self._values[self._shown_fmt] = self._read_widgets()

    def _load_current(self) -> None:
        raw = self._values.get(self._shown_fmt, {}) if self._shown_fmt else {}
        self._write_widgets(raw)

    # --- widget <-> dict ----------------------------------------------------

    def _read_widgets(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for name, (kind, widget) in self._widgets.items():
            if kind == "bool":
                out[name] = widget.isChecked()
            elif kind == "int":
                out[name] = widget.value()
            else:
                text = widget.text().strip()
                out[name] = text if text else None
        return out

    def _write_widgets(self, raw: dict[str, Any]) -> None:
        for name, (kind, widget) in self._widgets.items():
            if name not in raw:
                continue
            value = raw[name]
            if kind == "bool":
                widget.setChecked(bool(value))
            elif kind == "int":
                widget.set_value(value)
            elif isinstance(widget, QtWidgets.QLineEdit):
                widget.setText("" if value is None else str(value))

    # --- public API ---------------------------------------------------------

    def set_read_options(self, read_options: dict[str, dict[str, Any]]) -> None:
        """Seed the cache from a config's per-format dict and show the first."""
        self._values = {fmt: dict(raw) for fmt, raw in read_options.items()}
        # Prefer a format that actually has stored values as the initial view.
        first = next(
            (i for i in range(self.format_combo.count())
             if self.format_combo.itemData(i) in self._values),
            0,
        )
        # Move the combo without firing _on_format_changed: that slot
        # would store the widgets on screen over the freshly seeded cache.
        self.format_combo.blockSignals(True)
        self.format_combo.setCurrentIndex(first)
        self.format_combo.blockSignals(False)
        self._rebuild(self.format_combo.currentData())
        self._load_current()

    def get_read_options(self) -> dict[str, dict[str, Any]]:
        """Merge the live widgets back and return non-empty per-format dicts."""
        self._store_current()
        return {fmt: raw for fmt, raw in self._values.items() if raw}
