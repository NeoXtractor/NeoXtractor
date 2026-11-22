"""
The Characters category of the main window (the character-folder tree beside the file list), the "Scan character
folders" tool, and the HPMA switch that opens a pack together with its same-name copies in res, res2, res3...
Attached by gui.addons.
"""

import os

from PySide6 import QtCore, QtGui, QtWidgets

from core.npk.related_archives import RelatedArchiveSet, resource_location
from gui import addons
from gui.windows.character_folders_window import CharacterFoldersWindow

CHARACTERS = "characters"
RELATED_SETTING = "hpma.related_archives"


def install(app) -> None:
    """Add the Characters page and its tools to every main window (once each)."""
    for window in app.topLevelWidgets():
        if (
            hasattr(window, "entry_category_filter_combobox")
            and getattr(window, "_character_folders_window", None) is None
        ):
            _CharactersPage(window)


class _CharactersPage(QtCore.QObject):
    """Keeps the Characters page in step with the main window: its archive, filters, progress and menus."""

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.combo = window.entry_category_filter_combobox
        self.page = page = CharacterFoldersWindow(None, None, window)
        window._character_folders_window = page
        self.stack = addons.left_stack(window)
        self.stack.addWidget(page)
        addons.PAGES.add(CHARACTERS)
        self.combo.addItem("Characters", CHARACTERS)
        self.loading = False

        save = QtWidgets.QPushButton("Save Visible…")
        save.setToolTip("Save the assets the tree shows (with the current filters), linked textures included.")
        save.clicked.connect(page.extract_visible)
        page.layout().addWidget(save)

        page.open_entry_with.connect(window.list_widget.open_entry_with)
        page.scan_started.connect(self._scan_started)
        page.scan_progress.connect(window._loading_stage)
        page.scan_finished.connect(self._scan_finished)
        page.preview_entry_requested.connect(self._preview)
        page.unresolved_requested.connect(self._unresolved)
        self.combo.currentIndexChanged.connect(lambda _index: self.combo.currentData() == CHARACTERS and self.show())
        for signal in (
            window.name_filter_input.textChanged,
            window.filter_binary_filter.toggled,
            window.filter_text_filter.toggled,
            window.filter_slot_filter.toggled,
        ):
            signal.connect(self._sync_filters)
        window.cancel_button.clicked.connect(lambda: page.scan_busy and page.cancel_scan())
        window.archive_openers.append(self._open_related)
        load_npk = window.load_npk

        def load_with_page(path):
            page.cancel_scan(announce=False)
            page.set_archive(None)
            self.loading = True
            self.combo.setEnabled(False)  # the category stays put while the entries load
            load_npk(path)
            if window.app.property("npk_file") is None:  # could not be opened
                self._loaded()

        window.load_npk = load_with_page
        for signal in (window.loading_complete_signal, window.loading_error_signal):
            signal.connect(self._loaded)
        window.unload_npk_action.triggered.connect(self._unloaded)
        window.installEventFilter(self)
        self._add_menu_actions()

    def _add_menu_actions(self):
        window = self.window
        tools = addons.menu(window, "Tools")
        window.character_folders_action = QtGui.QAction("Scan character folders", window)
        window.character_folders_action.setStatusTip("Find character-family folders from decoded model metadata.")
        window.character_folders_action.setEnabled(False)
        window.character_folders_action.triggered.connect(self.scan)
        if tools is not None:
            first = tools.actions()[0] if tools.actions() else None
            tools.insertAction(first, window.character_folders_action)
            tools.insertSeparator(first)
        files = addons.menu(window, "File")
        related = window.related_archives_action = QtGui.QAction(
            "Load all same-name related copies together (HPMA)", window
        )
        related.setCheckable(True)
        related.setChecked(window.settings_manager.get(RELATED_SETTING, True))
        related.setStatusTip(
            "On next open, include matching filenames from res, res2, res3, res4, etc.; no whole-folder scan."
        )
        related.toggled.connect(lambda enabled: window.settings_manager.set(RELATED_SETTING, enabled, save=True))
        if files is not None:
            actions = files.actions()
            at = actions.index(window.open_file_action) + 1 if window.open_file_action in actions else len(actions)
            files.insertAction(actions[at] if at < len(actions) else None, related)

    def _open_related(self, path, options):
        """Magic Awakened packs in a res folder open with their same-name copies from the other res folders."""
        window = self.window
        config = window.config
        if not (
            path.lower().endswith(".npk")
            and window.related_archives_action.isChecked()
            and config is not None
            and "magic awakened" in config.name.lower()
            and resource_location(path) is not None
        ):
            return None
        archive = RelatedArchiveSet(path, options)
        window.setWindowTitle(
            f"NeoXtractor - {os.path.basename(path)} ({len(archive.primary_sources)} same-name packs)"
        )
        return archive

    def show(self, refresh=False):
        """Show the page with the loaded archive (scanned once per archive, or again when refresh is set)."""
        window, page = self.window, self.page
        window.filter.filter_type = None
        window.mesh_biped_head_filter_checkbox.setVisible(False)
        window.extract_button_widget.hide()  # it acts on the file list; the page has its own Save button
        archive = window.app.property("npk_file")
        if refresh or page.npk_file is not archive:
            page.set_archive(
                archive,
                getattr(window.config, "character_folder_roots", None),
                getattr(window.config, "entry_signature_name_map", {}),
            )
        self.stack.setCurrentWidget(page)
        self._sync_filters()

    def scan(self):
        """Tools > Scan character folders: scan the loaded archive again and show the page."""
        if self.window.app.property("npk_file") is None:
            return
        if self.combo.currentData() == CHARACTERS:
            self.show(refresh=True)
        else:
            self.page.set_archive(None)  # so showing the page scans afresh
            self.combo.setCurrentIndex(self.combo.findData(CHARACTERS))

    def _sync_filters(self, *_args):
        window = self.window
        self.page.set_filters(
            window.name_filter_input.text(),
            window.filter_binary_filter.isChecked(),
            window.filter_text_filter.isChecked(),
            window.filter_slot_filter.isChecked(),
        )

    def _loaded(self, *_args):
        self.loading = False
        self.combo.setEnabled(True)
        loaded = self.window.app.property("npk_file") is not None
        self.window.character_folders_action.setEnabled(loaded)
        if not loaded:
            self.page.set_archive(None)
        elif self.combo.currentData() == CHARACTERS:
            self.show()

    def _unloaded(self):
        self.page.cancel_scan(announce=False)
        self.page.set_archive(None)
        self.window.character_folders_action.setEnabled(False)

    def _scan_started(self):
        window = self.window
        window.progress_bar.setVisible(True)
        window.progress_bar.setRange(0, 1)
        window.progress_bar.setValue(0)
        window.cancel_button.setVisible(True)
        window.cancel_button.setStatusTip("Cancel the character-folder scan; keep the archive loaded.")
        for widget in (window.open_file_action, window.active_config, window.character_folders_action):
            widget.setEnabled(False)

    def _scan_finished(self, _outcome):
        if self.loading:
            return  # the archive's own loading still owns the progress bar
        window = self.window
        window.progress_bar.setVisible(False)
        window.cancel_button.setVisible(False)
        window.cancel_button.setStatusTip("Cancel loading the NPK file.")
        window.open_file_action.setEnabled(True)
        window.active_config.setEnabled(True)
        window.character_folders_action.setEnabled(window.app.property("npk_file") is not None)

    def _preview(self, index):
        """Preview a picked asset with the viewer its real type asks for."""
        archive = self.window.app.property("npk_file")
        if archive is not None and index in archive.entries:
            self.window.preview_widget.set_file(archive.find_entry_by_id(index))

    def _unresolved(self, path):
        """A reference whose asset is not in the loaded archive: say so instead of previewing its source."""
        preview = self.window.preview_widget
        preview.clear()
        preview.message_label.setText("Referenced asset is not present in the loaded archive.\n" + path)

    def eventFilter(self, watched, event):
        if watched is self.window and event.type() == QtCore.QEvent.Type.Close:
            self.page.cancel_scan(announce=False)
        return False
