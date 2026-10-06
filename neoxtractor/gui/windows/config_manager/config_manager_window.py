"""Config Manager Window for managing game configurations."""

import json
import os
from dataclasses import asdict
from typing import cast

from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QListWidget,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from neoxtractor.core.config import Config
from neoxtractor.core.utils import get_application_path
from neoxtractor.gui.config_manager import ConfigManager
from neoxtractor.gui.utils.npk import get_npk_file

from .edit_config_dialog import EditConfigDialog
from .new_config_dialog import NewConfigDialog


class ConfigManagerWindow(QDialog):
    """Config Manager Dialog for managing game configurations."""

    def __init__(self, config_manager: ConfigManager, parent=None):
        """Initialize the config manager window.

        Args:
            config_manager: The config manager instance to use
            parent: Parent widget
        """
        super().__init__(parent)
        self.config_manager = config_manager

        self.setWindowTitle("Config Manager")
        self.setMinimumSize(500, 400)

        # Create layout
        self.main_layout = QHBoxLayout(self)

        # Left side - Config list
        self.config_list = QListWidget()
        # Enable multiple selection for delete/export operations
        self.config_list.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.main_layout.addWidget(self.config_list, 3)  # Give it 3 parts of the layout

        # Right side - Buttons
        self.button_layout = QVBoxLayout()
        self.main_layout.addLayout(
            self.button_layout, 1
        )  # Give it 1 part of the layout

        # Create buttons
        self.add_button = QPushButton("Add")
        self.edit_button = QPushButton("Edit")
        self.delete_button = QPushButton("Delete")
        self.import_button = QPushButton("Import")
        self.import_defaults_button = QPushButton("Import Defaults")
        self.export_button = QPushButton("Export")

        # Add buttons to layout with some spacing
        self.button_layout.addWidget(self.add_button)
        self.button_layout.addWidget(self.edit_button)
        self.button_layout.addWidget(self.delete_button)
        self.button_layout.addSpacing(20)
        self.button_layout.addWidget(self.import_button)
        self.button_layout.addWidget(self.import_defaults_button)
        self.button_layout.addWidget(self.export_button)
        self.button_layout.addStretch()  # Push buttons to the top

        # Connect signals
        self.add_button.clicked.connect(self.add_config)
        self.edit_button.clicked.connect(self.edit_config)
        self.delete_button.clicked.connect(self.delete_config)
        self.import_button.clicked.connect(self.import_config)
        self.import_defaults_button.clicked.connect(self.import_defaults)
        self.export_button.clicked.connect(self.export_config)

        # Load configs into list
        self.refresh_config_list()

    def refresh_config_list(self):
        """Refresh the config list from the config manager."""
        self.config_list.clear()
        for config in self.config_manager.configs:
            self.config_list.addItem(config.name)

    def _is_config_current_config(self, config: Config) -> bool:
        """Check if the given config is the current active config."""
        app = QApplication.instance()
        if app is None:
            return False
        current_config: Config = app.property("game_config")
        return current_config == config

    def add_config(self):
        """Add a new config."""
        try:
            dialog = NewConfigDialog(self)
            if dialog.exec():
                new_config = dialog.get_config()
                self.config_manager.add_config(new_config)
                self.refresh_config_list()
                QMessageBox.information(
                    self, "Success", f"Config '{new_config.name}' added successfully"
                )
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to add config: {str(e)}")

    def edit_config(self):
        """Edit the selected config."""
        selected_items = self.config_list.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "Warning", "Please select a config to edit.")
            return

        if len(selected_items) > 1:
            QMessageBox.warning(
                self, "Warning", "Please select only one config to edit."
            )
            return

        try:
            config_name = selected_items[0].text()
            config = self.config_manager.get_config(config_name)
            if not config:
                QMessageBox.critical(
                    self, "Error", f"Config '{config_name}' not found."
                )
                return

            # Check if trying to edit current config while NPK file is loaded
            if self._is_config_current_config(config) and get_npk_file() is not None:
                QMessageBox.warning(
                    self,
                    "Warning",
                    "Cannot edit the current config while an NPK file is loaded. "
                    "Please close the NPK file first.",
                )
                return

            idx = cast(int, self.config_manager.get_config_index(config))

            dialog = EditConfigDialog(config, self)
            if dialog.exec():
                edited_config = dialog.get_config()
                self.config_manager.update_config(idx, edited_config)
                self.refresh_config_list()
                QMessageBox.information(
                    self,
                    "Success",
                    f"Config '{edited_config.name}' updated successfully",
                )
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to edit config: {str(e)}")

    def delete_config(self):
        """Delete the selected configs."""
        selected_items = self.config_list.selectedItems()
        if not selected_items:
            QMessageBox.warning(
                self, "Warning", "Please select at least one config to delete."
            )
            return

        # Check if trying to delete current config while NPK file is loaded
        for item in selected_items:
            config_name = item.text()
            config = self.config_manager.get_config(config_name)
            if (
                config
                and self._is_config_current_config(config)
                and get_npk_file() is not None
            ):
                QMessageBox.warning(
                    self,
                    "Warning",
                    "Cannot delete the current config while an NPK file is loaded. "
                    "Please close the NPK file first.",
                )
                return

        # Confirmation dialog
        if len(selected_items) == 1:
            config_name = selected_items[0].text()
            reply = QMessageBox.question(
                self,
                "Confirm Delete",
                f"Are you sure you want to delete the config '{config_name}'?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
        else:
            reply = QMessageBox.question(
                self,
                "Confirm Delete",
                f"Are you sure you want to delete {len(selected_items)} configs?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )

        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            # Delete all selected configs
            for item in selected_items:
                config_name = item.text()
                self.config_manager.remove_config(config_name)

            self.refresh_config_list()

            # Show confirmation message
            if len(selected_items) == 1:
                QMessageBox.information(self, "Success", "Config deleted successfully.")
            else:
                QMessageBox.information(
                    self,
                    "Success",
                    f"{len(selected_items)} configs deleted successfully.",
                )
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to delete config(s): {str(e)}")

    def import_config(self):
        """Import a config from file."""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Import Config", "", "JSON Files (*.json)"
        )

        if not file_path:
            return  # User cancelled

        try:
            config = Config.from_file(file_path)
            self.config_manager.add_config(config)
            self.refresh_config_list()
            QMessageBox.information(self, "Success", f"Imported config: {config.name}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to import config: {str(e)}")

    def import_defaults(self):
        """Import default configs from the configs directory with overwrite enabled."""
        # Only show confirmation if there are existing configs
        if len(self.config_manager.configs) > 0:
            reply = QMessageBox.question(
                self,
                "Confirm Import Defaults",
                "This will import all default configs and may overwrite existing configs with the same names. "
                "Are you sure you want to continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )

            if reply != QMessageBox.StandardButton.Yes:
                return

        try:
            configs_path = os.path.join(get_application_path(), "configs")
            if not os.path.exists(configs_path):
                QMessageBox.warning(
                    self, "Warning", "Default configs directory not found."
                )
                return

            # Load configs with overwrite enabled
            original_count = len(self.config_manager.configs)
            self.config_manager.load_from_path(configs_path, overwrite=True)
            new_count = len(self.config_manager.configs)

            self.refresh_config_list()

            imported_count = new_count - original_count
            if imported_count > 0:
                QMessageBox.information(
                    self,
                    "Success",
                    f"Successfully imported {imported_count} default configs.",
                )
            else:
                QMessageBox.information(
                    self,
                    "Success",
                    "Default configs imported (existing configs may have been updated).",
                )
        except Exception as e:
            QMessageBox.critical(
                self, "Error", f"Failed to import default configs: {str(e)}"
            )

    def export_config(self):
        """Export the selected config(s)."""
        selected_items = self.config_list.selectedItems()
        if not selected_items:
            QMessageBox.warning(
                self, "Warning", "Please select at least one config to export."
            )
            return

        selected_configs: list[Config] = []
        for item in selected_items:
            config_name = item.text()
            cfg = self.config_manager.get_config(config_name)
            if cfg:
                selected_configs.append(cfg)

        if not selected_configs:
            QMessageBox.critical(self, "Error", "No matching configs found.")
            return

        if len(selected_configs) == 1:
            # Single config: Save to file
            config = selected_configs[0]
            file_path, _ = QFileDialog.getSaveFileName(
                self, "Export Config", f"{config.name}.json", "JSON Files (*.json)"
            )

            if not file_path:
                return  # User cancelled

            try:
                with open(file_path, "w", encoding="utf-8") as config_file:
                    # Create a dictionary representing the config
                    json.dump(asdict(config), config_file, indent=4)

                QMessageBox.information(
                    self, "Success", f"Exported config to: {file_path}"
                )
            except Exception as e:
                QMessageBox.critical(
                    self, "Error", f"Failed to export config: {str(e)}"
                )
        else:
            # Multiple configs: Save to directory
            dir_path = QFileDialog.getExistingDirectory(
                self, "Select Directory to Export Configs"
            )

            if not dir_path:
                return  # User cancelled

            try:
                successful_exports = 0
                for config in selected_configs:
                    file_path = os.path.join(dir_path, f"{config.name}.json")
                    with open(file_path, "w", encoding="utf-8") as config_file:
                        json.dump(asdict(config), config_file, indent=4)
                    successful_exports += 1

                QMessageBox.information(
                    self,
                    "Success",
                    f"Exported {successful_exports} configs to: {dir_path}",
                )
            except Exception as e:
                QMessageBox.critical(
                    self, "Error", f"Failed to export configs: {str(e)}"
                )
