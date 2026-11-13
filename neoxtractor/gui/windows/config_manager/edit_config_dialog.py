"""Dialog for editing an existing game configuration.

Read options are schema-driven (:class:`OptionsForm`); the signature name map
keeps its dedicated table (its "0x<hex>" keys are not a generated field).
"""

from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from neoxtractor.gui.config import Config
from neoxtractor.gui.windows.config_manager.options_form import OptionsForm

# Semi-transparent red: stays readable on light and dark themes.
_DUP_BRUSH = QBrush(QColor(255, 80, 80, 90))


class EditConfigDialog(QDialog):
    """Dialog for editing an existing game configuration."""

    def __init__(self, config: Config, parent=None):
        """Initialize the edit config dialog.

        Args:
            config: The config to edit
            parent: Parent widget
        """
        super().__init__(parent)
        self.config = config
        self.setWindowTitle(f"Edit Config: {config.name}")
        self.setMinimumWidth(500)
        self.setMinimumHeight(400)

        self.main_layout = QVBoxLayout(self)
        self.form_layout = QFormLayout()
        self.main_layout.addLayout(self.form_layout)

        self.name_edit = QLineEdit()
        self.name_edit.setText(config.name)
        self.form_layout.addRow("Config Name:", self.name_edit)

        self.options_form = OptionsForm(self)
        self.options_form.set_read_options(config.read_options)
        self.main_layout.addWidget(self.options_form)

        self.main_layout.addWidget(QLabel("Entry Signature Name Map:"))
        self.map_table = QTableWidget()
        self.map_table.setColumnCount(2)
        self.map_table.setHorizontalHeaderLabels(["Signature", "File Name"])
        self.map_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.main_layout.addWidget(self.map_table)
        self._populate_table()

        self._dup_label = QLabel()
        self._dup_label.setStyleSheet("color: red;")
        self._dup_label.setWordWrap(True)
        self._dup_label.setVisible(False)
        self.main_layout.addWidget(self._dup_label)

        # Connect after populate: seeding the table must not scan row by row.
        self.map_table.cellChanged.connect(self._scan_duplicates)

        table_buttons_layout = QHBoxLayout()
        self.add_row_button = QPushButton("Add Row")
        self.add_row_button.clicked.connect(self._add_row)
        table_buttons_layout.addWidget(self.add_row_button)
        self.delete_row_button = QPushButton("Delete Selected Row")
        self.delete_row_button.clicked.connect(self._delete_selected_row)
        table_buttons_layout.addWidget(self.delete_row_button)
        table_buttons_layout.addStretch()
        self.main_layout.addLayout(table_buttons_layout)

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )

        def validate_and_accept():
            """Validate inputs before accepting the dialog."""
            if not self.name_edit.text().strip():
                self.name_edit.setStyleSheet("border: 1px solid red;")
                self.name_edit.setFocus()
                return
            self.accept()

        self.button_box.accepted.connect(validate_and_accept)
        self.button_box.rejected.connect(self.reject)
        self.main_layout.addWidget(self.button_box)

    def _populate_table(self) -> None:
        """Populate the table with existing entry signature name map data."""
        self.map_table.setRowCount(len(self.config.entry_signature_name_map))
        for row, (signature, name) in enumerate(
            self.config.entry_signature_name_map.items()
        ):
            self.map_table.setItem(row, 0, QTableWidgetItem(str(signature)))
            self.map_table.setItem(row, 1, QTableWidgetItem(str(name)))

    def _add_row(self) -> None:
        """Add a new empty row to the table."""
        row_count = self.map_table.rowCount()
        self.map_table.insertRow(row_count)
        self.map_table.setItem(row_count, 0, QTableWidgetItem(""))
        self.map_table.setItem(row_count, 1, QTableWidgetItem(""))
        self.map_table.setCurrentCell(row_count, 0)

    def _delete_selected_row(self) -> None:
        """Delete the currently selected row."""
        current_row = self.map_table.currentRow()
        if current_row >= 0:
            # removeRow emits no cellChanged, so re-scan to clear stale marks.
            self.map_table.removeRow(current_row)
            self._scan_duplicates()

    def _scan_duplicates(self) -> None:
        """Mark every row whose signature repeats; last write still wins.

        Duplicates do not block saving -- the collected dict keeps the
        final row per signature -- but earlier rows would vanish silently,
        so each duplicate group is painted and a note states last-wins.
        Idempotent full-table scan; empty signatures do not participate.
        """
        rows_by_sig: dict[str, list[int]] = {}
        for row in range(self.map_table.rowCount()):
            item = self.map_table.item(row, 0)
            signature = item.text().strip() if item else ""
            if signature:
                rows_by_sig.setdefault(signature, []).append(row)
        dup_rows = {
            r for rows in rows_by_sig.values() if len(rows) > 1 for r in rows
        }
        for row in range(self.map_table.rowCount()):
            for col in range(self.map_table.columnCount()):
                item = self.map_table.item(row, col)
                if item is not None:
                    item.setBackground(_DUP_BRUSH if row in dup_rows else QBrush())
        dups = sorted(sig for sig, rows in rows_by_sig.items() if len(rows) > 1)
        if dups:
            self._dup_label.setText(
                "Duplicate signature(s): "
                + ", ".join(dups)
                + " -- on save only the last row of each takes effect."
            )
            self._dup_label.setVisible(True)
        else:
            self._dup_label.setVisible(False)

    def get_config(self) -> Config:
        """Get the edited config.

        Returns:
            Config: A new config instance with the edited values
        """
        entry_signature_name_map: dict[str, str] = {}
        for row in range(self.map_table.rowCount()):
            signature_item = self.map_table.item(row, 0)
            name_item = self.map_table.item(row, 1)
            if signature_item and name_item:
                signature = signature_item.text().strip()
                name = name_item.text().strip()
                if signature and name:
                    entry_signature_name_map[signature] = name

        return Config(
            name=self.name_edit.text().strip(),
            read_options=self.options_form.get_read_options(),
            entry_signature_name_map=entry_signature_name_map,
        )
