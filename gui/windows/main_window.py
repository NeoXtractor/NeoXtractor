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
from core.npk.related_archives import RelatedArchiveSet, resource_location
from core.npk.scan_control import CharacterScanCancelled
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
from gui.windows.character_folders_window import CharacterFoldersWindow
from gui.windows.config_manager import ConfigManagerWindow
from gui.windows.settings_window import SettingsWindow
from gui.windows.viewer_tab_window import ViewerTabWindow


class MainWindow(QtWidgets.QMainWindow):
    """Main window class."""

    _loading_cancelled = False

    # Custom signals for thread-safe UI updates
    update_progress_signal = QtCore.Signal(int)
    update_model_signal = QtCore.Signal(int)
    loading_complete_signal = QtCore.Signal()
    loading_error_signal = QtCore.Signal(str)
    loading_stage_signal = QtCore.Signal(str, int, int)

    _config_list_refreshing = False

    _viewer_windows: dict[Any, ViewerTabWindow] = {}
    _character_folders_window: CharacterFoldersWindow | None = None

    def __init__(self):
        super().__init__()
        self._viewer_windows = {}
        self._archive_loading = False

        self.app = cast(QtCore.QCoreApplication, QtWidgets.QApplication.instance())

        # Connect signals to slots
        self.update_progress_signal.connect(self._update_progress)
        self.update_model_signal.connect(self._update_model)
        self.loading_complete_signal.connect(self._loading_complete)
        self.loading_error_signal.connect(self._loading_failed)
        self.loading_stage_signal.connect(self._character_scan_progress)

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
            self._apply_archive_filter_if_visible()
            self._sync_character_filters()

        self.name_filter_input.textChanged.connect(filter_text_changed)
        self.filter_section.addWidget(self.name_filter_input)

        self.filter_checkbox_section = QtWidgets.QGridLayout()

        self.filter_binary_filter = QtWidgets.QCheckBox("Binary Files")
        self.filter_binary_filter.setChecked(True)

        def filter_binary_filter_changed(checked: bool):
            self.filter.include_binary = checked
            self._apply_archive_filter_if_visible()
            self._sync_character_filters()

        self.filter_binary_filter.toggled.connect(filter_binary_filter_changed)
        self.filter_checkbox_section.addWidget(self.filter_binary_filter, 0, 0)

        self.filter_text_filter = QtWidgets.QCheckBox("Text Files")
        self.filter_text_filter.setChecked(True)

        def filter_text_filter_changed(checked: bool):
            self.filter.include_text = checked
            self._apply_archive_filter_if_visible()
            self._sync_character_filters()

        self.filter_text_filter.toggled.connect(filter_text_filter_changed)
        self.filter_checkbox_section.addWidget(self.filter_text_filter, 0, 1)

        self.filter_slot_filter = QtWidgets.QCheckBox("Slot Files")
        self.filter_slot_filter.setChecked(True)

        def filter_slot_filter_changed(checked: bool):
            self.filter.include_slot = checked
            self._apply_archive_filter_if_visible()
            self._sync_character_filters()

        self.filter_slot_filter.toggled.connect(filter_slot_filter_changed)
        self.filter_checkbox_section.addWidget(self.filter_slot_filter, 0, 2)

        self.filter_section.addLayout(self.filter_checkbox_section)

        self.entry_category_filter_combobox = QtWidgets.QComboBox()
        self.entry_category_filter_combobox.addItem("All", None)
        for i in NPKEntryFileCategories:
            self.entry_category_filter_combobox.addItem(i.value, i)
        self.entry_category_filter_combobox.addItem("Characters", "characters")
        self.entry_category_filter_combobox.setCurrentIndex(0)

        def filter_type_changed(index: int):
            selected_filter = self.entry_category_filter_combobox.itemData(index)
            if selected_filter == "characters":
                self.filter.filter_type = None
                self.mesh_biped_head_filter_checkbox.setVisible(False)
                self.open_character_folders()
                return

            self.left_content.setCurrentWidget(self.list_widget)
            self.filter.filter_type = selected_filter
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
        self.left_content = QtWidgets.QStackedWidget(self)
        self.left_content.addWidget(self.list_widget)
        self._character_folders_window = CharacterFoldersWindow(None, None, self)
        self._character_folders_window.open_entry_with.connect(open_tab_window_for_entry)
        self._character_folders_window.scan_started.connect(self._character_scan_started)
        self._character_folders_window.scan_progress.connect(self._character_scan_progress)
        self._character_folders_window.scan_finished.connect(self._character_scan_finished)
        self._character_folders_window.preview_entry_requested.connect(
            self.preview_character_entry
        )
        self._character_folders_window.unresolved_requested.connect(
            self.show_unresolved_character_asset
        )
        self.left_content.addWidget(self._character_folders_window)
        self.control_layout.addWidget(self.left_content, stretch=1)

        self.progress_bar = QtWidgets.QProgressBar(self)
        self.progress_bar.setVisible(False)
        self.control_layout.addWidget(self.progress_bar)

        self.cancel_button = QtWidgets.QPushButton("Cancel")
        self.cancel_button.setStatusTip("Cancel loading the NPK file.")
        self.cancel_button.setVisible(False)

        def cancel_loading():
            if self._character_folders_window.scan_busy:
                self._character_folders_window.cancel_scan()
            else:
                self._loading_cancelled = True

        self.cancel_button.clicked.connect(cancel_loading)
        self.control_layout.addWidget(self.cancel_button)

        def extract_all(visible_only: bool = False):
            if visible_only and self.entry_category_filter_combobox.currentData() == "characters":
                self._character_folders_window.extract_visible()
                return
            model = self.list_widget.model()
            if model is None:
                return
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
            self.related_archives_action = menu.addAction("Load all same-name related copies together (HPMA)")
            self.related_archives_action.setCheckable(True)
            self.related_archives_action.setChecked(self.settings_manager.get("hpma.related_archives", True))
            self.related_archives_action.setStatusTip(
                "On next open, include matching filenames from res, res2, res3, res4, etc.; no whole-folder scan."
            )
            self.related_archives_action.toggled.connect(
                lambda enabled: self.settings_manager.set("hpma.related_archives", enabled, save=True)
            )

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

            self.character_folders_action = menu.addAction(
                "Scan character folders", self.scan_character_folders
            )
            self.character_folders_action.setEnabled(False)
            self.character_folders_action.setStatusTip(
                "Find character-family folders from decoded model metadata."
            )

            menu.addSeparator()

            for viewer in ALL_VIEWERS:
                menu.addAction(
                    viewer.name,
                    lambda v=viewer: self._get_tab_window_for_viewer(v).show(),
                )

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

    def get_tab_windows(self) -> list[ViewerTabWindow]:
        """Get all viewer tab windows."""
        return list(self._viewer_windows.values())

    def _sync_character_filters(self):
        page = self._character_folders_window
        if page is not None:
            page.set_filters(
                self.name_filter_input.text(),
                self.filter_binary_filter.isChecked(),
                self.filter_text_filter.isChecked(),
                self.filter_slot_filter.isChecked(),
            )

    def _apply_archive_filter_if_visible(self):
        # The archive list is hidden while the Characters page is shown;
        # filtering it then would only read every entry for nothing.
        if self.entry_category_filter_combobox.currentData() != "characters":
            self.filter.apply_filter()

    def scan_character_folders(self):
        """Explicit Tools scan always refreshes the current archive's catalog."""
        npk_file = self.app.property("npk_file")
        if npk_file is None:
            return
        self._character_folders_window.set_archive(
            npk_file,
            getattr(self.config, "character_folder_roots", None),
            getattr(self.config, "entry_signature_name_map", {}),
        )
        self.open_character_folders()

    def _character_scan_started(self):
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        self.cancel_button.setVisible(True)
        self.cancel_button.setStatusTip("Cancel the character-folder scan; keep the archive loaded.")
        self.open_file_action.setEnabled(False)
        self.active_config.setEnabled(False)
        self.character_folders_action.setEnabled(False)
        self.extract_button_widget.setEnabled(False)

    @QtCore.Slot(str, int, int)
    def _character_scan_progress(self, stage, current, total):
        self.progress_bar.setFormat(stage + "… (%v/%m)")
        self.progress_bar.setRange(0, max(total, 1))
        self.progress_bar.setValue(current)

    def _character_scan_finished(self, _outcome):
        if self._archive_loading:
            return
        self.progress_bar.setVisible(False)
        self.cancel_button.setVisible(False)
        self.cancel_button.setStatusTip("Cancel loading the NPK file.")
        self.open_file_action.setEnabled(True)
        self.active_config.setEnabled(True)
        self.character_folders_action.setEnabled(self.app.property("npk_file") is not None)
        self.extract_button_widget.setEnabled(True)

    def open_character_folders(self):
        """Select the embedded Characters page in the main filter dropdown."""
        combo = self.entry_category_filter_combobox
        if combo.currentData() != "characters":
            combo.setCurrentIndex(combo.findData("characters"))
            return
        page = self._character_folders_window
        npk_file = self.app.property("npk_file")
        if page.npk_file is not npk_file:
            page.set_archive(
                npk_file,
                getattr(self.config, "character_folder_roots", None),
                getattr(self.config, "entry_signature_name_map", {}),
            )
        self.left_content.setCurrentWidget(page)
        self._sync_character_filters()

    def preview_character_entry(self, index):
        """Use the actual entry type to select the right-hand preview."""
        npk_file = self.app.property("npk_file")
        if npk_file is not None and index in npk_file.entries:
            self.preview_widget.set_file(npk_file.find_entry_by_id(index))

    def show_unresolved_character_asset(self, path):
        """Do not pretend a reference's metadata source is the requested mesh."""
        self.preview_widget.clear()
        self.preview_widget.message_label.setText(
            "Referenced asset is not present in the loaded archive.\n" + path
        )

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
        self._character_folders_window.cancel_scan(announce=False)

    def unload_npk(self):
        """Unload the NPK file."""

        if self._character_folders_window is not None:
            self._character_folders_window.cancel_scan(announce=False)
        if self.app.property("npk_file") is None:
            return

        self.setWindowTitle("NeoXtractor")
        self.app.setProperty("npk_file", None)
        self.list_widget.refresh_npk_file()
        self.extract_button_widget.setVisible(False)
        self.preview_widget.clear()
        self.unload_npk_action.setEnabled(False)
        if self._character_folders_window is not None:
            self._character_folders_window.set_archive(None)
        self.character_folders_action.setEnabled(False)
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

            get_logger().info(
                "Config changed to: %s", self.config.name if self.config else "None"
            )

    def load_npk(self, path: str):
        """Load an NPK file and populate the list widget."""

        self.unload_npk()

        self._loading_cancelled = False
        self._archive_loading = True

        self.setWindowTitle(f"NeoXtractor - {os.path.basename(path)}")

        self.open_file_action.setEnabled(False)
        self.active_config.setEnabled(False)
        self.progress_bar.setVisible(True)

        self.progress_bar.setFormat("Reading NPK file...")
        self.progress_bar.setRange(0, 0)

        self.list_widget.setDisabled(True)
        self.entry_category_filter_combobox.setEnabled(False)

        read_options = self.config.read_options if self.config else None
        if read_options is None:
            # No read options set, use default
            read_options = NPKReadOptions()

        ext = os.path.splitext(path)[1].lower()
        try:
            if ext == ".npk" and resource_location(path) is not None and self.config and \
                    "magic awakened" in self.config.name.lower() and \
                    self.related_archives_action.isChecked():
                archive_file = RelatedArchiveSet(path, read_options)
                self.setWindowTitle(
                    f"NeoXtractor - {os.path.basename(path)} ({len(archive_file.primary_sources)} same-name packs)"
                )
            elif ext in (".npk", ".expk"):
                archive_file = NPKFile(path, read_options)
            elif ext in (".idx", ".wpk"):
                archive_file = IDXWPKFile(path, read_options)
            else:
                raise ValueError(f"Unsupported archive type: {path}")
        except Exception as exc:
            self._loading_failed(str(exc))
            return

        self.app.setProperty("npk_file", archive_file)

        # A related-archive set reads its metadata on the worker thread first;
        # the list model is attached when that is done, so the view does not
        # query entries that are still being prepared.

        if isinstance(archive_file, RelatedArchiveSet):
            self.list_widget.setModel(None)
        else:
            self.list_widget.refresh_npk_file()

        self.progress_bar.setFormat("Loading entries... (%v/%m)")
        self.progress_bar.setRange(0, archive_file.file_count)
        self.progress_bar.setValue(0)

        def _load_entries():
            try:
                if isinstance(archive_file, RelatedArchiveSet):
                    archive_file.prepare(progress=self.loading_stage_signal.emit,
                                         cancelled=lambda: self._loading_cancelled)
                else:
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
            except CharacterScanCancelled:
                self.loading_complete_signal.emit()
            except Exception as exc:
                self.loading_error_signal.emit(str(exc))

        QtCore.QThreadPool.globalInstance().start(_load_entries)

        self.cancel_button.setVisible(True)

    @QtCore.Slot(str)
    def _loading_failed(self, error):
        # Restore the controls before reporting the error.
        self._loading_cancelled = True
        self._loading_complete()
        QtWidgets.QMessageBox.critical(self, "Could not load archive", error)

    @QtCore.Slot(int)
    def _update_progress(self, value):
        """Update progress bar value from the signal."""
        self.progress_bar.setValue(value)

    @QtCore.Slot(int)
    def _update_model(self, index):
        """Update model from the signal."""
        model = cast(NPKFileModel, self.list_widget.model())
        idx = model.index(index)
        model.get_filename(idx, invalidate_cache=True)
        self.list_widget.update(idx)

    @QtCore.Slot()
    def _loading_complete(self):
        """Handle completion of loading from the signal."""
        self._archive_loading = False
        if isinstance(self.app.property("npk_file"), RelatedArchiveSet) and not self._loading_cancelled:
            self.list_widget.refresh_npk_file()
        model = self.list_widget.model()
        if model is not None:
            model._file_names_cache.clear()
            model.layoutChanged.emit()
        # Restore normal selection behavior and style when loading is complete
        self.list_widget.setDisabled(False)
        self.open_file_action.setEnabled(True)
        self.active_config.setEnabled(True)
        self.entry_category_filter_combobox.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.cancel_button.setVisible(False)
        self.extract_button_widget.setVisible(True)
        self.unload_npk_action.setEnabled(True)
        if self._loading_cancelled:
            self.unload_npk()
        else:
            # This causes all the entries to be read. Making the cancelling not working and stuck the thread.
            self.character_folders_action.setEnabled(True)
            self._apply_archive_filter_if_visible()
            if self.entry_category_filter_combobox.currentData() == "characters":
                self.open_character_folders()
