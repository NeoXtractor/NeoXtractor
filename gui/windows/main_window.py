"""Provides MainWindow class."""

import os
import sys
from typing import Any, cast

from PySide6 import QtCore, QtGui, QtWidgets

from core.config import Config
from core.logger import get_logger
from core.npk.class_types import NPKEntry, NPKEntryDataFlags, NPKReadOptions
from core.npk.enums import NPKEntryFileCategories
from core.npk.npk_file import NPKFile
from core.wpk.wpk_file import IDXWPKFile
from gui.config_manager import ConfigManager
from gui.models.npk_file_model import NPKFileModel
from gui.npk_entry_filter import NPKEntryFilter
from gui.settings_manager import SettingsManager
from gui.utils.config import save_config_manager_to_settings
from gui.utils.viewer import ALL_VIEWERS, find_best_viewer
from gui.widgets.npk_file_list import NPKFileList
from gui.widgets.preview_widget import PreviewWidget
from gui.windows.about_window import AboutWindow
from gui.windows.config_manager import ConfigManagerWindow
from gui.windows.settings_window import SettingsWindow
from gui.windows.viewer_tab_window import ViewerTabWindow
from gui.windows.id5_path_mapping_window import (
    Id5PathMappingWindow,
    is_wwise_archive,
)
from id5mapping.wwise import (
    WwiseEventIndex,
    apply_bank_labels,
    expand_banks,
    mount_archive_banks,
)


class MainWindow(QtWidgets.QMainWindow):
    """Main window class."""

    _loading_cancelled = False
    _loading_in_progress = False

    # Custom signals for thread-safe UI updates
    update_progress_signal = QtCore.Signal(int)
    update_model_signal = QtCore.Signal(int)
    loading_complete_signal = QtCore.Signal()

    _config_list_refreshing = False

    _viewer_windows: dict[Any, ViewerTabWindow] = {}

    def __init__(self):
        super().__init__()

        self.app = cast(QtCore.QCoreApplication, QtWidgets.QApplication.instance())

        # Connect signals to slots
        self.update_progress_signal.connect(self._update_progress)
        self.update_model_signal.connect(self._update_model)
        self.loading_complete_signal.connect(self._loading_complete)

        self.setWindowTitle("NeoXtractor")

        self.config: Config | None = None

        self.config_manager: ConfigManager = self.app.property("config_manager")
        self.settings_manager: SettingsManager = self.app.property("settings_manager")

        self.main_layout = QtWidgets.QHBoxLayout()

        self.control_layout = QtWidgets.QVBoxLayout()

        self.config_section = QtWidgets.QHBoxLayout()

        self.active_config_label = QtWidgets.QLabel("Active Config:")
        self.active_config_label.setStyleSheet("font-weight: bold;")
        self.active_config_label.setFixedWidth(100)
        self.config_section.addWidget(self.active_config_label)

        self.active_config = QtWidgets.QComboBox()
        self.active_config.setMinimumWidth(200)
        self.active_config.currentIndexChanged.connect(self.on_config_changed)
        self.config_section.addWidget(self.active_config)

        self.control_layout.addLayout(self.config_section)

        self.list_widget = NPKFileList(self)
        self.list_widget.preview_entry.connect(
            lambda _row, entry: self.preview_widget.set_file(entry)
        )

        def open_tab_window_for_entry(
            _row: int, entry: NPKEntry, viewer: type | None = None, batch_index: int = 0
        ):
            if viewer is None:
                viewer = find_best_viewer(
                    entry.extension, bool(entry.data_flags & NPKEntryDataFlags.TEXT)
                )
            wnd = self._get_tab_window_for_viewer(viewer)
            wnd.load_file(entry, batch_index == 0)
            wnd.show()

        self.list_widget.open_entry.connect(open_tab_window_for_entry)
        self.list_widget.open_entry_with.connect(open_tab_window_for_entry)

        self.filter = NPKEntryFilter(self.list_widget)

        self.filter_section = QtWidgets.QVBoxLayout()

        self.filter_label = QtWidgets.QLabel("Filters")
        self.filter_label.setStyleSheet("font-weight: bold;")
        self.filter_section.addWidget(self.filter_label)

        self.name_filter_input = QtWidgets.QLineEdit()
        self.name_filter_input.setPlaceholderText("Search by filename...")

        def filter_text_changed():
            self.filter.filter_string = self.name_filter_input.text().lower()
            self.filter.apply_filter()

        self.name_filter_input.textChanged.connect(filter_text_changed)
        self.filter_section.addWidget(self.name_filter_input)

        self.filter_checkbox_section = QtWidgets.QGridLayout()

        self.filter_binary_filter = QtWidgets.QCheckBox("Binary Files")
        self.filter_binary_filter.setChecked(True)

        def filter_binary_filter_changed(checked: bool):
            self.filter.include_binary = checked
            self.filter.apply_filter()

        self.filter_binary_filter.toggled.connect(filter_binary_filter_changed)
        self.filter_checkbox_section.addWidget(self.filter_binary_filter, 0, 0)

        self.filter_text_filter = QtWidgets.QCheckBox("Text Files")
        self.filter_text_filter.setChecked(True)

        def filter_text_filter_changed(checked: bool):
            self.filter.include_text = checked
            self.filter.apply_filter()

        self.filter_text_filter.toggled.connect(filter_text_filter_changed)
        self.filter_checkbox_section.addWidget(self.filter_text_filter, 0, 1)

        self.filter_slot_filter = QtWidgets.QCheckBox("Slot Files")
        self.filter_slot_filter.setChecked(True)

        def filter_slot_filter_changed(checked: bool):
            self.filter.include_slot = checked
            self.filter.apply_filter()

        self.filter_slot_filter.toggled.connect(filter_slot_filter_changed)
        self.filter_checkbox_section.addWidget(self.filter_slot_filter, 0, 2)

        self.filter_section.addLayout(self.filter_checkbox_section)

        self.entry_category_filter_combobox = QtWidgets.QComboBox()
        self.entry_category_filter_combobox.addItem("All", None)
        for i in NPKEntryFileCategories:
            self.entry_category_filter_combobox.addItem(i.value, i)
        self.entry_category_filter_combobox.setCurrentIndex(0)

        def filter_type_changed(index: int):
            self.filter.filter_type = self.entry_category_filter_combobox.itemData(
                index
            )
            self.mesh_biped_head_filter_checkbox.setVisible(
                self.filter.filter_type == NPKEntryFileCategories.MESH
            )
            self.filter.apply_filter()

        self.entry_category_filter_combobox.currentIndexChanged.connect(
            filter_type_changed
        )
        self.filter_section.addWidget(self.entry_category_filter_combobox)

        self.mesh_biped_head_filter_checkbox = QtWidgets.QCheckBox(
            "Only 'biped head' meshes"
        )
        self.mesh_biped_head_filter_checkbox.setVisible(False)

        def filter_mesh_biped_head_changed(checked: bool):
            self.filter.mesh_biped_head = checked
            self.filter.apply_filter()

        self.mesh_biped_head_filter_checkbox.toggled.connect(
            filter_mesh_biped_head_changed
        )
        self.filter_section.addWidget(self.mesh_biped_head_filter_checkbox)

        self.control_layout.addLayout(self.filter_section)
        self.control_layout.addWidget(self.list_widget)

        self.progress_bar = QtWidgets.QProgressBar(self)
        self.progress_bar.setVisible(False)
        self.control_layout.addWidget(self.progress_bar)

        self.cancel_button = QtWidgets.QPushButton("Cancel")
        self.cancel_button.setStatusTip("Cancel loading the NPK file.")
        self.cancel_button.setVisible(False)

        def cancel_loading():
            self._loading_cancelled = True

        self.cancel_button.clicked.connect(cancel_loading)
        self.control_layout.addWidget(self.cancel_button)

        def extract_all(visible_only: bool = False):
            model = self.list_widget.model()
            all_indexes = [model.index(i, 0) for i in range(model.rowCount())]

            if visible_only:
                indexes = [
                    idx
                    for idx in all_indexes
                    if not self.list_widget.isRowHidden(idx.row())
                ]
            else:
                indexes = all_indexes
            self.list_widget.extract_entries(indexes)

        self.extract_button_widget = QtWidgets.QWidget()
        self.extract_button_widget.setVisible(False)

        self.extract_buttons = QtWidgets.QHBoxLayout()
        self.extract_button_widget.setLayout(self.extract_buttons)

        self.extract_all = QtWidgets.QPushButton("Extract All")
        self.extract_all.setStatusTip("Extract all files in the NPK file.")
        self.extract_all.clicked.connect(lambda: extract_all(False))
        self.extract_buttons.addWidget(self.extract_all)

        self.extract_filtered = QtWidgets.QPushButton("Extract Filtered")
        self.extract_filtered.setStatusTip("Extract all files in the list.")
        self.extract_filtered.clicked.connect(lambda: extract_all(True))

        self.extract_buttons.addWidget(self.extract_filtered)

        self.control_layout.addWidget(self.extract_button_widget)

        # Container for the control layout to limit its width
        control_widget = QtWidgets.QWidget()
        control_widget.setLayout(self.control_layout)
        control_widget.setMaximumWidth(500)
        self.main_layout.addWidget(control_widget, stretch=1)

        self.preview_widget = PreviewWidget(self)
        self.main_layout.addWidget(self.preview_widget, stretch=2)

        # Create a central widget and set the layout on it
        self.central_widget = QtWidgets.QWidget()
        self.central_widget.setLayout(self.main_layout)
        self.setCentralWidget(self.central_widget)

        self.open_file_action: QtGui.QAction
        self.unload_npk_action: QtGui.QAction

        def file_menu() -> QtWidgets.QMenu:
            menu = QtWidgets.QMenu(title="File")

            open_file = QtGui.QAction(
                self.style().standardIcon(
                    QtWidgets.QStyle.StandardPixmap.SP_DirOpenIcon
                ),
                "Open File",
                self,
            )
            open_file.setStatusTip("Open a supported archive file.")
            open_file.setShortcut("Ctrl+O")
            menu.addAction(open_file)

            def open_file_dialog():
                if self.config is None:
                    QtWidgets.QMessageBox.warning(
                        self,
                        "No Config Selected",
                        "Please select a config before opening a file.",
                    )
                    return
                file_path, _ = QtWidgets.QFileDialog.getOpenFileName(
                    self,
                    "Open Archive File",
                    "",
                    "Supported Files (*.npk *.expk *.idx *.wpk);;NPK Files (*.npk *.expk);;IDX Files (*.idx);;WPK Files (*.wpk);;All Files (*)",
                )
                if file_path:
                    self.load_npk(file_path)

            open_file.triggered.connect(open_file_dialog)
            self.open_file_action = open_file

            unload_npk = QtGui.QAction(
                self.style().standardIcon(
                    QtWidgets.QStyle.StandardPixmap.SP_DialogCancelButton
                ),
                "Unload NPK",
                self,
            )
            unload_npk.setStatusTip("Unload the current NPK file.")
            unload_npk.setShortcut("Ctrl+W")
            unload_npk.setEnabled(False)  # Initially disabled
            unload_npk.triggered.connect(self.unload_npk)
            menu.addAction(unload_npk)
            self.unload_npk_action = unload_npk

            menu.addSeparator()

            config_manager = QtGui.QAction(
                self.style().standardIcon(
                    QtWidgets.QStyle.StandardPixmap.SP_FileDialogContentsView
                ),
                "Config Manager",
                self,
            )
            config_manager.setMenuRole(QtGui.QAction.MenuRole.NoRole)
            config_manager.setStatusTip("Open the Config Manager.")
            config_manager.setShortcut("Ctrl+M")

            def open_config_manager():
                dialog = ConfigManagerWindow(self.config_manager)
                dialog.exec()
                save_config_manager_to_settings(
                    self.config_manager, self.app.property("settings_manager")
                )
                self.refresh_config_list()

            config_manager.triggered.connect(open_config_manager)

            menu.addAction(config_manager)

            return menu

        self.menuBar().addMenu(file_menu())

        app_menu = None
        if sys.platform == "darwin":
            app_menu = self.menuBar().addMenu("NeoXtractor")

        settings_action = (app_menu or self.menuBar()).addAction("Settings")
        settings_action.setStatusTip("Open Settings window.")
        settings_action.setMenuRole(QtGui.QAction.MenuRole.PreferencesRole)
        settings_action.triggered.connect(
            lambda: SettingsWindow(self.settings_manager, self).exec()
        )

        def tools_menu() -> QtWidgets.QMenu:
            menu = QtWidgets.QMenu("Tools")

            for viewer in ALL_VIEWERS:
                menu.addAction(
                    viewer.name,
                    lambda v=viewer: self._get_tab_window_for_viewer(v).show(),
                )

            menu.addSeparator()
            id5_mapping = menu.addAction("ID5 Path Mapping...")
            id5_mapping.setStatusTip(
                "Identity V only: restore vfs paths from the two .thy name tables "
                "(and label Wwise events for wwise.idx)."
            )
            id5_mapping.setEnabled(self._wwise_enabled())
            id5_mapping.triggered.connect(self.open_id5_path_mapping)
            self.id5_mapping_action = id5_mapping

            return menu

        self.menuBar().addMenu(tools_menu())

        (app_menu or self.menuBar()).addAction(
            "About", lambda: AboutWindow(self).exec()
        ).setMenuRole(QtGui.QAction.MenuRole.AboutRole)

        self.refresh_config_list()

    def _get_tab_window_for_viewer(self, viewer: Any) -> ViewerTabWindow:
        if viewer not in self._viewer_windows:
            self._viewer_windows[viewer] = ViewerTabWindow(viewer)
        return self._viewer_windows[viewer]

    def _wwise_enabled(self) -> bool:
        """Identity V special features are only active with the matching config."""
        return bool(self.config is not None and getattr(self.config, "wwise_event_map", False))

    def _update_mapping_action(self) -> None:
        """Keep the Tools entry in sync with the selected config."""
        action = getattr(self, "id5_mapping_action", None)
        if action is not None:
            action.setEnabled(self._wwise_enabled())

    def _auto_wwise_index(self, archive_file):
        """Activate the bundled hash index and mount the wwise package's banks."""
        if not self._wwise_enabled():
            return None
        idx_path = getattr(archive_file, "idx_path", None)
        if not is_wwise_archive(idx_path):
            return None
        index = WwiseEventIndex()
        if not index.load_bundled_index():
            return None

        index.autodetect_thy(idx_path)
        mount_archive_banks(archive_file, index)
        self.app.setProperty("wwise_event_index", index)
        get_logger().info(
            "Wwise event index auto-enabled: %d names, %d mounted shortIDs, %d md5 entries",
            len(index.name_by_hash),
            len(index.short_id_events),
            len(index.md5_events),
        )
        return index

    def open_id5_path_mapping(self):
        """Apply the ID5 name tables to the opened package."""
        if not self._wwise_enabled():
            QtWidgets.QMessageBox.information(
                self,
                "ID5 Path Mapping",
                "Select the \"Identity V\" config in Active Config to use this feature.",
            )
            return

        archive_file = self.app.property("npk_file")
        idx_path = getattr(archive_file, "idx_path", None)
        if archive_file is None or not idx_path:
            QtWidgets.QMessageBox.information(
                self,
                "ID5 Path Mapping",
                "Open an IDX package first.",
            )
            return
        if self._loading_in_progress:
            QtWidgets.QMessageBox.information(
                self,
                "ID5 Path Mapping",
                "Wait for the package to finish loading, then open this dialog.",
            )
            return

        dialog = Id5PathMappingWindow(self, idx_path=idx_path, archive_file=archive_file)
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return

        if dialog.index is not None:
            self.app.setProperty("wwise_event_index", dialog.index)
            if is_wwise_archive(idx_path):
                expand_banks(archive_file, dialog.index)
            apply_bank_labels(archive_file, dialog.index)

        NPKFileModel._file_names_cache.clear()
        self.list_widget.refresh_npk_file()
        self.filter.apply_filter()

        get_logger().info(
            "ID5 path mapping applied: %d entries restored, %d events labelled.",
            dialog.restored,
            len(dialog.index.events) if dialog.index else 0,
        )

    def get_tab_windows(self) -> list[ViewerTabWindow]:
        """Get all viewer tab windows."""
        return list(self._viewer_windows.values())

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        force_close = False
        for viewer_window in self._viewer_windows.values():
            if force_close:
                viewer_window.close()
                continue
            if viewer_window.isVisible():
                if (
                    QtWidgets.QMessageBox.warning(
                        self,
                        "Close Viewers",
                        "There are still viewer windows open.\n"
                        + "Are you sure you want to quit?",
                        buttons=QtWidgets.QMessageBox.StandardButton.Ok
                        | QtWidgets.QMessageBox.StandardButton.Cancel,
                        defaultButton=QtWidgets.QMessageBox.StandardButton.Cancel,
                    )
                    == QtWidgets.QMessageBox.StandardButton.Ok
                ):
                    force_close = True
                    viewer_window.close()
                else:
                    event.ignore()
                    return

    def unload_npk(self):
        """Unload the NPK file."""

        if self.app.property("npk_file") is None:
            return

        self.setWindowTitle("NeoXtractor")
        self.app.setProperty("npk_file", None)
        self.app.setProperty("wwise_event_index", None)
        self._loading_in_progress = False
        NPKFileModel._file_names_cache.clear()
        self.list_widget.refresh_npk_file()
        self.extract_button_widget.setVisible(False)
        self.preview_widget.clear()
        self.unload_npk_action.setEnabled(False)
        get_logger().info("NPK file unloaded.")

    def refresh_config_list(self):
        """Refresh the config list from the config manager."""
        previous_config = self.config

        self._config_list_refreshing = True

        self.active_config.clear()
        for i, config in enumerate(self.config_manager.configs):
            self.active_config.addItem(config.name)
            if previous_config == config:
                self.active_config.setCurrentIndex(i)

        self._config_list_refreshing = False

        # Trigger the config change event
        self.on_config_changed(self.active_config.currentIndex())

    def on_config_changed(self, index: int):
        """Handle config change."""

        if self._config_list_refreshing:
            return

        previous_config = self.config

        if index == -1:
            self.config = None
        else:
            self.config = self.config_manager.configs[index]

        if previous_config != self.config:
            if (
                previous_config is not None
                and self.app.property("npk_file") is not None
                and QtWidgets.QMessageBox.warning(
                    self,
                    "NPK File loaded",
                    "Changing the config will unload the NPK file.\n"
                    + "Are you sure you want to continue?",
                    buttons=QtWidgets.QMessageBox.StandardButton.Ok
                    | QtWidgets.QMessageBox.StandardButton.Cancel,
                    defaultButton=QtWidgets.QMessageBox.StandardButton.Cancel,
                )
                == QtWidgets.QMessageBox.StandardButton.Cancel
            ):
                # Restore the previous config selection
                self.config = previous_config
                self.active_config.setCurrentIndex(
                    self.active_config.findText(previous_config.name)
                )
                return

            self.unload_npk()

            self.app.setProperty("game_config", self.config)
            if not self._wwise_enabled():
                self.app.setProperty("wwise_event_index", None)
                NPKFileModel._file_names_cache.clear()

            get_logger().info(
                "Config changed to: %s", self.config.name if self.config else "None"
            )

        self._update_mapping_action()

    def load_npk(self, path: str):
        """Load an NPK file and populate the list widget."""

        self.unload_npk()

        self._loading_cancelled = False
        self._loading_in_progress = True

        self.setWindowTitle(f"NeoXtractor - {os.path.basename(path)}")

        self.open_file_action.setEnabled(False)
        self.active_config.setEnabled(False)
        self.progress_bar.setVisible(True)

        self.progress_bar.setFormat("Reading NPK file...")
        self.progress_bar.setRange(0, 0)

        self.list_widget.setDisabled(True)

        read_options = self.config.read_options if self.config else None
        if read_options is None:
            # No read options set, use default
            read_options = NPKReadOptions()

        ext = os.path.splitext(path)[1].lower()
        if ext in (".npk", ".expk"):
            archive_file = NPKFile(path, read_options)
        elif ext in (".idx", ".wpk"):
            archive_file = IDXWPKFile(
                path,
                read_options,
                merge_counterpart=self._wwise_enabled(),
                restore_paths=self._wwise_enabled(),
            )
        else:
            raise ValueError(f"Unsupported archive type: {path}")

        self.app.setProperty("npk_file", archive_file)

        self.list_widget.refresh_npk_file()

        self.progress_bar.setFormat("Loading entries... (%v/%m)")
        self.progress_bar.setRange(0, archive_file.file_count)
        self.progress_bar.setValue(0)

        def _load_entries():
            with open(archive_file.file_path, "rb") as f:
                for i in range(archive_file.file_count):
                    if self._loading_cancelled:
                        break
                    if isinstance(archive_file, NPKFile):
                        archive_file.load_entry(i, f)
                    else:
                        archive_file.load_entry(i)
                    self.update_model_signal.emit(i)
                    self.update_progress_signal.emit(i + 1)
                self.loading_complete_signal.emit()

        QtCore.QThreadPool.globalInstance().start(_load_entries)

        self.cancel_button.setVisible(True)

    def _update_progress(self, value):
        """Update progress bar value from the signal."""
        self.progress_bar.setValue(value)

    def _update_model(self, index):
        """Update model from the signal."""
        model = cast(NPKFileModel, self.list_widget.model())
        idx = model.index(index)
        model.get_filename(idx, invalidate_cache=True)
        self.list_widget.update(idx)

    def _loading_complete(self):
        """Handle completion of loading from the signal."""
        self._loading_in_progress = False
        # Restore normal selection behavior and style when loading is complete
        self.list_widget.setDisabled(False)
        self.open_file_action.setEnabled(True)
        self.active_config.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.cancel_button.setVisible(False)
        self.extract_button_widget.setVisible(True)
        self.unload_npk_action.setEnabled(True)
        if self._loading_cancelled:
            self.unload_npk()
        else:
            archive_file = self.app.property("npk_file")

            if (
                archive_file is not None
                and self._wwise_enabled()
                and is_wwise_archive(getattr(archive_file, "idx_path", None))
            ):
                # Rebuild the index from the fully loaded package: an index
                # built while the entries were still loading only sees part of
                # the banks, which would leave most media unnamed.
                wwise_index = self._auto_wwise_index(archive_file)
                expanded = expand_banks(archive_file, wwise_index)
                renamed = (
                    apply_bank_labels(archive_file, wwise_index)
                    if wwise_index is not None
                    else 0
                )
                if expanded or renamed:
                    NPKFileModel._file_names_cache.clear()
                    self.list_widget.refresh_npk_file()

            if archive_file is not None and hasattr(archive_file, "get_load_error_summary"):
                summary = archive_file.get_load_error_summary()

                missing = summary.get("missing_entry_count", 0)
                partial = summary.get("partial_entry_count", 0)
                other = summary.get("other_error_count", 0)

                if missing or partial or other:
                    get_logger().warning(
                        "IDX/WPK load summary: %d missing resources, %d incomplete entries, %d other errors",
                        missing,
                        partial,
                        other,
                    )

            self.filter.apply_filter()
