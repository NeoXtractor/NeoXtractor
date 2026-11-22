"""
The "Export character animations (VMD / GLB)…" dialog, opened from the right-click menu in Animations. Pick the
male or female character, search and select clips, then save each one as a VMD motion (for the character PMX
files this app exports, with MMD bone names) or as a GLB, either on the bare armature or on the default
character model.
"""

from __future__ import annotations

import os
import threading

from PySide6 import QtCore, QtWidgets
from core.logger import get_logger

FORMATS = [
    ("vmd", "VMD motion (for the PMX models exported from this app)"),
    ("glb", "GLB, armature only"),
    ("glb_character", "GLB on the default character model"),
]
SETTING_FORMAT = "character_anim.format"
_INDEXES: dict[str, object] = {}
_LOCK = threading.Lock()


def _game_folder():
    from core.npk.stage_export import game_root_for

    app = QtWidgets.QApplication.instance()
    archive = app.property("npk_file") if app is not None else None
    game = game_root_for(archive.file_path) if archive is not None and getattr(archive, "file_path", None) else None
    settings = app.property("settings_manager") if app is not None else None
    if game is None and settings is not None:
        try:
            saved = settings.get("stage_export.game_folder", "")
            game = game_root_for(os.path.join(saved, "res", "x")) if saved else None
        except Exception:
            game = None
    return game


def _game_options():
    """The open archive's read options (call on the GUI thread; see stage_export.read_options_for)."""
    from core.npk.stage_export import read_options_for

    app = QtWidgets.QApplication.instance()
    return read_options_for(app.property("npk_file") if app is not None else None)


def _index(game, options=None):
    """The pack index of a game folder, built once and shared."""
    from core.npk.stage_export import PackIndex, read_options_for

    with _LOCK:
        key = str(game)
        if key not in _INDEXES:
            _INDEXES.clear()
            _INDEXES[key] = PackIndex(game, options or read_options_for(None))
        return _INDEXES[key]


class _Signals(QtCore.QObject):
    loaded = QtCore.Signal(object, str)


def _load_names(game, options, signals):
    from core.mesh_converter import character_anim

    try:
        paths = _index(game, options).manifest()
        result = ({sex: character_anim.animation_names(paths, sex) for sex in character_anim.RIGS}, "")
    except Exception as exc:
        result = ({}, str(exc))
    try:
        signals.loaded.emit(*result)
    except RuntimeError:  # the dialog was closed meanwhile
        pass


class AnimationExportDialog(QtWidgets.QDialog):
    def __init__(self, main_window, game):
        super().__init__(main_window)
        self.main_window = main_window
        self.game = game
        self.options = _game_options()
        self.names: dict[str, list[str]] = {}
        self.setWindowTitle("Export character animations")
        self.resize(520, 620)
        layout = QtWidgets.QVBoxLayout(self)
        top = QtWidgets.QHBoxLayout()
        top.addWidget(QtWidgets.QLabel("Character:"))
        self.sex = QtWidgets.QComboBox()
        self.sex.addItem("Male", "male")
        self.sex.addItem("Female", "female")
        top.addWidget(self.sex)
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("Search clips (e.g. dance, run, idle, attack)…")
        self.search.setClearButtonEnabled(True)
        top.addWidget(self.search, 1)
        layout.addLayout(top)
        self.list = QtWidgets.QListWidget()
        self.list.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        layout.addWidget(self.list, 1)
        row = QtWidgets.QHBoxLayout()
        self.count = QtWidgets.QLabel("Loading the game's file list…")
        row.addWidget(self.count, 1)
        select_all = QtWidgets.QPushButton("Select all shown")
        select_all.clicked.connect(self._select_shown)
        row.addWidget(select_all)
        layout.addLayout(row)
        box = QtWidgets.QGroupBox("Format")
        box_layout = QtWidgets.QVBoxLayout(box)
        self.format_buttons = {}
        saved = self._settings_get(SETTING_FORMAT, "vmd")
        for key, label in FORMATS:
            button = QtWidgets.QRadioButton(label)
            button.setChecked(key == saved)
            box_layout.addWidget(button)
            self.format_buttons[key] = button
        if not any(b.isChecked() for b in self.format_buttons.values()):
            self.format_buttons["vmd"].setChecked(True)
        layout.addWidget(box)
        note = QtWidgets.QLabel(
            "VMD keeps rotations and movement but not bone scaling. Clips that can't be read "
            "are skipped and listed at the end."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: gray")
        layout.addWidget(note)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        self.export_button = buttons.addButton("Export…", QtWidgets.QDialogButtonBox.ButtonRole.AcceptRole)
        self.export_button.setEnabled(False)
        buttons.accepted.connect(self._export)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.sex.currentIndexChanged.connect(self._fill)
        self.search.textChanged.connect(self._filter)
        self.list.itemSelectionChanged.connect(self._update_count)
        self.list.itemDoubleClicked.connect(lambda _item: self._export())

        self._signals = _Signals(self)
        self._signals.loaded.connect(self._loaded)
        signals = self._signals
        threading.Thread(target=_load_names, args=(game, self.options, signals), daemon=True).start()

    @staticmethod
    def _settings_get(key, default):
        app = QtWidgets.QApplication.instance()
        settings = app.property("settings_manager") if app is not None else None
        try:
            return settings.get(key, default) if settings is not None else default
        except Exception:
            return default

    def _loaded(self, names, error):
        if error or not any(names.values()):
            self.count.setText(f"Could not list the animations: {error or 'none found'}")
            return
        self.names = names
        self._fill()

    def _fill(self):
        self.list.clear()
        for name in self.names.get(self.sex.currentData(), []):
            self.list.addItem(name)
        self._filter(self.search.text())

    def _filter(self, text):
        words = text.lower().split()
        for row in range(self.list.count()):
            item = self.list.item(row)
            item.setHidden(not all(word in item.text().lower() for word in words))
        self._update_count()

    def _select_shown(self):
        self.list.clearSelection()
        for row in range(self.list.count()):
            item = self.list.item(row)
            if not item.isHidden():
                item.setSelected(True)

    def _selected(self):
        return [item.text() for item in self.list.selectedItems() if not item.isHidden()]

    def _update_count(self):
        shown = sum(not self.list.item(r).isHidden() for r in range(self.list.count()))
        chosen = len(self._selected())
        self.count.setText(f"{shown} of {self.list.count()} clips shown, {chosen} selected")
        self.export_button.setEnabled(chosen > 0)

    def _export(self):
        names = self._selected()
        if not names:
            return
        fmt = next(key for key, button in self.format_buttons.items() if button.isChecked())
        app = QtWidgets.QApplication.instance()
        settings = app.property("settings_manager") if app is not None else None
        if settings is not None:
            try:
                settings.set(SETTING_FORMAT, fmt, True)
            except Exception:
                get_logger().warning("Could not save the export format setting", exc_info=True)
        sex = self.sex.currentData()
        self.accept()
        export_animations(self.main_window, self.game, sex, names, fmt, self.options)


def export_animations(main_window, game, sex: str, names: list[str], fmt: str, options=None) -> None:
    from gui.widgets.character_exports import start_export

    def build(progress, cancelled, report, target):
        from core.mesh_converter import character_anim as ca
        from core.npk.stage_export import StageLoader

        progress("Reading the skeleton…", 0, len(names))
        index = _index(game, options)

        def read(path):
            found = index.read(path)
            return found[0] if found else None

        skeleton = ca.load_skeleton(read, sex)
        models = {}
        loader = StageLoader(index) if fmt != "vmd" else None

        def read_model(path):
            if path not in models:
                models[path] = loader.model(path)
            return models[path]

        folder = os.path.join(target, f"{sex}_animations")
        os.makedirs(folder, exist_ok=True)
        done, unsupported, scaled = 0, [], 0
        for number, name in enumerate(names):
            if cancelled():
                raise InterruptedError
            progress(f"{name}  ({number + 1}/{len(names)})", number, len(names))
            try:
                clip = ca.load_clip(read, sex, name, skeleton)
            except ValueError as exc:
                if "Unsupported" in str(exc):
                    unsupported.append(name)
                else:
                    report.append(f"{name}: {exc}")
                continue
            except FileNotFoundError:
                report.append(f"{name}: not found in the game files")
                continue
            out = os.path.join(folder, ca.safe_name(name))
            try:
                if fmt == "vmd":
                    _path, scales = ca.write_vmd(clip, skeleton, out + ".vmd")
                    scaled += scales
                else:
                    ca.write_glb(
                        read_model,
                        clip,
                        sex,
                        out + ("_character.glb" if fmt == "glb_character" else ".glb"),
                        fmt == "glb_character",
                    )
            except Exception as exc:
                report.append(f"{name}: {exc}")
                continue
            done += 1
        if unsupported:
            report.append(
                f"{len(unsupported)} clip(s) skipped (stored in a format that can't be read yet): "
                + ", ".join(unsupported[:12])
                + (" …" if len(unsupported) > 12 else "")
            )
        if scaled:
            report.append(
                f"{scaled} clip(s) also scale bones (squash/stretch, hiding props); VMD cannot store "
                "scaling, so that part is left out. The GLB export keeps it."
            )
        if not done:
            raise ValueError("No animation could be exported." + ("\n" + "\n".join(report[:5]) if report else ""))
        kind = "VMD" if fmt == "vmd" else "GLB"
        return f"{done} {kind} file(s) in {folder}"

    start_export(main_window, "each:vmd" if fmt == "vmd" else "each:glb", "animations", build)


def open_dialog(main_window) -> None:
    game = _game_folder()
    if game is None:
        QtWidgets.QMessageBox.information(
            main_window,
            "Export character animations",
            "Open any Harry Potter: Magic Awakened .npk first, so the game folder is known.",
        )
        return
    dialog = AnimationExportDialog(main_window, game)
    dialog.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose)
    dialog.show()
