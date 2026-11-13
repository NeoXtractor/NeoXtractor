"""Config Manager Window for managing game configurations."""

import json
import os
from typing import Callable, Optional, cast

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from neoxtractor.core.utils.logger import get_logger
from neoxtractor.gui.config import Config
from neoxtractor.gui.config_manager import ConfigManager
from neoxtractor.gui.utils.export import sanitize_filename, unique_name

from .edit_config_dialog import EditConfigDialog
from .new_config_dialog import NewConfigDialog


class ConfigManagerWindow(QDialog):
    """Config Manager Dialog for managing game configurations."""

    def __init__(
        self,
        config_manager: ConfigManager,
        get_current_config: Optional[Callable[[], Config | None]] = None,
        archive_loaded: Optional[Callable[[], bool]] = None,
        parent=None,
    ):
        """Initialize the config manager window.

        Args:
            config_manager: The config manager instance to use
            get_current_config: Returns the config the main window has active
            archive_loaded: True while the main window holds an open archive
            parent: Parent widget
        """
        super().__init__(parent)
        self.config_manager = config_manager
        self._get_current_config = get_current_config or (lambda: None)
        self._archive_loaded = archive_loaded or (lambda: False)

        self.setWindowTitle("Config Manager")
        self.setMinimumSize(500, 400)

        self.main_layout = QHBoxLayout(self)

        # Left side - Config list
        self.config_list = QListWidget()
        # Enable multiple selection for delete/export operations
        self.config_list.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.main_layout.addWidget(self.config_list, 3)

        # Right side - Buttons
        self.button_layout = QVBoxLayout()
        self.main_layout.addLayout(
            self.button_layout, 1
        )

        self.add_button = QPushButton("Add")
        self.edit_button = QPushButton("Edit")
        self.delete_button = QPushButton("Delete")
        self.import_button = QPushButton("Import")
        self.import_defaults_button = QPushButton("Import Defaults")
        self.export_button = QPushButton("Export")

        self.button_layout.addWidget(self.add_button)
        self.button_layout.addWidget(self.edit_button)
        self.button_layout.addWidget(self.delete_button)
        self.button_layout.addSpacing(20)
        self.button_layout.addWidget(self.import_button)
        self.button_layout.addWidget(self.import_defaults_button)
        self.button_layout.addWidget(self.export_button)
        self.button_layout.addStretch()

        self.add_button.clicked.connect(self.add_config)
        self.edit_button.clicked.connect(self.edit_config)
        self.delete_button.clicked.connect(self.delete_config)
        self.import_button.clicked.connect(self.import_config)
        self.import_defaults_button.clicked.connect(self.import_defaults)
        self.export_button.clicked.connect(self.export_config)

        self.refresh_config_list()

    def refresh_config_list(self):
        """Refresh the config list from the config manager.

        Built-in (seed) names are marked; a seed with a user override is
        marked as modified.
        """
        self.config_list.clear()
        for config in self.config_manager.configs:
            label = config.name
            if self.config_manager.is_seed(config.name):
                if self.config_manager.has_user_override(config.name):
                    label += " (modified)"
                else:
                    label += " (built-in)"
            # The display label carries a suffix, so the real name rides in
            # the item's user data; callers read it back via _name_of().
            entry = QListWidgetItem(label)
            entry.setData(Qt.ItemDataRole.UserRole, config.name)
            self.config_list.addItem(entry)

    @staticmethod
    def _name_of(item) -> str:
        """The config name behind a list item (label may carry a suffix)."""
        name = item.data(Qt.ItemDataRole.UserRole)
        return str(name) if name is not None else item.text()

    def _is_config_current_config(self, config: Config) -> bool:
        """Check if the given config is the current active config."""
        return self._get_current_config() == config

    def _confirm_overwrite(self, name: str) -> bool:
        """Ask before a write would silently replace a same-named config.

        Caller asks only when the target name exists and is not the very
        config being edited under its own name.
        """
        reply = QMessageBox.question(
            self,
            "Confirm Overwrite",
            f"A config named '{name}' already exists; saving will replace "
            "it. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return reply == QMessageBox.StandardButton.Yes

    def add_config(self):
        """Add a new config."""
        try:
            dialog = NewConfigDialog(self)
            if dialog.exec():
                new_config = dialog.get_config()
                if self.config_manager.get_config(new_config.name):
                    if not self._confirm_overwrite(new_config.name):
                        return
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
            config_name = self._name_of(selected_items[0])
            config = self.config_manager.get_config(config_name)
            if not config:
                QMessageBox.critical(
                    self, "Error", f"Config '{config_name}' not found."
                )
                return

            if self._is_config_current_config(config) and self._archive_loaded():
                QMessageBox.warning(
                    self,
                    "Warning",
                    "Cannot edit the current config while an archive is loaded. "
                    "Please close the archive first.",
                )
                return

            idx = cast(int, self.config_manager.get_config_index(config))

            dialog = EditConfigDialog(config, self)
            if dialog.exec():
                edited_config = dialog.get_config()
                # Renaming onto an existing name collides; own-name edits
                # do not.
                if (
                    edited_config.name != config_name
                    and self.config_manager.get_config(edited_config.name)
                ):
                    if not self._confirm_overwrite(edited_config.name):
                        return
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

        # Check if trying to delete current config while an archive is loaded
        for item in selected_items:
            config_name = self._name_of(item)
            config = self.config_manager.get_config(config_name)
            if (
                config
                and self._is_config_current_config(config)
                and self._archive_loaded()
            ):
                QMessageBox.warning(
                    self,
                    "Warning",
                    "Cannot delete the current config while an archive is loaded. "
                    "Please close the archive first.",
                )
                return

        # A pure built-in (no user override) cannot be deleted: refuse
        # before confirmation, never offer a doomed dialog.  Deleting an
        # edited seed's override reverts it to built-in.
        for item in selected_items:
            config_name = self._name_of(item)
            if (
                self.config_manager.is_seed(config_name)
                and not self.config_manager.has_user_override(config_name)
            ):
                QMessageBox.warning(
                    self,
                    "Warning",
                    f"'{config_name}' is a built-in config and cannot be deleted. "
                    "Edit it to create your own version.",
                )
                return

        if len(selected_items) == 1:
            config_name = self._name_of(selected_items[0])
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
            for item in selected_items:
                config_name = self._name_of(item)
                self.config_manager.remove_config(config_name)

            self.refresh_config_list()

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
        """Revert edited built-in configs to their shipped defaults.

        Drops user overrides so the seed layer shows through again;
        non-built-in user configs are untouched.
        """
        if not any(
            self.config_manager.is_seed(cfg.name)
            and self.config_manager.has_user_override(cfg.name)
            for cfg in self.config_manager.configs
        ):
            QMessageBox.information(
                self,
                "Nothing to revert",
                "No built-in config has been modified.",
            )
            return

        reply = QMessageBox.question(
            self,
            "Confirm Revert",
            "This reverts every modified built-in config to its shipped "
            "default. Your own (non-built-in) configs are kept. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            reverted = self.config_manager.revert_to_seeds()
            self.refresh_config_list()
            QMessageBox.information(
                self,
                "Success",
                f"Reverted {reverted} built-in config(s) to defaults.",
            )
        except Exception as e:
            QMessageBox.critical(
                self, "Error", f"Failed to revert default configs: {str(e)}"
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
            config_name = self._name_of(item)
            cfg = self.config_manager.get_config(config_name)
            if cfg:
                selected_configs.append(cfg)

        if not selected_configs:
            QMessageBox.critical(self, "Error", "No matching configs found.")
            return

        log = get_logger()
        if len(selected_configs) == 1:
            # Single config: save to file.  Names are display strings, so
            # the suggested file name must be sanitized.
            config = selected_configs[0]
            suggested = f"{sanitize_filename(config.name)}.json"
            file_path, _ = QFileDialog.getSaveFileName(
                self, "Export Config", suggested, "JSON Files (*.json)"
            )

            if not file_path:
                return  # User cancelled

            try:
                with open(file_path, "w", encoding="utf-8") as config_file:
                    json.dump(config.to_dict(), config_file, indent=4)

                log.info("exported config %r -> %s", config.name, file_path)
                QMessageBox.information(
                    self, "Success", f"Exported config to: {file_path}"
                )
            except Exception as e:
                log.error("config export failed for %r: %s", config.name, e)
                QMessageBox.critical(
                    self, "Error", f"Failed to export config: {str(e)}"
                )
        else:
            # Multiple configs: save to a directory.  Names are sanitized
            # and made unique; one bad file never stops the rest.
            dir_path = QFileDialog.getExistingDirectory(
                self, "Select Directory to Export Configs"
            )

            if not dir_path:
                return  # User cancelled

            used: set[str] = set()
            successful_exports = 0
            failures: list[str] = []
            for config in selected_configs:
                name = unique_name(dir_path, f"{config.name}.json", used)
                file_path = os.path.join(dir_path, name)
                try:
                    with open(file_path, "w", encoding="utf-8") as config_file:
                        json.dump(config.to_dict(), config_file, indent=4)
                except Exception as e:
                    log.error("config export failed for %r: %s", config.name, e)
                    failures.append(f"{config.name}: {e}")
                else:
                    log.info("exported config %r -> %s", config.name, file_path)
                    successful_exports += 1

            message = f"Exported {successful_exports} configs to: {dir_path}"
            log.info("config batch export finished: %d to %s", successful_exports, dir_path)
            if failures:
                message += "\n" + "\n".join(failures)
                QMessageBox.warning(self, "Export Complete", message)
            else:
                QMessageBox.information(self, "Success", message)
