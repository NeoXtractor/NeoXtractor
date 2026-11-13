"""Main window: owns the current archive, its entry list, and the probe scan.

Per-session state (the open archive, the active game config) lives here and
is passed explicitly to the widgets and models -- never on a global service
key.  Opening a file is one call, ``registry.open_file``, run on a background
thread (``gui.utils.load``) with its progress and Cancel in the status bar;
the GUI inspects no container format.
The background content probe is bound to the archive's lifetime: closing or
replacing the archive stops it.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Callable, cast

from PySide6 import QtCore, QtGui, QtWidgets

from neoxtractor.core.packages.base import Archive
from neoxtractor.core.packages.errors import OperationCancelled, PackageError
from neoxtractor.core.packages.registry import PackageResult, detect_format
from neoxtractor.core.utils.content import head_is_binary
from neoxtractor.core.utils.logger import get_logger
from neoxtractor.core.utils.result import Category
from neoxtractor.gui.app_state import AppState
from neoxtractor.gui.config import Config
from neoxtractor.gui.config_manager import ConfigManager
from neoxtractor.gui.models.archive_list_model import ArchiveListModel
from neoxtractor.gui.models.archive_source import ArchiveSource
from neoxtractor.gui.models.entry_filter import EntryFilter
from neoxtractor.gui.settings_manager import SettingsManager
from neoxtractor.gui.utils.export import export_many, export_single
from neoxtractor.gui.utils.load import LoadWorker
from neoxtractor.gui.utils.probe import ProbeWorker
from neoxtractor.gui.viewers import registry as viewer_registry
from neoxtractor.gui.viewers.base import make_entry_source
from neoxtractor.gui.widgets.archive_list import ArchiveList
from neoxtractor.gui.widgets.preview_widget import PreviewWidget
from neoxtractor.gui.windows.about_window import AboutWindow
from neoxtractor.gui.windows.config_manager import ConfigManagerWindow
from neoxtractor.gui.windows.settings_window import SettingsWindow
from neoxtractor.gui.windows.viewer_tab_window import ViewerTabWindow

OPEN_FILTER = (
    "Supported Files (*.npk *.expk *.idx *.wpk);;All Files (*)"
)


def _show_tab_handler(window: "MainWindow", viewer_name: str) -> Callable[[], None]:
    """Bind one viewer name into a no-arg Tools-menu callback."""

    def handler() -> None:
        window._tab_window(viewer_name).show()

    return handler


class MainWindow(QtWidgets.QMainWindow):
    """The application's main window."""

    def __init__(self) -> None:
        super().__init__()
        state = AppState.instance()
        self.config_manager: ConfigManager = state.configs
        self.settings_manager: SettingsManager = state.settings

        self.config: Config | None = None
        self._archive: Archive | None = None
        self._source: ArchiveSource | None = None
        self._filter = EntryFilter()
        self._waiting_key: int | None = None

        self._probe_thread: QtCore.QThread | None = None
        self._probe_worker: ProbeWorker | None = None
        self._probe_bar: QtWidgets.QProgressBar | None = None
        self._probe_timer: QtCore.QTimer | None = None
        self._probe_total = 0
        self._probe_shown = 0

        self._load_thread: QtCore.QThread | None = None
        self._load_worker: LoadWorker | None = None
        self._load_path: str = ""
        self._load_bar: QtWidgets.QProgressBar | None = None
        self._load_cancel_button: QtWidgets.QPushButton | None = None
        self._status_summary: str = ""

        self._viewer_windows: dict[str, ViewerTabWindow] = {}
        self._config_list_refreshing = False

        self.setWindowTitle("NeoXtractor")
        self._build_ui()
        self._build_menus()
        self.refresh_config_list()

    # --- UI construction ----------------------------------------------------

    def _build_ui(self) -> None:
        main_layout = QtWidgets.QHBoxLayout()
        control_layout = QtWidgets.QVBoxLayout()

        config_row = QtWidgets.QHBoxLayout()
        config_label = QtWidgets.QLabel("Active Config:")
        config_label.setStyleSheet("font-weight: bold;")
        config_label.setFixedWidth(100)
        config_row.addWidget(config_label)
        self.active_config = QtWidgets.QComboBox()
        self.active_config.setMinimumWidth(200)
        self.active_config.currentIndexChanged.connect(self.on_config_changed)
        config_row.addWidget(self.active_config)
        control_layout.addLayout(config_row)

        self.list_widget = ArchiveList(self)
        self.list_widget.preview_entry.connect(self._on_preview)
        self.list_widget.open_entry.connect(self._on_open)
        self.list_widget.open_entry_with.connect(self._on_open_with)
        self.list_widget.extract.connect(self._on_extract)
        control_layout.addWidget(self.list_widget, stretch=1)

        control_layout.addLayout(self._build_filter_section())

        control_widget = QtWidgets.QWidget()
        control_widget.setLayout(control_layout)
        control_widget.setMaximumWidth(500)
        main_layout.addWidget(control_widget, stretch=1)

        self.preview_widget = PreviewWidget(self)
        main_layout.addWidget(self.preview_widget, stretch=2)

        central = QtWidgets.QWidget()
        central.setLayout(main_layout)
        self.setCentralWidget(central)

    def _build_filter_section(self) -> QtWidgets.QVBoxLayout:
        section = QtWidgets.QVBoxLayout()

        title = QtWidgets.QLabel("Filters")
        title.setStyleSheet("font-weight: bold;")
        section.addWidget(title)

        self.name_filter_input = QtWidgets.QLineEdit()
        self.name_filter_input.setPlaceholderText("Search by filename...")
        self.name_filter_input.textChanged.connect(self._on_filter_text)
        section.addWidget(self.name_filter_input)

        checks = QtWidgets.QGridLayout()
        self.filter_text_filter = QtWidgets.QCheckBox("Text Files")
        self.filter_text_filter.setChecked(True)
        self.filter_text_filter.toggled.connect(
            lambda c: self._configure_filter(show_text=c)
        )
        checks.addWidget(self.filter_text_filter, 0, 0)

        self.filter_binary_filter = QtWidgets.QCheckBox("Binary Files")
        self.filter_binary_filter.setChecked(True)
        self.filter_binary_filter.toggled.connect(
            lambda c: self._configure_filter(show_binary=c)
        )
        checks.addWidget(self.filter_binary_filter, 0, 1)

        self.filter_encrypted_filter = QtWidgets.QCheckBox("Encrypted")
        self.filter_encrypted_filter.setChecked(True)
        self.filter_encrypted_filter.toggled.connect(
            lambda c: self._configure_filter(show_encrypted=c)
        )
        checks.addWidget(self.filter_encrypted_filter, 0, 2)

        self.filter_missing_filter = QtWidgets.QCheckBox("Missing")
        self.filter_missing_filter.toggled.connect(
            lambda c: self._configure_filter(show_missing=c)
        )
        checks.addWidget(self.filter_missing_filter, 1, 0)
        section.addLayout(checks)

        self.category_filter = QtWidgets.QComboBox()
        self.category_filter.addItem("All", "")
        for category in Category:
            self.category_filter.addItem(category.value, category.value)
        self.category_filter.currentIndexChanged.connect(self._on_filter_category)
        section.addWidget(self.category_filter)

        return section

    def _build_menus(self) -> None:
        file_menu = QtWidgets.QMenu("File", self)

        self.open_file_action = QtGui.QAction(
            self.style().standardIcon(
                QtWidgets.QStyle.StandardPixmap.SP_FileDialogDetailedView
            ),
            "Open File",
            self,
        )
        self.open_file_action.setStatusTip("Open a supported archive file.")
        self.open_file_action.setShortcut("Ctrl+O")
        self.open_file_action.triggered.connect(self._open_file_dialog)
        file_menu.addAction(self.open_file_action)

        self.unload_action = QtGui.QAction(
            self.style().standardIcon(
                QtWidgets.QStyle.StandardPixmap.SP_DialogCancelButton
            ),
            "Unload Archive",
            self,
        )
        self.unload_action.setStatusTip("Unload the current archive.")
        self.unload_action.setShortcut("Ctrl+W")
        self.unload_action.setEnabled(False)
        self.unload_action.triggered.connect(self.unload_archive)
        file_menu.addAction(self.unload_action)

        file_menu.addSeparator()

        config_action = QtGui.QAction(
            self.style().standardIcon(
                QtWidgets.QStyle.StandardPixmap.SP_FileDialogContentsView
            ),
            "Config Manager",
            self,
        )
        config_action.setMenuRole(QtGui.QAction.MenuRole.NoRole)
        config_action.setStatusTip("Open the Config Manager.")
        config_action.setShortcut("Ctrl+M")
        config_action.triggered.connect(self._open_config_manager)
        file_menu.addAction(config_action)

        app_menu: QtWidgets.QMenu | None = None
        if sys.platform == "darwin":
            app_menu = self.menuBar().addMenu("NeoXtractor")
        menubar = app_menu or self.menuBar()
        menubar.addMenu(file_menu)

        settings_action = menubar.addAction("Settings")
        settings_action.setStatusTip("Open Settings window.")
        settings_action.setMenuRole(QtGui.QAction.MenuRole.PreferencesRole)
        settings_action.triggered.connect(
            lambda: SettingsWindow(self.settings_manager, self).exec()
        )

        tools_menu = QtWidgets.QMenu("Tools", self)
        for info in viewer_registry.list_viewers():
            tools_menu.addAction(info.display_name, _show_tab_handler(self, info.name))
        self.menuBar().addMenu(tools_menu)

        menubar.addAction(
            "About", lambda: AboutWindow(self).exec()
        ).setMenuRole(QtGui.QAction.MenuRole.AboutRole)

    # --- config handling ----------------------------------------------------

    def refresh_config_list(self) -> None:
        """Rebuild the active-config combo from the config manager."""
        previous = self.config
        self._config_list_refreshing = True
        self.active_config.clear()
        for i, cfg in enumerate(self.config_manager.configs):
            self.active_config.addItem(cfg.name)
            if previous is not None and previous.name == cfg.name:
                self.active_config.setCurrentIndex(i)
        self._config_list_refreshing = False
        self.on_config_changed(self.active_config.currentIndex())

    def on_config_changed(self, index: int) -> None:
        if self._config_list_refreshing:
            return

        previous = self.config
        new_config = (
            None if index == -1 else self.config_manager.configs[index]
        )
        if previous == new_config:
            return

        if (
            previous is not None
            and self._archive is not None
            and QtWidgets.QMessageBox.warning(
                self,
                "Archive loaded",
                "Changing the config will unload the current archive.\n"
                "Are you sure you want to continue?",
                buttons=QtWidgets.QMessageBox.StandardButton.Ok
                | QtWidgets.QMessageBox.StandardButton.Cancel,
                defaultButton=QtWidgets.QMessageBox.StandardButton.Cancel,
            )
            == QtWidgets.QMessageBox.StandardButton.Cancel
        ):
            self.config = previous
            self.active_config.setCurrentIndex(
                self.active_config.findText(previous.name)
            )
            return

        self.unload_archive()
        self.config = new_config
        get_logger().info(
            "Config changed to: %s", new_config.name if new_config else "None"
        )

    # --- open / unload ------------------------------------------------------

    def _open_file_dialog(self) -> None:
        if self.config is None:
            QtWidgets.QMessageBox.warning(
                self,
                "No Config Selected",
                "Please select a config before opening a file.",
            )
            return
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Open Archive File", "", OPEN_FILTER
        )
        if path:
            self.open_archive(path)

    def open_archive(self, path: str) -> None:
        """Start a background open; the status bar carries progress + Cancel.

        The format is resolved first (a header-only matches probe), then the
        active config supplies that format's read options -- the GUI holds no
        format knowledge beyond this data-keyed lookup.  The heavy
        ``open_file`` runs on a worker thread; results land in
        ``_on_load_succeeded`` / ``_on_load_failed`` on the UI thread, so
        browsing (and the previous archive) stay responsive while loading.
        """
        if self._load_worker is not None:
            QtWidgets.QMessageBox.information(
                self,
                "Already loading",
                "Another file is still being opened.\n"
                "Cancel it (status bar) or wait for it to finish.",
            )
            return

        self.unload_archive()

        fmt = detect_format(path)
        options = self.config.options_for(fmt) if self.config is not None else None

        thread = QtCore.QThread(self)
        worker = LoadWorker(path, options, fmt)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progressed.connect(self._on_load_progress)
        worker.ask_requested.connect(self._on_load_ask)
        worker.succeeded.connect(self._on_load_succeeded)
        worker.failed.connect(self._on_load_failed)
        worker.succeeded.connect(thread.quit)
        worker.failed.connect(thread.quit)
        # The window owns the references; _finish_load drops them.  No
        # deleteLater (see _start_probe).
        self._load_thread = thread
        self._load_worker = worker
        self._load_path = path
        self._show_load_controls()
        thread.start()

    def _show_load_controls(self) -> None:
        """Put a progress bar and a Cancel button in the status bar."""
        bar = QtWidgets.QProgressBar(self)
        bar.setRange(0, 0)  # busy indicator until the first report
        bar.setMaximumWidth(220)
        self.statusBar().addPermanentWidget(bar)
        bar.show()
        button = QtWidgets.QPushButton("Cancel", self)
        button.clicked.connect(self._on_load_cancel_clicked)
        self.statusBar().addPermanentWidget(button)
        button.show()
        self._load_bar = bar
        self._load_cancel_button = button
        self.statusBar().showMessage(f"Opening {os.path.basename(self._load_path)}...")

    def _finish_load(self) -> None:
        """Tear down the load widgets and drop the thread/worker refs."""
        for widget in (self._load_bar, self._load_cancel_button):
            if widget is not None:
                self.statusBar().removeWidget(widget)
                widget.deleteLater()
        self._load_bar = None
        self._load_cancel_button = None
        self._load_worker = None
        self._load_thread = None
        self._load_path = ""
        self._status_summary = ""

    def _on_load_cancel_clicked(self) -> None:
        worker = self._load_worker
        if worker is not None:
            worker.request_cancel()
            self.statusBar().showMessage("Cancelling...")

    def _on_load_ask(self, path: str, reason: str) -> None:
        """Answer one driver consent request (runs on the UI thread).

        The worker parks until ``answer_ask``; this modal dialog is the
        answer path, and blocking the window keeps cancel/close from racing
        the question.  Five verdicts: Yes/No decide this file,
        YesToAll/NoToAll latch the rest of this open, Abort cancels it.
        ``Yes`` and ``YesToAll`` share a button role, so the answer is read
        from the clicked button's standard-button identity, never its role.
        """
        worker = self._load_worker
        if worker is None:
            return
        if reason == "outside_anchor":
            text = (
                "The archive being opened references a file outside its\n"
                "own folder:\n\n"
                f"{path}\n\n"
                "Allow reading this file?"
            )
        else:
            text = f"Allow reading this file?\n\n{path}\n\n({reason})"

        box = QtWidgets.QMessageBox(self)
        box.setIcon(QtWidgets.QMessageBox.Icon.Question)
        box.setWindowTitle("Access outside folder")
        box.setText(text)
        box.setStandardButtons(
            QtWidgets.QMessageBox.StandardButton.Yes
            | QtWidgets.QMessageBox.StandardButton.YesToAll
            | QtWidgets.QMessageBox.StandardButton.No
            | QtWidgets.QMessageBox.StandardButton.NoToAll
            | QtWidgets.QMessageBox.StandardButton.Abort
        )
        box.setDefaultButton(QtWidgets.QMessageBox.StandardButton.No)
        box.exec()
        verdict = {
            QtWidgets.QMessageBox.StandardButton.Yes: "accept",
            QtWidgets.QMessageBox.StandardButton.YesToAll: "accept_all",
            QtWidgets.QMessageBox.StandardButton.No: "reject",
            QtWidgets.QMessageBox.StandardButton.NoToAll: "reject_all",
            QtWidgets.QMessageBox.StandardButton.Abort: "abort",
        }[box.standardButton(box.clickedButton())]
        worker.answer_ask(verdict)

    def _on_load_progress(self, stage: str, done: int, total: int) -> None:
        name = os.path.basename(self._load_path)
        if total > 0:
            if self._load_bar is not None:
                self._load_bar.setRange(0, total)
                self._load_bar.setValue(done)
            self.statusBar().showMessage(f"Opening {name}: {stage} ({done}/{total})")
        else:
            self.statusBar().showMessage(f"Opening {name}: {stage}")

    def _on_load_succeeded(self, result: object) -> None:
        assert isinstance(result, PackageResult)
        base = os.path.basename(self._load_path)
        self._finish_load()
        self._archive = result.archive
        self._source = ArchiveSource(result.archive)
        self.list_widget.set_source(self._source)
        self.setWindowTitle(f"NeoXtractor - {base}")
        self.unload_action.setEnabled(True)
        self._apply_filter()
        self._start_probe()
        summary = (
            f"Opened {result.format.upper()}: "
            f"{len(list(result.archive.entries()))} entries, "
            f"{len(result.members)} file(s)"
        )
        if result.warnings:
            summary += f" ({len(result.warnings)} warning(s))"
            for warning in result.warnings:
                get_logger().warning("open warning: %s", warning)
        self.statusBar().showMessage(summary)
        self._status_summary = summary
        get_logger().info(summary)

    def _on_load_failed(self, error: object) -> None:
        assert isinstance(error, BaseException)
        name = os.path.basename(self._load_path)
        self._finish_load()
        self.statusBar().clearMessage()
        if isinstance(error, OperationCancelled):
            get_logger().info("Open cancelled: %s", name)
            return
        get_logger().error("Failed to open %s: %s", name, error)
        QtWidgets.QMessageBox.critical(
            self, "Open Failed", f"Could not open {name}:\n\n{error}"
        )

    def unload_archive(self) -> None:
        """Close the current archive (stops the probe) and reset the UI."""
        if self._archive is None:
            return
        # _stop_probe closes the archive first (see there): the join then
        # returns in milliseconds instead of blocking the UI.
        self._stop_probe()
        self._archive = None
        self._source = None
        self._waiting_key = None
        self.list_widget.set_source(None)
        self.preview_widget.clear()
        self.setWindowTitle("NeoXtractor")
        self.unload_action.setEnabled(False)
        self.statusBar().clearMessage()
        self._status_summary = ""
        get_logger().info("Archive unloaded.")

    # --- probe scan ---------------------------------------------------------

    def _start_probe(self) -> None:
        if self._archive is None or self._source is None:
            return
        bar = QtWidgets.QProgressBar(self)
        bar.setRange(0, len(self._source))
        bar.setMaximumWidth(220)
        self.statusBar().addPermanentWidget(bar)
        bar.show()
        self._probe_bar = bar
        thread = QtCore.QThread(self)
        worker = ProbeWorker(self._archive, len(self._source))
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.scan_finished.connect(self._on_scan_finished)
        worker.scan_finished.connect(thread.quit)
        # The window owns the references; _stop_probe drops them.  No
        # deleteLater: a self-destructing C++ object behind a live Python
        # reference makes later calls raise.
        self._probe_thread = thread
        self._probe_worker = worker
        self._probe_total = len(self._source)
        self._probe_shown = 0
        # Drain the worker's buffer on a fixed cadence instead of per-entry
        # queued signals: one event per batch keeps the loop responsive, and
        # a progress bar and a list need no finer cadence.
        timer = QtCore.QTimer(self)
        timer.setInterval(25)
        timer.timeout.connect(self._drain_probe_batch)
        timer.start()
        self._probe_timer = timer
        thread.start()

    def _stop_probe(self) -> None:
        timer = self._probe_timer
        self._probe_timer = None
        if timer is not None:
            timer.stop()
        # Close the archive first (idempotent): the running scan raises on
        # its next get_res and exits by itself, so the thread join below
        # returns in milliseconds.  Closing here keeps "probe stopped"
        # meaning "its data source is gone".
        if self._archive is not None:
            try:
                self._archive.close()
            except Exception as exc:  # a stubborn close must not trap the UI
                get_logger().warning("Error closing archive: %s", exc)
        self._finish_probe_bar()
        thread = self._probe_thread
        self._probe_thread = None
        self._probe_worker = None
        if thread is None:
            return
        try:
            thread.quit()
            thread.wait(3000)
        except RuntimeError:  # the C++ object is already gone
            pass

    def _drain_probe_batch(self) -> None:
        """Apply every result the worker buffered since the last tick.

        Runs on the drain timer.  Row updates are coalesced behind
        setUpdatesEnabled, and a visibility re-check is skipped whenever the
        filter's content predicates could not change a row's state (the
        common case: no category set, text and binary both shown -- then a
        probe only fills the display, never hides the row).
        """
        worker = self._probe_worker
        if worker is None or self._source is None:
            return
        batch, done = worker.take_batch()
        if not batch and done == self._probe_shown:
            return

        content_can_hide = self._filter.content_can_hide()
        model = self.list_widget.model()
        list_model = model if isinstance(model, ArchiveListModel) else None
        self.list_widget.setUpdatesEnabled(False)
        try:
            for key, outcome in batch:
                self._source.record_probe(
                    key,
                    ext=outcome.ext,
                    category=outcome.category,
                    is_text=outcome.is_text,
                    unreadable=outcome.unreadable,
                )
                if list_model is not None:
                    list_model.refresh_row(key)
                if content_can_hide:
                    self._update_row_visibility(key)
                if self._waiting_key == key and self._archive is not None:
                    self._waiting_key = None
                    extension = self._source.usable_extension(key)
                    name = self._source.display_name(key)
                    self.preview_widget.set_entry(
                        self._archive, key, name, extension
                    )
        finally:
            self.list_widget.setUpdatesEnabled(True)

        self._probe_shown = done
        if self._probe_bar is not None:
            self._probe_bar.setRange(0, self._probe_total)
            self._probe_bar.setValue(done)
            self.statusBar().showMessage(
                f"Detecting content... {done}/{self._probe_total}"
            )

    def _finish_probe_bar(self) -> None:
        """Remove the probe progress widget (scan end, unload, or teardown)."""
        bar = self._probe_bar
        self._probe_bar = None
        if bar is not None:
            self.statusBar().removeWidget(bar)
            bar.deleteLater()

    def _on_scan_finished(self) -> None:
        # Drain whatever landed after the last timer tick, then tear down.
        timer = self._probe_timer
        self._probe_timer = None
        if timer is not None:
            timer.stop()
        self._drain_probe_batch()
        self._finish_probe_bar()
        if self._status_summary:
            self.statusBar().showMessage(self._status_summary)
        else:
            self.statusBar().clearMessage()

    # --- selection / preview ------------------------------------------------

    def _on_preview(self, row: int, key: int, name: str) -> None:
        if self._source is None or self._archive is None:
            return
        extension = self._source.usable_extension(row)
        if extension or self._source.is_probed(row):
            self._waiting_key = None
            self.preview_widget.set_entry(self._archive, key, name, extension)
        else:
            self._waiting_key = key
            self.preview_widget.set_waiting(name)

    def _pick_viewer(
        self, row: int, key: int, viewer_name: str | None
    ) -> viewer_registry.ViewerInfo | None:
        """Resolve the ViewerInfo for one entry: explicit choice, extension,
        or content-chosen fallback (header read once, then released)."""
        if viewer_name is not None:
            return viewer_registry.info_for(viewer_name)
        assert self._source is not None and self._archive is not None
        extension = self._source.usable_extension(row)
        if extension:
            info = viewer_registry.find_specialist(extension)
            if info is not None:
                return info
        # Classify from a bounded header (head_is_binary reads its own window
        # and rewinds), matching the probe path -- never materialize the whole
        # payload just to decide text vs binary.
        with self._archive.get_res(key) as stream:
            is_text = not head_is_binary(stream)
        return viewer_registry.find_viewer_class(extension, is_text=is_text)

    def _open_in(self, row: int, key: int, name: str, viewer_name: str | None) -> None:
        if self._archive is None:
            return
        # EOFError subclasses (MissingIndexedResourceError) cover MISSING;
        # PackageError covers CORRUPTED and every other content fault a
        # decoded read surfaces (PayloadDecodeError) or a reference the
        # format cannot resolve.  None may escape this slot unreported.
        try:
            info = self._pick_viewer(row, key, viewer_name)
            if info is None:
                QtWidgets.QMessageBox.warning(
                    self, "No Viewer", f"No viewer available for {name!r}."
                )
                return
            source = make_entry_source(self._archive, key, name, input=info.input)
            window = self._tab_window(info.name)
            window.load_source(source)
        except (ValueError, EOFError, PackageError) as exc:
            QtWidgets.QMessageBox.critical(
                self, "Error", cast(str, exc.args[0] or str(exc))
            )
            return
        window.show()

    def _on_open(self, row: int, key: int, name: str) -> None:
        self._open_in(row, key, name, None)

    def _on_open_with(self, row: int, key: int, name: str, viewer_name: str) -> None:
        self._open_in(row, key, name, viewer_name)

    def _tab_window(self, viewer_name: str) -> ViewerTabWindow:
        cached = self._viewer_windows.get(viewer_name)
        if cached is not None:
            return cached
        info = viewer_registry.info_for(viewer_name)
        assert info is not None  # only called with discovered viewer names
        window = ViewerTabWindow(info, self)
        self._viewer_windows[viewer_name] = window
        return window

    # --- extraction ---------------------------------------------------------

    def _on_extract(self, rows: list[int], decoded: bool) -> None:
        if self._source is None or self._archive is None:
            return
        pairs = [(self._source.entry_at(row).key, self._source.display_name(row)) for row in rows]
        if len(pairs) == 1:
            export_single(self, self._archive, pairs[0][0], pairs[0][1], decoded)
        else:
            export_many(self, self._archive, pairs, decoded)

    # --- filtering ----------------------------------------------------------

    def _configure_filter(self, **kwargs: Any) -> None:
        self._filter.configure(**kwargs)
        self._apply_filter()

    def _on_filter_text(self, text: str) -> None:
        self._filter.configure(text=text)
        self._apply_filter()

    def _on_filter_category(self, index: int) -> None:
        value = self.category_filter.itemData(index)
        self._filter.configure(categories=set([value]) if value else set())
        self._apply_filter()

    def _apply_filter(self) -> None:
        if self._source is None:
            return
        for row in range(len(self._source)):
            self._update_row_visibility(row)

    def _update_row_visibility(self, row: int) -> None:
        if self._source is None:
            return
        hidden = not self._filter.matches(self._source, row)
        self.list_widget.setRowHidden(row, hidden)

    # --- config manager -----------------------------------------------------

    def _open_config_manager(self) -> None:
        dialog = ConfigManagerWindow(
            self.config_manager,
            get_current_config=lambda: self.config,
            archive_loaded=lambda: self._archive is not None,
            parent=self,
        )
        dialog.exec()
        # Only the user layer persists; seeds live on disk and are re-read
        # each launch, so they never belong in settings.
        AppState.instance().settings.set(
            "gameconfigs", self.config_manager.to_user_dicts(), True
        )
        self.refresh_config_list()

    # --- window lifecycle ---------------------------------------------------

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        # A load still in flight must be cancelled and drained before the
        # window goes away, or its worker keeps firing into a dead window.
        if self._load_worker is not None:
            self._load_worker.request_cancel()
            thread = self._load_thread
            self._finish_load()
            if thread is not None:
                try:
                    thread.quit()
                    thread.wait(3000)
                except RuntimeError:  # the C++ object is already gone
                    pass
        force_close = False
        for window in self._viewer_windows.values():
            if force_close:
                window.close()
                continue
            if window.isVisible():
                if (
                    QtWidgets.QMessageBox.warning(
                        self,
                        "Close Viewers",
                        "There are still viewer windows open.\n"
                        "Are you sure you want to quit?",
                        buttons=QtWidgets.QMessageBox.StandardButton.Ok
                        | QtWidgets.QMessageBox.StandardButton.Cancel,
                        defaultButton=QtWidgets.QMessageBox.StandardButton.Cancel,
                    )
                    == QtWidgets.QMessageBox.StandardButton.Ok
                ):
                    force_close = True
                    window.close()
                else:
                    event.ignore()
                    return
        self.unload_archive()
        super().closeEvent(event)
