"""Dialog for creating a new game configuration."""

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
)

from core.config import Config
from core.npk.class_types import NPKReadOptions


class NewConfigDialog(QDialog):
    """Dialog for creating a new game configuration."""

    def __init__(self, parent=None):
        """Initialize the new config dialog.

        Args:
            parent: Parent widget
        """
        super().__init__(parent)
        self.setWindowTitle("Create New Config")
        self.setMinimumWidth(300)

        # Create layout
        self.main_layout = QVBoxLayout(self)
        self.form_layout = QFormLayout()
        self.main_layout.addLayout(self.form_layout)

        # Config name field
        self.name_edit = QLineEdit()
        self.form_layout.addRow("Config Name:", self.name_edit)

        # Info size field
        self.info_size_edit = QSpinBox()
        self.info_size_edit.setMinimum(0)
        self.info_size_edit.setMaximum(999999)
        self.info_size_edit.setValue(0)  # Default value
        self.form_layout.addRow(
            "Info Size (0 for auto determine):", self.info_size_edit
        )

        # Decryption key field
        self.key_edit = QSpinBox()
        self.key_edit.setMinimum(-2147483648)  # int32 min
        self.key_edit.setMaximum(2147483647)  # int32 max
        self.key_edit.setValue(0)  # Default value
        self.form_layout.addRow("Decryption Key (Use 0 for no key):", self.key_edit)

        # Identity V special mode
        self.wwise_check = QCheckBox(
            "Identity V special mode (dual IDX merge, path restore, bank expansion, Wwise event names)"
        )
        self.form_layout.addRow("", self.wwise_check)

        # Buttons
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

    def get_config(self):
        """Get the created config.

        Returns:
            Config: The newly created config
        """
        config = Config(
            name=self.name_edit.text(),
            read_options=NPKReadOptions(
                decryption_key=None
                if self.key_edit.value() == 0
                else self.key_edit.value(),
                info_size=None
                if self.info_size_edit.value() == 0
                else self.info_size_edit.value(),
            ),
            wwise_event_map=self.wwise_check.isChecked(),
        )
        return config
