"""Dialog for creating a new game configuration.

Read options are schema-driven (:class:`OptionsForm`).
"""

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QVBoxLayout,
)

from neoxtractor.gui.config import Config
from neoxtractor.gui.windows.config_manager.options_form import OptionsForm


class NewConfigDialog(QDialog):
    """Dialog for creating a new game configuration."""

    def __init__(self, parent=None):
        """Initialize the new config dialog.

        Args:
            parent: Parent widget
        """
        super().__init__(parent)
        self.setWindowTitle("Create New Config")
        self.setMinimumWidth(400)

        self.main_layout = QVBoxLayout(self)
        self.form_layout = QFormLayout()
        self.main_layout.addLayout(self.form_layout)

        self.name_edit = QLineEdit()
        self.form_layout.addRow("Config Name:", self.name_edit)

        self.options_form = OptionsForm(self)
        self.main_layout.addWidget(self.options_form)

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

    def get_config(self) -> Config:
        """Get the created config.

        Returns:
            Config: The newly created config
        """
        return Config(
            name=self.name_edit.text().strip(),
            read_options=self.options_form.get_read_options(),
        )
