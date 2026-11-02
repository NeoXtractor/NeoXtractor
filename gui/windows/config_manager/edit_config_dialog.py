"""Dialog for editing an existing game configuration."""

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from core.config import Config
from core.npk.class_types import NPKReadOptions


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

        # Create layout
        self.main_layout = QVBoxLayout(self)
        self.form_layout = QFormLayout()
        self.main_layout.addLayout(self.form_layout)

        # Config name field
        self.name_edit = QLineEdit()
        self.name_edit.setText(config.name)
        self.form_layout.addRow("Config Name:", self.name_edit)

        # Info size field
        self.info_size_edit = QSpinBox()
        self.info_size_edit.setMinimum(0)
        self.info_size_edit.setMaximum(999999)
        if config.read_options is None or config.read_options.info_size is None:
            self.info_size_edit.setValue(0)
        else:
            self.info_size_edit.setValue(config.read_options.info_size)
        self.form_layout.addRow(
            "Info Size (0 for auto determine):", self.info_size_edit
        )

        # Decryption key field
        self.key_edit = QSpinBox()
        self.key_edit.setMinimum(-2147483648)  # int32 min
        self.key_edit.setMaximum(2147483647)  # int32 max
        if config.read_options is None or config.read_options.decryption_key is None:
            self.key_edit.setValue(0)
        else:
            self.key_edit.setValue(config.read_options.decryption_key)
        self.form_layout.addRow("Decryption Key (Use 0 for no key):", self.key_edit)

        # Identity V special mode
        self.wwise_check = QCheckBox(
            "Identity V special mode (dual IDX merge, path restore, bank expansion, Wwise event names)"
        )
        self.wwise_check.setChecked(bool(config.wwise_event_map))
        self.form_layout.addRow("", self.wwise_check)

        # Entry signature name map section
        map_label = QLabel("Entry Signature Name Map:")
        self.main_layout.addWidget(map_label)

        # Table for entry signature name map
        self.map_table = QTableWidget()
        self.map_table.setColumnCount(2)
        self.map_table.setHorizontalHeaderLabels(["Signature", "File Name"])
        self.map_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.main_layout.addWidget(self.map_table)

        # Populate table with existing data
        self._populate_table()

        # Buttons for table operations
        table_buttons_layout = QHBoxLayout()

        self.add_row_button = QPushButton("Add Row")
        self.add_row_button.clicked.connect(self._add_row)
        table_buttons_layout.addWidget(self.add_row_button)

        self.delete_row_button = QPushButton("Delete Selected Row")
        self.delete_row_button.clicked.connect(self._delete_selected_row)
        table_buttons_layout.addWidget(self.delete_row_button)

        table_buttons_layout.addStretch()
        self.main_layout.addLayout(table_buttons_layout)

        # Dialog buttons
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

    def _populate_table(self):
        """Populate the table with existing entry signature name map data."""
        self.map_table.setRowCount(len(self.config.entry_signature_name_map))

        for row, (signature, name) in enumerate(
            self.config.entry_signature_name_map.items()
        ):
            signature_item = QTableWidgetItem(str(signature))
            name_item = QTableWidgetItem(str(name))

            self.map_table.setItem(row, 0, signature_item)
            self.map_table.setItem(row, 1, name_item)

    def _add_row(self):
        """Add a new empty row to the table."""
        row_count = self.map_table.rowCount()
        self.map_table.insertRow(row_count)

        # Add empty items
        signature_item = QTableWidgetItem("")
        name_item = QTableWidgetItem("")

        self.map_table.setItem(row_count, 0, signature_item)
        self.map_table.setItem(row_count, 1, name_item)

        # Focus on the new row
        self.map_table.setCurrentCell(row_count, 0)

    def _delete_selected_row(self):
        """Delete the currently selected row."""
        current_row = self.map_table.currentRow()
        if current_row >= 0:
            self.map_table.removeRow(current_row)

    def get_config(self):
        """Get the edited config.

        Returns:
            Config: A new config instance with the edited values
        """

        # Build entry signature name map
        entry_signature_name_map = {}
        for row in range(self.map_table.rowCount()):
            signature_item = self.map_table.item(row, 0)
            name_item = self.map_table.item(row, 1)

            if signature_item and name_item:
                signature = signature_item.text().strip()
                name = name_item.text().strip()

                # Only add non-empty entries
                if signature and name:
                    entry_signature_name_map[signature] = name

        # Create a new config instance
        new_config = Config(
            name=self.name_edit.text().strip(),
            read_options=NPKReadOptions(
                decryption_key=None
                if self.key_edit.value() == 0
                else self.key_edit.value(),
                info_size=None
                if self.info_size_edit.value() == 0
                else self.info_size_edit.value(),
            ),
            entry_signature_name_map=entry_signature_name_map,
            wwise_event_map=self.wwise_check.isChecked(),
        )

        return new_config
