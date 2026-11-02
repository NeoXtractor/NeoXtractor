"""Custom QListView to display NPK files."""

import os
from typing import cast

from PySide6 import QtCore, QtWidgets

from core.config import Config
from core.npk.class_types import NPKEntry, State
from gui.models.npk_file_model import NPKFileModel
from gui.utils.config import save_config_manager_to_settings
from gui.utils.npk import get_npk_file
from gui.utils.viewer import ALL_VIEWERS


class NPKFileList(QtWidgets.QListView):
    """
    Custom QListView to display NPK files.
    """

    preview_entry = QtCore.Signal(int, NPKEntry)
    open_entry = QtCore.Signal(int, NPKEntry)
    open_entry_with = QtCore.Signal(int, NPKEntry, type, int)

    def __init__(self, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)

        self._disabled = False
        self._select_after_enabled: QtCore.QModelIndex | None = None

        self.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.setDragEnabled(False)
        self.setAcceptDrops(False)
        self.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_context_menu)

        # Connect double-click signal to handler
        self.doubleClicked.connect(self.on_item_double_clicked)

    def setDisabled(self, disabled: bool):
        """
        Set the disabled state of the list view.

        :param disabled: True to disable, False to enable.
        """
        if disabled:
            self.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.NoSelection)
            self.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
            self.setProperty("disabled", True)
        else:
            self.setSelectionMode(
                QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection
            )
            self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
            self.setProperty("disabled", None)
        self.style().unpolish(self)
        self.style().polish(self)

        self._disabled = disabled

        if self._select_after_enabled:
            self.selectionModel().select(
                self._select_after_enabled,
                QtCore.QItemSelectionModel.SelectionFlag.ClearAndSelect,
            )
            self.on_current_changed(self._select_after_enabled, QtCore.QModelIndex())
            self._select_after_enabled = None

    def disabled(self):
        """Get the disabled state of the list view."""
        return self._disabled

    def model(self) -> NPKFileModel:
        """
        Get the current model of the list view.

        :return: The current model, or None if not set.
        """
        return cast(NPKFileModel, super().model())

    def refresh_npk_file(self):
        """
        Set the NPK file to be displayed in the list.

        :param npk_file: The NPK file to display.
        """

        npk_file = get_npk_file()

        if npk_file is None:
            self.setModel(None)
        else:
            self.setModel(NPKFileModel(npk_file, self))
            self.selectionModel().currentChanged.connect(self.on_current_changed)

    def on_current_changed(
        self, current: QtCore.QModelIndex, previous: QtCore.QModelIndex
    ):
        """
        Handle single-click on an item in the list.

        :param index: The model index that was clicked.
        """
        if self._disabled:
            self._select_after_enabled = current
            return

        npk_file = get_npk_file()

        if not self.model() or npk_file is None:
            return

        # Get the row index from the model index
        row_index = current.row()

        entry = npk_file.find_entry_by_id(row_index)
        entry.state = State.PRIMARY_LOAD

        if not previous.row() == -1:
            npk_file.find_entry_by_id(previous.row()).state = State.CACHED

        self.preview_entry.emit(row_index, entry)

    def on_item_double_clicked(self, index: QtCore.QModelIndex):
        """
        Handle double-click on an item in the list.

        :param index: The model index that was double-clicked.
        """
        if self._disabled:
            return

        npk_file = get_npk_file()

        if not self.model() or npk_file is None:
            return

        # Get the row index from the model index
        row_index = index.row()

        entry = npk_file.find_entry_by_id(row_index)

        self.open_entry.emit(row_index, entry)

    def show_context_menu(self, position):
        """
        Show a context menu for selected items.

        :param position: Position where the context menu was requested.
        """
        npk_file = get_npk_file()

        if not self.model() or npk_file is None:
            return

        # Check if there are any selected items
        indexes = self.selectedIndexes()
        if not indexes:
            return

        menu = QtWidgets.QMenu(self)

        # Add extract option for any selection
        extract = menu.addAction("Extract")
        extract.triggered.connect(lambda: self.extract_entries(indexes, decoded=True))

        if self._selection_has_decoded_entries(indexes):
            extract_raw = menu.addAction("Extract Raw")
            extract_raw.triggered.connect(
                lambda: self.extract_entries(indexes, decoded=False)
            )

        menu.addSeparator()
        for viewer in ALL_VIEWERS:
            viewer_action = menu.addAction("Open in " + viewer.name)
            viewer_action.triggered.connect(
                lambda _checked, v=viewer: self.open_entries_with(indexes, v)
            )

        if len(indexes) == 1:
            menu.addSeparator()
            rename = menu.addAction("Rename")
            rename.triggered.connect(lambda: self.show_rename_dialog(indexes[0]))

        # Show the context menu at the current position
        menu.exec(self.viewport().mapToGlobal(position))

    def open_entries_with(self, indexes: list[QtCore.QModelIndex], viewer: type):
        """
        Open the selected entry with the specified viewer.

        :param indexes: List of model indexes for the selected entries.
        :param viewer: The viewer class to use.
        """
        npk_file = get_npk_file()
        if npk_file is None:
            return
        for i, index in enumerate(indexes):
            row = index.row()
            entry = npk_file.find_entry_by_id(row)
            self.open_entry_with.emit(row, entry, viewer, i)

    @staticmethod
    def _export_relative_path(filename: str, row_index: int) -> str:
        """Keep the restored directory tree and drop unsafe path segments."""
        parts = [
            part
            for part in filename.replace("\\", "/").split("/")
            if part not in ("", ".", "..")
        ]
        if not parts:
            return f"unknown_file_{row_index}"
        return os.path.join(*parts)

    @staticmethod
    def _write_entry(entry: NPKEntry, file_path: str, decoded: bool) -> None:
        """Write one entry to disk, creating the restored directory tree."""
        parent = os.path.dirname(file_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(file_path, "wb") as handle:
            handle.write(entry.get_export_data(decoded=decoded))

    def _selection_has_decoded_entries(self, indexes: list[QtCore.QModelIndex]) -> bool:
        npk_file = get_npk_file()
        if npk_file is None:
            return False
        for index in indexes:
            entry = npk_file.find_entry_by_id(index.row())
            if getattr(entry, "has_decoded_view", False):
                return True
        return False

    def extract_entries(self, indexes: list[QtCore.QModelIndex], decoded: bool = True):
        """
        Extract selected entries from the NPK file.

        :param indexes: List of model indexes for the selected entries.
        """
        npk_file = get_npk_file()
        if not self.model() or npk_file is None:
            return

        if len(indexes) == 1:
            index = indexes[0]
            row_index = index.row()

            # Get the entry data first so the detected extension is reflected in the filename.
            entry = npk_file.find_entry_by_id(row_index)
            filename = (
                entry.get_export_filename(decoded=decoded)
                or self.model().get_filename(index)
            )
            relative = self._export_relative_path(filename, row_index)

            if os.path.dirname(relative):
                # Restored path: keep the original directory tree under a chosen folder.
                dir_path = QtWidgets.QFileDialog.getExistingDirectory(
                    self,
                    "Select Directory to Extract File",
                    "",
                    QtWidgets.QFileDialog.Option.ShowDirsOnly,
                )
                if not dir_path:
                    return
                file_path = os.path.join(dir_path, relative)
            else:
                file_path, _ = QtWidgets.QFileDialog.getSaveFileName(
                    self, "Extract File", relative, "All Files (*.*)"
                )
                if not file_path:
                    return

            try:
                self._write_entry(entry, file_path, decoded)
                QtWidgets.QMessageBox.information(
                    self, "Success", f"File extracted to {file_path}"
                )
            except Exception as e:
                QtWidgets.QMessageBox.critical(
                    self, "Error", f"Failed to extract file: {str(e)}"
                )
        else:
            # For multiple files, show directory selection dialog
            dir_path = QtWidgets.QFileDialog.getExistingDirectory(
                self,
                "Select Directory to Extract Files",
                "",
                QtWidgets.QFileDialog.Option.ShowDirsOnly,
            )

            if dir_path:
                try:
                    success_count = 0
                    fail_count = 0

                    for index in indexes:
                        row_index = index.row()

                        # Get the entry data first so the detected extension is reflected in the filename.
                        entry = npk_file.find_entry_by_id(row_index)
                        filename = (
                            entry.get_export_filename(decoded=decoded)
                            or self.model().get_filename(index)
                        )

                        # Restored paths carry the original directory tree; keep it.
                        relative = self._export_relative_path(filename, row_index)

                        file_path = os.path.join(dir_path, relative)

                        try:
                            self._write_entry(entry, file_path, decoded)
                            success_count += 1
                        except Exception:
                            fail_count += 1

                    message = f"Extracted {success_count} files to {dir_path}"
                    if fail_count > 0:
                        message += f"\n{fail_count} files failed to extract"

                    QtWidgets.QMessageBox.information(
                        self, "Extraction Complete", message
                    )
                except Exception as e:
                    QtWidgets.QMessageBox.critical(
                        self, "Error", f"Failed to extract files: {str(e)}"
                    )

    def show_rename_dialog(self, index: QtCore.QModelIndex):
        """
        Show a dialog to rename the selected file.

        :param index: The model index of the item to rename.
        """
        npk_file = get_npk_file()

        if not self.model() or npk_file is None:
            return

        entry_index = npk_file.indices[index.row()]

        # Show input dialog to get new name
        new_name, ok = QtWidgets.QInputDialog.getText(
            self,
            "Rename File",
            f"Enter new name for {self.model().get_filename(index)}:",
            QtWidgets.QLineEdit.EchoMode.Normal,
            "",
        )

        if ok and new_name:
            app = cast(QtCore.QCoreApplication, QtWidgets.QApplication.instance())
            config: Config = app.property("game_config")
            config_manager = app.property("config_manager")
            settings_manager = app.property("settings_manager")
            config.entry_signature_name_map[hex(entry_index.file_signature)] = new_name
            save_config_manager_to_settings(config_manager, settings_manager)
            model = self.model()
            model.get_filename(index, invalidate_cache=True)
            self.update(model.index(index.row()))
