"""
The "Stages" entry of the main window's category list: the stage scenes of the opened pack (scene.npk or a DLC
scene pack), grouped by stage. Selected scenes export as GLB, PMX or glTF (with their textures), and one scene at
a time opens in the Scene Viewer. The models and textures a scene places are read from the game's other packs.
"""

from __future__ import annotations

import threading
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from core.npk.npk_file import NPKReadOptions
from core.npk.stage_export import PackIndex, StageExporter, game_layers, game_root_for, read_options_for, resource_dirs
from gui import addons
from gui.utils import scan_memory
from gui.utils.file_list_theme import tree_icons
from gui.utils.page_filters import watch_page_filters

STAGES_DATA = "stages"
SETTING_GAME_FOLDER = "stage_export.game_folder"  # remembered for the other exports' fallback
NO_STAGES_HINT = "Open scene.npk, or a DLC scene pack (res_dlc_…), to see its stages."
SETTING_OUTPUT_FOLDER = "stage_export.output_folder"
SETTING_FORMAT = "stage_export.format"


def export_stage_file(index, scene, output, kind, include_hidden, progress, cancelled) -> Path:
    """Write a stage as one <output>/<stage>/<stage>.glb or .pmx, with a short report beside it."""
    from core.mesh_converter import model_export
    from core.npk.stage_export import StageLoader

    loader = StageLoader(
        index, progress=progress, cancelled=cancelled, include_hidden=include_hidden, max_texture_size=8192
    )
    objects = loader.load(scene)
    if not objects:
        raise ValueError("the scene has no visible models")
    stage = Path(scene.replace("\\", "/")).stem
    folder = Path(output) / stage
    progress(f"Writing {stage}.{kind}…", len(objects), len(objects))
    items = [
        {
            "name": obj["name"],
            "mesh": obj["mesh"],
            "matrix": obj["matrix"],
            "parts": obj["parts"],
            "animation": obj.get("animation"),
        }
        for obj in objects
    ]
    target = folder / f"{stage}.{kind}"
    if kind == "glb":
        model_export.write_glb(items, target)
    else:
        model_export.write_pmx(items, target)
    untextured = sum(1 for obj in objects for part in obj["parts"] if part.get("image") is None)
    summary = [f"Stage: {scene}", f"Placed objects: {len(objects)}", f"Parts without a texture: {untextured}"]
    (folder / "export_report.txt").write_text("\n".join(summary + [""] + sorted(set(loader.report))), encoding="utf-8")
    return target


def _size_text(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    return f"{max(size, 0) / 1024:.0f} KB"


class _SortableItem(QtWidgets.QTreeWidgetItem):
    """Sorts the Scenes and Size columns by their numbers, the rest by text."""

    def __lt__(self, other):
        column = self.treeWidget().sortColumn() if self.treeWidget() is not None else 0
        mine = self.data(column, QtCore.Qt.ItemDataRole.UserRole) if column else None
        theirs = other.data(column, QtCore.Qt.ItemDataRole.UserRole) if column else None
        if isinstance(mine, int) and isinstance(theirs, int):
            return mine < theirs
        return self.text(column).lower() < other.text(column).lower()


class _Signals(QtCore.QObject):
    scenes_ready = QtCore.Signal(int, object, str)  # generation, scenes, error
    progress = QtCore.Signal(str, int, int)
    finished = QtCore.Signal(object, str)  # written files, error


class StagesPage(QtWidgets.QWidget):
    """Lists the scenes of the opened pack and exports selected ones."""

    def __init__(self, window: QtWidgets.QMainWindow):
        super().__init__(window)
        self.setObjectName("stagesPage")
        self.window_ = window
        self.game: Path | None = None
        self.pack: str | None = None  # stem of the opened pack: "scene", "res_dlc1_hogsmeade", ...
        self.index: PackIndex | None = None
        self._all_scenes: list[str] = []
        self._sizes: dict[str, int] = {}
        self._generation = 0
        self._cancel = threading.Event()
        self._busy = False
        self._scanning = False  # the stage list of self.game is being read
        self._last_output: Path | None = None

        self.signals = _Signals()
        self.signals.scenes_ready.connect(self._scenes_ready)
        self.signals.progress.connect(self._progress)
        self.signals.finished.connect(self._finished)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.folder_label = QtWidgets.QLabel()
        self.folder_label.setWordWrap(True)
        layout.addWidget(self.folder_label)

        self.tree = QtWidgets.QTreeWidget()
        self._folder_icon, self._file_icon = tree_icons(self.tree)
        self.tree.setHeaderLabels(["Stage", "Scenes", "Size"])
        self.tree.sortByColumn(0, QtCore.Qt.SortOrder.AscendingOrder)
        self.tree.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.header().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.tree.itemSelectionChanged.connect(self._update_buttons)
        self.tree.itemDoubleClicked.connect(
            lambda item, _column: self.export_selected() if item.childCount() == 0 else None
        )
        self.tree.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._stage_menu)
        layout.addWidget(self.tree, 1)

        options_row = QtWidgets.QHBoxLayout()
        self.hidden_checkbox = QtWidgets.QCheckBox("Include collision / navigation helpers")
        options_row.addWidget(self.hidden_checkbox, 1)
        options_row.addWidget(QtWidgets.QLabel("Format:"))
        self.format_combo = QtWidgets.QComboBox()
        self.format_combo.addItem("GLB (single file)", "glb")
        self.format_combo.addItem("PMX + textures", "pmx")
        self.format_combo.addItem("glTF + .bin + textures", "gltf")
        self.format_combo.setToolTip("GLB packs the whole stage and its textures into one file.")
        saved = self._setting(SETTING_FORMAT, "glb")
        self.format_combo.setCurrentIndex(max(0, self.format_combo.findData(saved)))
        self.format_combo.currentIndexChanged.connect(
            lambda _i: self._store(SETTING_FORMAT, self.format_combo.currentData())
        )
        options_row.addWidget(self.format_combo)
        layout.addLayout(options_row)

        self.status = QtWidgets.QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.hide()
        layout.addWidget(self.progress_bar)

        buttons = QtWidgets.QHBoxLayout()
        self.export_button = QtWidgets.QPushButton("Export Selected…")
        self.export_button.clicked.connect(self.export_selected)
        self.view_button = QtWidgets.QPushButton("Open in Scene Viewer")
        self.view_button.setToolTip("Load the selected scene into the Scene Viewer with its textures matched.")
        self.view_button.clicked.connect(self.view_selected)
        self.open_button = QtWidgets.QPushButton("Open Output Folder")
        self.open_button.clicked.connect(self._open_output)
        self.open_button.hide()
        self.cancel_button = QtWidgets.QPushButton("Cancel")
        self.cancel_button.clicked.connect(self._cancel.set)
        self.cancel_button.hide()
        buttons.addWidget(self.export_button)
        buttons.addWidget(self.view_button)
        buttons.addWidget(self.open_button)
        buttons.addWidget(self.cancel_button)
        layout.addLayout(buttons)
        self._update_buttons()

    def _settings(self):
        app = QtWidgets.QApplication.instance()
        return app.property("settings_manager") if app is not None else None

    def _setting(self, key, default=None):
        settings = self._settings()
        try:
            return settings.get(key, default) if settings is not None else default
        except Exception:
            return default

    def _store(self, key, value):
        settings = self._settings()
        if settings is not None:
            settings.set(key, value, True)

    def _read_options(self) -> NPKReadOptions:
        config = getattr(self.window_, "config", None)
        options = getattr(config, "read_options", None)
        if config is not None and "magic awakened" in str(getattr(config, "name", "")).lower() and options is not None:
            return options
        app = QtWidgets.QApplication.instance()
        return read_options_for(app.property("npk_file") if app is not None else None)

    def activate(self):
        """Called whenever the Stages category is selected or an archive finishes loading."""
        game = pack = None
        app = QtWidgets.QApplication.instance()
        archive = app.property("npk_file") if app is not None else None
        if archive is not None and getattr(archive, "file_path", None):
            game = game_root_for(archive.file_path)
            pack = Path(archive.file_path).stem.lower()
        if game is not None:
            self._store(SETTING_GAME_FOLDER, str(game))
        self.set_game(game, pack)

    def set_game(self, game: Path | None, pack: str | None = None):
        if game is not None and not resource_dirs(Path(game)):
            game = None
        same_game = game is not None and Path(game) == self.game and (self.index is not None or self._scanning)
        self.pack = pack if game is not None else None
        if same_game:
            # The game's index is kept: only the list changes with the opened pack
            if not self._scanning:
                self._fill()
            return
        self.game = Path(game) if game else None
        self.index = None
        self._all_scenes, self._sizes = [], {}
        self._scanning = False
        self.tree.clear()
        if self.game is None:
            self.folder_label.setText(NO_STAGES_HINT)
            self.status.setText("")
            self._update_buttons()
            return
        self.folder_label.setText("")
        self.status.setText("Reading stage list…")
        self._generation += 1
        generation, options, game = self._generation, self._read_options(), self.game
        self._scanning = True

        def scan():
            try:
                index = PackIndex(game, options)
                scenes = index.scenes()
                sizes = {scene: index.scene_size(scene) for scene in scenes}
                self.signals.scenes_ready.emit(generation, (index, scenes, sizes), "")
            except Exception as exc:
                self.signals.scenes_ready.emit(generation, None, str(exc))

        scan_memory.begin()  # the pack index this builds stays in memory
        threading.Thread(target=scan, daemon=True).start()
        self._update_buttons()

    def _scenes_ready(self, generation, result, error):
        scan_memory.end()
        if generation != self._generation:
            return
        self._scanning = False
        if error or result is None:
            self.status.setText(f"Could not read the stage list: {error}")
            return
        self.index, self._all_scenes, self._sizes = result
        self._fill()

    def _fill(self):
        """List the scenes that live in the opened pack (the same pack in every res folder counts as one)."""
        self.tree.clear()
        scenes = [scene for scene in self._all_scenes if self.pack and scene.split("\\")[0].lower() == self.pack]
        sizes = self._sizes
        if not scenes:
            self.folder_label.setText(NO_STAGES_HINT)
            self.status.setText("")
            self._update_buttons()
            return
        layers = game_layers(self.game)
        self.folder_label.setText(f"Stages in {self.pack}.npk  ({'  +  '.join(str(layer) for layer in layers)})")
        groups: dict[tuple[str, str], list[str]] = {}
        for scene in scenes:
            parts = scene.split("\\")
            if "scene" in parts[:-1]:
                at = parts.index("scene")
                stage = parts[at + 1] if len(parts) > at + 2 else Path(scene.replace("\\", "/")).stem
                pack = parts[0] if at > 0 else ""  # DLC packs keep their own scene folder
            else:
                # e.g. res_dlc_x\levelsets\<stage>\<stage>.scn: file it under the same stage
                stage = parts[-2] if len(parts) > 1 else Path(scene.replace("\\", "/")).stem
                pack = parts[0] if len(parts) > 2 and parts[0].startswith("res_dlc") else ""
            groups.setdefault((stage, pack), []).append(scene)
        self.tree.setSortingEnabled(False)
        self.tree.setUpdatesEnabled(False)
        for (stage, pack), members in groups.items():
            largest = max(sizes.get(scene, 0) for scene in members)
            parent = _SortableItem([stage, str(len(members)), _size_text(largest)])
            parent.setData(1, QtCore.Qt.ItemDataRole.UserRole, len(members))
            parent.setData(2, QtCore.Qt.ItemDataRole.UserRole, largest)
            parent.setToolTip(2, "Size of the stage's largest scene file; bigger usually means more objects.")
            parent.setIcon(0, self._folder_icon)
            for scene in members:
                parts = scene.split("\\")
                at = len(parts) - 1 - parts[::-1].index(stage) if stage in parts[:-1] else len(parts) - 2
                label = "\\".join(parts[at + 1 :])
                if "scene" not in parts[:-1]:
                    label += f"   (from {parts[1] if len(parts) > 2 else parts[0]})"
                child = _SortableItem([label, "", _size_text(sizes.get(scene, 0))])
                child.setData(0, QtCore.Qt.ItemDataRole.UserRole, scene)
                child.setData(2, QtCore.Qt.ItemDataRole.UserRole, sizes.get(scene, 0))
                child.setToolTip(0, scene)
                child.setIcon(0, self._file_icon)
                parent.addChild(child)
            self.tree.addTopLevelItem(parent)
        self.tree.setUpdatesEnabled(True)
        self.tree.setSortingEnabled(True)
        self.status.setText(
            f"{len(groups)} stages, {len(scenes)} scenes. "
            "Click a column header to sort; select scenes (or a stage) and export."
        )
        self.apply_filter(
            getattr(self.window_, "name_filter_input", None).text()
            if hasattr(self.window_, "name_filter_input")
            else ""
        )
        self._update_buttons()

    def apply_filter(self, text: str):
        text = (text or "").strip().lower()
        for i in range(self.tree.topLevelItemCount()):
            parent = self.tree.topLevelItem(i)
            stage_match = text in parent.text(0).lower()
            visible = 0
            for j in range(parent.childCount()):
                child = parent.child(j)
                show = not text or stage_match or text in child.text(0).lower()
                child.setHidden(not show)
                visible += show
            parent.setHidden(visible == 0)

    def _selected_scenes(self) -> list[str]:
        scenes = []
        for item in self.tree.selectedItems():
            items = [item] if item.childCount() == 0 else [item.child(i) for i in range(item.childCount())]
            for child in items:
                scene = child.data(0, QtCore.Qt.ItemDataRole.UserRole)
                if scene and not child.isHidden() and scene not in scenes:
                    scenes.append(scene)
        return scenes

    def _update_buttons(self):
        self.export_button.setEnabled(not self._busy and self.index is not None and bool(self._selected_scenes()))
        self.view_button.setEnabled(self.index is not None and len(self._selected_scenes()) == 1)

    def export_selected(self):
        scenes = self._selected_scenes()
        if self._busy or self.index is None or not scenes:
            return
        start = self._setting(SETTING_OUTPUT_FOLDER, "") or ""
        output = QtWidgets.QFileDialog.getExistingDirectory(self, "Export stages to", start)
        if not output:
            return
        self._store(SETTING_OUTPUT_FOLDER, output)
        self._busy = True
        self._cancel.clear()
        self._last_output = Path(output)
        self.progress_bar.setRange(0, 0)
        self.progress_bar.show()
        self.cancel_button.show()
        self.open_button.hide()
        self._update_buttons()
        index, include_hidden = self.index, self.hidden_checkbox.isChecked()
        kind = self.format_combo.currentData()

        def run():
            written, errors = [], []
            for number, scene in enumerate(scenes, 1):
                if self._cancel.is_set():
                    break
                prefix = f"[{number}/{len(scenes)}] " if len(scenes) > 1 else ""

                def progress(text, value, total, prefix=prefix):
                    self.signals.progress.emit(prefix + text, value, total)

                try:
                    if kind == "gltf":
                        exporter = StageExporter(
                            index, include_hidden=include_hidden, cancelled=self._cancel.is_set, progress=progress
                        )
                        written.append(exporter.export(scene, output))
                    else:
                        written.append(
                            export_stage_file(index, scene, output, kind, include_hidden, progress, self._cancel.is_set)
                        )
                except InterruptedError:
                    break
                except Exception as exc:
                    errors.append(f"{scene}: {exc}")
            self.signals.finished.emit(written, "\n".join(errors))

        scan_memory.begin()
        threading.Thread(target=run, daemon=True).start()

    def view_selected(self):
        scenes = self._selected_scenes()
        if self.index is None or len(scenes) != 1:
            return
        from gui.widgets import scene_viewer

        scene_viewer.viewer_for(self.window_).open_stage(self.game, scenes[0], self._read_options())

    def _stage_menu(self, position):
        """Right-click on a scene: add it to the Scene Viewer (as in the file list), or export the selection."""
        item = self.tree.itemAt(position)
        if item is not None and not item.isSelected():
            self.tree.setCurrentItem(item)
        scenes = self._selected_scenes()
        if not scenes:
            return
        menu = QtWidgets.QMenu(self.tree)
        add = menu.addAction("Add to Scene Viewer")
        add.setEnabled(self.index is not None and len(scenes) == 1)
        add.triggered.connect(lambda _checked=False: self.view_selected())
        export = menu.addAction("Export Selected…")
        export.setEnabled(not self._busy and self.index is not None)
        export.triggered.connect(lambda _checked=False: self.export_selected())
        menu.exec(self.tree.viewport().mapToGlobal(position))

    def _progress(self, text, value, total):
        self.status.setText(text)
        self.progress_bar.setRange(0, max(total, 1))
        self.progress_bar.setValue(value)

    def _finished(self, written, errors):
        scan_memory.end()
        self._busy = False
        self.progress_bar.hide()
        self.cancel_button.hide()
        cancelled = self._cancel.is_set()
        message = f"Exported {len(written)} scene(s) to {self._last_output}." if written else "Nothing exported."
        if cancelled:
            message = "Cancelled. " + message
        if errors:
            message += "\nFailed:\n" + errors
        self.status.setText(message)
        self.open_button.setVisible(bool(written))
        self._update_buttons()

    def _open_output(self):
        if self._last_output:
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(self._last_output)))


def install(app) -> None:
    """Add "Stages" to the category dropdown of every main window."""
    for window in app.topLevelWidgets():
        combo = getattr(window, "entry_category_filter_combobox", None)
        if combo is None or combo.findData(STAGES_DATA) >= 0:
            continue
        stack = addons.left_stack(window)
        addons.PAGES.add(STAGES_DATA)
        page = StagesPage(window)
        stack.addWidget(page)
        window.stages_page = page
        combo.addItem("Stages", STAGES_DATA)

        def show_if_selected(*_args, window=window, combo=combo, stack=stack, page=page):
            if combo.currentData() != STAGES_DATA:
                return
            stack.setCurrentWidget(page)
            for name in ("extract_button_widget", "mesh_biped_head_filter_checkbox"):
                widget = getattr(window, name, None)
                if widget is not None:
                    widget.hide()
            page.activate()

        # Connected after the window's own handlers, so this runs last
        combo.currentIndexChanged.connect(show_if_selected)
        if hasattr(window, "loading_complete_signal"):
            window.loading_complete_signal.connect(show_if_selected)
        if hasattr(window, "name_filter_input"):
            window.name_filter_input.textChanged.connect(page.apply_filter)
        watch_page_filters(window)
