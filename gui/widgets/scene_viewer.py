"""
Scene Viewer: assemble several meshes (with their rigs) in one view, try textures on
them, and load whole HPMA stages with every texture already matched.
"""

from __future__ import annotations

import ntpath
import os
import threading
from PySide6 import QtCore, QtGui, QtWidgets
from core.mesh_loader.loader import MeshLoader
from core.logger import get_logger
from gui.utils import scan_memory
from gui.widgets.character_exports import (  # noqa: F401
    BODY_SIZE_SETTING,
    EACH_FORMATS,
    ITEM_INFO_LABEL,
    ITEM_INFO_SETTING,
    _EXPORT_JOBS,
    _selected_face_folder,
    body_size_adult,
    export_character_meshes_each,
    export_character_selection,
    export_item_icons,
    export_selected_item_icons,
    include_item_info,
    is_character_path,
    outfit_body_items,
    read_entry_data,
    selected_mesh_specs,
    start_export,
    textured_model,
    with_body_size,
)
from gui.widgets.scene_render import (  # noqa: F401
    MEMORY_TEXTURES,
    SceneObject,
    SceneRenderWidget,
    decode_image_bytes,
    pil_to_qimage,
    remember_texture,
)

TEXTURE_EXTENSIONS = {"dds", "tga", "png", "jpg", "jpeg", "ktx", "pvr", "astc", "bmp"}


class _Signals(QtCore.QObject):
    loaded = QtCore.Signal(object, str)  # (group, [object specs]) , error
    textures = QtCore.Signal(object, str)  # [(object id, part index, key, qimage, label)], error
    progress = QtCore.Signal(str, int, int)


# The "Export Animations (PMX + VMD)" button is hidden for now; its export code (export_scene("vmd"),
# core.mesh_converter.vmd_export) is kept and working. Set True to bring the button back.
SHOW_VMD_EXPORT = False


OBJECT_ROLE = QtCore.Qt.ItemDataRole.UserRole


PART_ROLE = QtCore.Qt.ItemDataRole.UserRole + 1


GROUP_ROLE = QtCore.Qt.ItemDataRole.UserRole + 2


class TexturePicker(QtWidgets.QDialog):
    """Pick a texture from the open archive (or a file) and apply it to the scene selection."""

    def __init__(self, viewer: "SceneViewerWindow"):
        super().__init__(viewer)
        self.viewer = viewer
        self.setWindowTitle("Assign Texture")
        self.resize(560, 620)
        layout = QtWidgets.QVBoxLayout(self)
        self.hint = QtWidgets.QLabel("Select parts or objects in the scene, then pick a texture and click Apply.")
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("Filter textures…")
        self.search.textChanged.connect(self._filter)
        layout.addWidget(self.search)
        self.list = QtWidgets.QListWidget()
        self.list.itemDoubleClicked.connect(lambda _item: self.apply())
        layout.addWidget(self.list, 1)
        buttons = QtWidgets.QHBoxLayout()
        from_file = QtWidgets.QPushButton("From File…")
        from_file.clicked.connect(self.from_file)
        apply_button = QtWidgets.QPushButton("Apply")
        apply_button.setDefault(True)
        apply_button.clicked.connect(self.apply)
        close = QtWidgets.QPushButton("Close")
        close.clicked.connect(self.close)
        buttons.addWidget(from_file)
        buttons.addStretch(1)
        buttons.addWidget(apply_button)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        self.populate()

    def populate(self):
        self.list.clear()
        archive = self.viewer.archive()
        if archive is None:
            self.list.addItem("Open an archive to pick textures from it, or use From File…")
            return
        entries = getattr(archive, "entries", {}) or {}
        reference = (self.viewer.selection_paths() or [""])[0].rsplit("\\", 1)[0]
        rows = []
        for index, entry in entries.items():
            extension = (getattr(entry, "extension", "") or "").lower()
            name = getattr(entry, "filename", "") or ""
            if extension not in TEXTURE_EXTENSIONS:
                continue
            label = (
                name
                if name and not name.startswith("0x")
                else f"{hex(getattr(entry, 'file_signature', 0))}.{extension}"
            )
            if hasattr(archive, "source_label"):
                try:
                    label += f"   ·  {archive.source_label(index)}"
                except Exception:
                    get_logger().debug("No source label for entry %s", index, exc_info=True)
            shared = len(os.path.commonprefix([reference.lower(), name.lower()])) if reference else 0
            rows.append((-shared, label.lower(), label, index))
        rows.sort()
        for _shared, _key, label, index in rows:
            item = QtWidgets.QListWidgetItem(label)
            item.setData(QtCore.Qt.ItemDataRole.UserRole, index)
            self.list.addItem(item)
        if not rows:
            self.list.addItem("No textures found in the open archive; use From File…")
        self._filter(self.search.text())

    def _filter(self, text):
        text = text.strip().lower()
        for row in range(self.list.count()):
            item = self.list.item(row)
            item.setHidden(bool(text) and text not in item.text().lower())

    def apply(self):
        item = self.list.currentItem()
        if item is None or item.data(QtCore.Qt.ItemDataRole.UserRole) is None:
            return
        self.viewer.assign_archive_texture(
            int(item.data(QtCore.Qt.ItemDataRole.UserRole)), item.text().split("   ·")[0]
        )

    def from_file(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Texture file", "", "Images (*.png *.jpg *.jpeg *.tga *.dds *.bmp *.ktx *.pvr);;All files (*)"
        )
        if path:
            self.viewer.assign_file_texture(path)


class SceneViewerWindow(QtWidgets.QMainWindow):
    """Window with the scene tree on the left and the 3D view on the right."""

    def __init__(self, main_window=None):
        super().__init__(None)
        self.main_window = main_window
        self.setWindowTitle("Scene Viewer")
        self.resize(1400, 860)
        self.signals = _Signals()
        self.signals.loaded.connect(self._loaded)
        self.signals.textures.connect(self._textures_loaded)
        self.signals.progress.connect(self._progress)
        self._objects: dict[int, SceneObject] = {}
        self._items: dict[int, QtWidgets.QTreeWidgetItem] = {}
        self._cancel = threading.Event()
        self._jobs = 0
        self._picker: TexturePicker | None = None

        splitter = QtWidgets.QSplitter()
        panel = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(6, 6, 6, 6)
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderLabels(["Scene", "Texture"])
        self.tree.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.header().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.tree.itemChanged.connect(self._item_changed)
        self.tree.itemSelectionChanged.connect(self._selection_changed)
        self.tree.itemDoubleClicked.connect(lambda _item, _col: self.view.focus(self.selected_objects() or None))

        search_row = QtWidgets.QHBoxLayout()
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("Search objects or textures…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.apply_search)
        search_row.addWidget(self.search, 1)
        layout.addLayout(search_row)
        layout.addWidget(self.tree, 1)

        # Adding is "Add to Scene Viewer" in the main window's right-click menus; textures and removing are in the
        # scene list's right-click menu (Delete removes, Ctrl+A selects everything). Exporting is one menu, as in the
        # Main Character Creator, with the PMX switches in their own submenu.
        export = QtWidgets.QPushButton("Export")  # a button with a menu draws its own arrow
        export.setToolTip("Save the selection (or everything visible) as one file with its rigs and textures.")
        export_menu = QtWidgets.QMenu(export)
        export_menu.setToolTipsVisible(True)
        export_menu.addAction("GLB…").triggered.connect(lambda _checked=False: self.export_scene("glb"))
        export_menu.addAction("PMX…").triggered.connect(lambda _checked=False: self.export_scene("pmx"))
        if SHOW_VMD_EXPORT:
            vmd = export_menu.addAction("Animations (PMX + VMD)…")
            vmd.setToolTip("Each animated object as its own .pmx with textures plus a .vmd of its loop, for MMD.")
            vmd.triggered.connect(lambda _checked=False: self.export_scene("vmd"))
        try:
            from core.mesh_converter.formats.pmx import pmx_option_actions

            export_menu.addSeparator()
            pmx_menu = export_menu.addMenu("PMX options")
            self.pmx_actions = pmx_option_actions(pmx_menu)
            for action in self.pmx_actions:
                pmx_menu.addAction(action)
            pmx_menu.setToolTipsVisible(True)
        except Exception:
            self.pmx_actions = ()
        export.setMenu(export_menu)
        layout.addWidget(export)
        self.bones_checkbox = QtWidgets.QCheckBox("Show bones")
        self.bones_checkbox.setChecked(True)
        self.bones_checkbox.toggled.connect(self._toggle_bones)
        self.play_checkbox = QtWidgets.QCheckBox("Play animations")
        self.play_checkbox.setChecked(True)
        self.play_checkbox.setToolTip("Play each animated model's default loop (as the game auto-plays it).")
        self.play_checkbox.toggled.connect(lambda checked: self.view.set_playing(checked))
        toggles = QtWidgets.QHBoxLayout()
        toggles.addWidget(self.bones_checkbox)
        toggles.addWidget(self.play_checkbox)
        toggles.addStretch(1)
        layout.addLayout(toggles)
        self.status = QtWidgets.QLabel()  # progress and short messages; empty otherwise
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.progress = QtWidgets.QProgressBar()
        self.progress.hide()
        layout.addWidget(self.progress)
        self.cancel_button = QtWidgets.QPushButton("Cancel")
        self.cancel_button.clicked.connect(self._cancel.set)
        self.cancel_button.hide()
        layout.addWidget(self.cancel_button)
        self.view = SceneRenderWidget()
        self.view.upload_progress.connect(self._upload_progress)
        # As in NeoXtractor's mesh viewer: the scene's numbers top left, the camera keys bottom left
        from gui.widgets.viewer_overlay import ViewOverlays

        self.overlays = ViewOverlays(self.view)
        splitter.addWidget(panel)
        splitter.addWidget(self.view)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([380, 1020])
        self.setCentralWidget(splitter)
        QtGui.QShortcut(QtGui.QKeySequence.StandardKey.Delete, self.tree, self.remove_selected)
        self.tree.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._scene_menu)

    def _scene_menu(self, position):
        """Right-click on the scene list: textures of the selection, and removing it."""
        item = self.tree.itemAt(position)
        if item is not None and not item.isSelected():
            self.tree.setCurrentItem(item)
        if not self.tree.selectedItems():
            return
        menu = QtWidgets.QMenu(self.tree)
        menu.setToolTipsVisible(True)
        for label, tip, handler in (
            ("Assign Texture…", "Pick a texture for the selected parts or objects.", self.open_texture_picker),
            (
                "Auto-match Textures",
                "Find each selected model's own materials and textures in the game.",
                self.auto_match,
            ),
            ("Clear Texture", "Remove assigned textures from the selection.", self.clear_textures),
        ):
            action = menu.addAction(label)
            action.setToolTip(tip)
            action.triggered.connect(lambda _checked=False, handler=handler: handler())
        menu.addSeparator()
        remove = menu.addAction("Remove")
        remove.setShortcut(QtGui.QKeySequence.StandardKey.Delete)
        remove.triggered.connect(lambda _checked=False: self.remove_selected())
        menu.exec(self.tree.viewport().mapToGlobal(position))

    def archive(self):
        app = QtWidgets.QApplication.instance()
        return app.property("npk_file") if app is not None else None

    def _game_index(self):
        """PackIndex for the open archive's game folder (cached), or None."""
        from core.npk.stage_export import PackIndex, game_root_for, read_options_for

        archive = self.archive()
        game = game_root_for(archive.file_path) if archive is not None and getattr(archive, "file_path", None) else None
        if game is None:
            return None
        cached = getattr(self, "_index_cache", None)
        if cached is None or cached[0] != game:
            self._index_cache = (game, PackIndex(game, read_options_for(archive)))
        return self._index_cache[1]

    def _run(self, label, work):
        self._jobs += 1
        scan_memory.begin()  # what the job loads must not count against the character-scan limit
        self._cancel.clear()
        self.progress.setRange(0, 0)
        self.progress.show()
        self.cancel_button.show()
        self.status.setText(label)
        threading.Thread(target=work, daemon=True).start()

    def _job_done(self):
        self._jobs = max(0, self._jobs - 1)
        scan_memory.end()
        if not self._jobs:
            self.progress.hide()
            self.cancel_button.hide()

    def _progress(self, text, value, total):
        self.status.setText(text)
        self.progress.setRange(0, max(total, 1))
        self.progress.setValue(value)

    def _upload_progress(self, done, total):
        if total and done < total:
            self._uploading = True
            self.status.setText(f"Uploading to GPU: {done} / {total} objects…")
        elif getattr(self, "_uploading", False):
            self._uploading = False
            self._summary()

    def _summary(self):
        """The scene's numbers in the view's corner (the mesh viewer's rows, for every object together)."""
        objects = list(self._objects.values())
        parts = sum(len(o.parts) for o in objects)
        rows = [
            ("Objects", len(objects)),
            ("Bones", sum(o.bone_count for o in objects)),
            ("Vertices", f"{sum(o.vertex_count for o in objects):,}"),
            ("Triangles", f"{sum(o.triangle_count for o in objects):,}"),
            ("Textured", f"{sum(1 for o in objects for p in o.parts if p.texture_key)} of {parts} parts"),
        ]
        animated = sum(1 for o in objects if o.clip is not None)
        if animated:
            rows.append(("Animated", animated))
        self.overlays.set_info(rows if objects else [])
        self.status.clear()

    def add_mesh_data(self, name, mesh, path=None, source="", matrix=None, group=None, parts=None, animation=None):
        """Add a mesh; parts may carry pre-matched textures and animation a default loop from StageLoader."""
        obj = SceneObject(name, mesh, matrix, path, source)
        obj.group = group
        obj.set_animation(animation)
        for number, part in enumerate(obj.parts):
            info = parts[number] if parts and number < len(parts) else None
            if info is None:
                continue
            part.material = info.get("material") or part.material
            if info.get("qimage") is not None:
                part.texture_key = self.view.set_texture(
                    "game:" + (info.get("texture") or f"{id(info)}"),
                    info["qimage"],
                    (info.get("texture") or "").rsplit("\\", 1)[-1],
                )
                part.alpha = info.get("alpha", "OPAQUE")
                part.cutoff = info.get("cutoff", 0.5)
        self._objects[obj.id] = obj
        self.view.add_object(obj)
        self._add_tree_item(obj)
        return obj

    def add_baked_items(self, items, group=None):
        """Add export items whose parts carry PIL images made in the app (kept as memory: textures)."""
        added = []
        paths = [item.get("path") for item in items if item.get("path")]
        if body_size_adult() and any(is_character_path(p) for p in paths):
            # Same adult body as the rest of the character: the outfit already in the scene decides the plan
            scene_paths = [obj.path for obj in self._objects.values() if obj.path and "\\clothes\\" in obj.path.lower()]
            notes: list[str] = []
            items = with_body_size(self._game_index(), items, scene_paths + paths, notes)
            for note in notes:
                get_logger().info("Scene Viewer: %s", note)
        for item in items:
            obj = SceneObject(item["name"], item["mesh"], item.get("matrix"), item.get("path"), item.get("source", ""))
            obj.group = group
            for number, part in enumerate(obj.parts):
                info = item["parts"][number] if number < len(item["parts"]) else None
                if info is None:
                    continue
                part.material = info.get("material") or part.material
                image = info.get("image")
                if image is not None:
                    key = remember_texture(image)
                    label = os.path.basename(str(info.get("texture") or "")) or f"{item['name']}.png"
                    part.texture_key = self.view.set_texture(key, pil_to_qimage(image), label)
                    part.alpha = info.get("alpha", "OPAQUE")
                    part.cutoff = info.get("cutoff", 0.5)
            self._objects[obj.id] = obj
            self.view.add_object(obj)
            self._add_tree_item(obj)
            added.append(obj)
        if added:
            self.view.focus(added)
            self.view.update()
        return added

    def _group_item(self, group):
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            if item.data(0, GROUP_ROLE) == group:
                return item
        item = QtWidgets.QTreeWidgetItem([group, ""])
        item.setData(0, GROUP_ROLE, group)
        item.setFlags(item.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable | QtCore.Qt.ItemFlag.ItemIsAutoTristate)
        item.setCheckState(0, QtCore.Qt.CheckState.Checked)
        self.tree.addTopLevelItem(item)
        return item

    def _add_tree_item(self, obj):
        self.tree.blockSignals(True)
        item = QtWidgets.QTreeWidgetItem([obj.name, ""])
        item.setData(0, OBJECT_ROLE, obj.id)
        item.setFlags(item.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(0, QtCore.Qt.CheckState.Checked)
        tip = [obj.path or obj.name, f"{obj.vertex_count:,} vertices · {obj.triangle_count:,} triangles"]
        if obj.bone_count:
            tip.append(f"{obj.bone_count} bones")
        if obj.source:
            tip.append(obj.source)
        item.setToolTip(0, "\n".join(tip))
        for number, part in enumerate(obj.parts):
            child = QtWidgets.QTreeWidgetItem([f"Part {number}: {part.material}", ""])
            child.setData(0, OBJECT_ROLE, obj.id)
            child.setData(0, PART_ROLE, number)
            item.addChild(child)
        if obj.group:
            self._group_item(obj.group).addChild(item)
        else:
            self.tree.addTopLevelItem(item)
        self._items[obj.id] = item
        self._refresh_labels(obj)
        self.tree.blockSignals(False)

    def _refresh_labels(self, obj):
        item = self._items.get(obj.id)
        if item is None:
            return
        labels = []
        for number, part in enumerate(obj.parts):
            label = self.view.textures.get(part.texture_key, {}).get("label", "") if part.texture_key else ""
            item.child(number).setText(1, label or "—")
            labels.append(label)
        named = [label for label in labels if label]
        item.setText(
            1,
            (
                named[0]
                if len(set(named)) == 1 and len(named) == len(labels)
                else (f"{len(named)}/{len(labels)} parts" if named else "—")
            ),
        )

    def add_entries(self, specs):
        """specs: [(name, entry index, game path or None)] from the open archive; loaded in the background."""
        archive = self.archive()
        if archive is None or not specs:
            return
        # Outfits come with the body piece they are worn with (only_body), as in the exports
        game_index = self._game_index()
        selected_paths = [path for _name, _index, path in specs if path]

        def work():
            from core.npk.stage_export import StageLoader

            loader, results, errors, added_bodies = MeshLoader(), [], [], set()
            for number, (name, index, path) in enumerate(specs):
                if self._cancel.is_set():
                    break
                self.signals.progress.emit(f"Loading {name}…", number, len(specs))
                try:
                    mesh = loader.load_from_bytes(read_entry_data(archive, index))
                    if mesh is None or not mesh.position:
                        raise ValueError("not a readable mesh")
                    source = archive.source_label(index) if hasattr(archive, "source_label") else ""
                    results.append({"name": name, "mesh": mesh, "path": path, "source": source})
                except Exception as exc:
                    errors.append(f"{name}: {exc}")
                    continue
                if game_index is None or not path:
                    continue
                try:
                    stage_loader = StageLoader(game_index)
                    for body in outfit_body_items(stage_loader, path, selected_paths):
                        if body["name"] in added_bodies:  # hands / neck shared by several outfits
                            continue
                        added_bodies.add(body["name"])
                        for part in body["parts"]:  # textures go to the GUI thread as QImages
                            if part.get("image") is not None:
                                part["qimage"] = pil_to_qimage(part["image"])
                            part["image"] = None
                        results.append(
                            {
                                "name": f"{body['name']}.mesh",
                                "mesh": body["mesh"],
                                "parts": body["parts"],
                                "path": None,
                                "source": f"body piece of {name}",
                            }
                        )
                    errors.extend(f"{name}: {line}" for line in stage_loader.report)
                except Exception as exc:
                    errors.append(f"{name}: body piece not loaded ({exc})")
            if body_size_adult() and any(is_character_path(p) for p in selected_paths):
                adult_index = game_index or self._game_index()
                notes: list[str] = []
                adult = with_body_size(adult_index, [{"mesh": r["mesh"]} for r in results], selected_paths, notes)
                for result, item in zip(results, adult):
                    result["mesh"] = item["mesh"]
                errors.extend(notes)
            self.signals.loaded.emit((None, results), "\n".join(errors))

        self._run(f"Loading {len(specs)} mesh(es)…", work)

    def open_stage(self, game, scene_path, options=None):
        """Load a stage with StageLoader (textures matched) into a new group."""
        from core.npk.stage_export import PackIndex, StageLoader, read_options_for

        group = scene_path.rsplit("\\", 1)[-1].rsplit(".", 1)[0]
        options = options or read_options_for(self.archive())

        self._stage_game = (game, options)

        def work():
            index = PackIndex(game, options)
            loader = StageLoader(
                index,
                cancelled=self._cancel.is_set,
                progress=lambda text, value, total: self.signals.progress.emit(text, value, total),
            )
            try:
                objects = loader.load(scene_path)
                qimages = {}
                for obj in objects:
                    for part in obj["parts"]:
                        image = part.get("image")
                        if image is not None:
                            if id(image) not in qimages:
                                qimages[id(image)] = pil_to_qimage(image)
                            part["qimage"] = qimages[id(image)]
                        part["image"] = None
                self.signals.loaded.emit((group, objects), "\n".join(sorted(set(loader.report)))[:2000])
            except InterruptedError:
                self.signals.loaded.emit((group, []), "Cancelled")
            except Exception as exc:
                self.signals.loaded.emit((group, []), str(exc))

        self.show()
        self.raise_()
        self._run(f"Loading stage {group}…", work)

    def _loaded(self, payload, error):
        group, objects = payload
        first = not self._objects
        added = []
        for spec in objects:
            added.append(
                self.add_mesh_data(
                    spec["name"],
                    spec["mesh"],
                    spec.get("path"),
                    spec.get("source", ""),
                    spec.get("matrix"),
                    group,
                    spec.get("parts"),
                    spec.get("animation"),
                )
            )
        self._job_done()
        if group:
            item = self._group_item(group) if added else None
            if item is not None:
                item.setExpanded(False)
        if added and (first or group):
            self.view.focus(added)
        self._summary()
        if self.search.text().strip():
            self.apply_search()
        if error == "Cancelled":
            self.status.setText("Cancelled.")
        elif error:
            for line in error.splitlines():
                get_logger().warning("Scene Viewer: %s", line)
            self.status.setText("Some of it couldn't be added: see the log.")

    def add_from_main_window(self):
        specs = selected_mesh_specs(self.main_window)
        if not specs:
            self.status.setText("Select one or more meshes in the main window (file list or Characters) first.")
            return
        self.add_entries(specs)

    def _selected_targets(self):
        """[(SceneObject, [part indices])] for the tree selection; groups expand to their objects."""
        chosen: dict[int, set] = {}
        for item in self.tree.selectedItems():
            group = item.data(0, GROUP_ROLE)
            if group is not None:
                for i in range(item.childCount()):
                    obj_id = item.child(i).data(0, OBJECT_ROLE)
                    chosen[obj_id] = set(range(len(self._objects[obj_id].parts)))
                continue
            obj_id = item.data(0, OBJECT_ROLE)
            if obj_id not in self._objects:
                continue
            part = item.data(0, PART_ROLE)
            parts = chosen.setdefault(obj_id, set())
            if part is None:
                parts.update(range(len(self._objects[obj_id].parts)))
            else:
                parts.add(int(part))
        return [(self._objects[obj_id], sorted(parts)) for obj_id, parts in chosen.items()]

    def selected_objects(self):
        return [obj for obj, _parts in self._selected_targets()]

    def selection_paths(self):
        return [obj.path for obj in self.selected_objects() if obj.path]

    def _selection_changed(self):
        targets = {(obj.id, part) for obj, parts in self._selected_targets() for part in parts}
        for obj in self._objects.values():
            for number, part in enumerate(obj.parts):
                highlight = (obj.id, number) in targets
                if part.highlight != highlight:
                    part.highlight = highlight
                    part.dirty = True
        self.view.update()

    def _item_changed(self, item, column):
        if column != 0:
            return
        obj_id = item.data(0, OBJECT_ROLE)
        if obj_id in self._objects and item.data(0, PART_ROLE) is None:
            obj = self._objects[obj_id]
            visible = item.checkState(0) != QtCore.Qt.CheckState.Unchecked
            if obj.visible != visible:
                # Visibility only affects what is drawn: no GPU data needs rebuilding
                obj.visible = visible
                self.view._bones_dirty = True
                self.view.update()

    def _object_items(self):
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            if top.data(0, GROUP_ROLE) is not None:
                for j in range(top.childCount()):
                    yield top.child(j)
            else:
                yield top

    def apply_search(self, text=None):
        text = (self.search.text() if text is None else text).strip().lower()
        self.tree.setUpdatesEnabled(False)
        shown = 0
        for item in self._object_items():
            match = (
                not text
                or text in item.text(0).lower()
                or text in item.text(1).lower()
                or any(text in item.child(k).text(1).lower() for k in range(item.childCount()))
            )
            item.setHidden(not match)
            shown += match
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            if top.data(0, GROUP_ROLE) is not None:
                top.setHidden(all(top.child(j).isHidden() for j in range(top.childCount())))
                if text:
                    top.setExpanded(True)
        self.tree.setUpdatesEnabled(True)
        if text:
            self.status.setText(f"{shown} object(s) match “{text}”.")
        else:
            self._summary()

    def _toggle_bones(self, checked):
        self.view.show_bones = checked
        self.view.update()

    def _apply_texture(self, key, targets):
        for obj, parts in targets:
            for number in parts:
                part = obj.parts[number]
                part.texture_key = key
                part.alpha = "OPAQUE"
                part.dirty = True
            self._refresh_labels(obj)
        self.view.update()
        self._summary()

    def open_texture_picker(self):
        if not self._selected_targets():
            self.status.setText("Select objects or parts in the scene first.")
            return
        if self._picker is None:
            self._picker = TexturePicker(self)
        else:
            self._picker.populate()
        self._picker.show()
        self._picker.raise_()

    def assign_archive_texture(self, index, label):
        archive, targets = self.archive(), self._selected_targets()
        if archive is None or not targets:
            self.status.setText("Select objects or parts in the scene first.")
            return

        def work():
            try:
                qimage = pil_to_qimage(decode_image_bytes(read_entry_data(archive, index)))
                self.signals.textures.emit([(targets, f"entry:{id(archive)}:{index}", qimage, label)], "")
            except Exception as exc:
                self.signals.textures.emit([], f"{label}: {exc}")

        self._run(f"Decoding {label}…", work)

    def assign_file_texture(self, path):
        targets = self._selected_targets()
        if not targets:
            self.status.setText("Select objects or parts in the scene first.")
            return

        def work():
            try:
                with open(path, "rb") as stream:
                    data = stream.read()
                try:
                    image = decode_image_bytes(data)
                except Exception:
                    from PIL import Image
                    import io

                    image = Image.open(io.BytesIO(data))
                    image.load()
                self.signals.textures.emit(
                    [(targets, f"file:{path}", pil_to_qimage(image), os.path.basename(path))], ""
                )
            except Exception as exc:
                self.signals.textures.emit([], f"{os.path.basename(path)}: {exc}")

        self._run(f"Decoding {os.path.basename(path)}…", work)

    def _textures_loaded(self, results, error):
        for targets, key, qimage, label in results:
            if isinstance(targets, list):
                self.view.set_texture(key, qimage, label)
                self._apply_texture(key, targets)
            else:
                obj = self._objects.get(targets)
                if obj is None:
                    continue
                number, alpha, cutoff = label[1], label[2], label[3]
                self.view.set_texture(key, qimage, label[0])
                part = obj.parts[number]
                part.texture_key, part.alpha, part.cutoff, part.dirty = key, alpha, cutoff, True
                self._refresh_labels(obj)
        self._job_done()
        self.view.update()
        self._summary()
        if error:
            for line in error.splitlines():
                get_logger().warning("Scene Viewer: %s", line)
            self.status.setText("Some textures couldn't be matched: see the log.")

    def auto_match(self):
        targets = [(obj, parts) for obj, parts in self._selected_targets() if obj.path]
        if not targets:
            self.status.setText("Auto-match needs objects with a known game path (from Characters or stages).")
            return
        index = self._game_index()
        if index is None:
            self.status.setText("Open an archive from the game folder first, so its textures can be found.")
            return
        from core.npk.stage_export import StageLoader

        def work():
            loader, results = StageLoader(index), []
            for number, (obj, parts) in enumerate(targets):
                if self._cancel.is_set():
                    break
                self.signals.progress.emit(f"Matching textures for {obj.name}…", number, len(targets))
                model = loader.model(obj.path)
                if model is None:
                    continue
                for part_number in parts:
                    if part_number >= len(model[1]):
                        continue
                    info = model[1][part_number]
                    if info.get("image") is None:
                        continue
                    label = (info["texture"].rsplit("\\", 1)[-1], part_number, info["alpha"], info["cutoff"])
                    results.append((obj.id, "game:" + info["texture"], pil_to_qimage(info["image"]), label))
            report = "\n".join(sorted(set(loader.report)))[:1500]
            self.signals.textures.emit(results, report or ("" if results else "No textures found for the selection."))

        self._run("Matching textures…", work)

    def clear_textures(self):
        targets = self._selected_targets()
        for obj, parts in targets:
            for number in parts:
                obj.parts[number].texture_key = None
                obj.parts[number].alpha = "OPAQUE"
                obj.parts[number].dirty = True
            self._refresh_labels(obj)
        self.view._collect_textures()
        self.view.update()
        self._summary()

    def export_scene(self, kind: str):
        # Unchecked objects are never exported; a selection narrows it further
        selected = self.selected_objects()
        objects = [obj for obj in (selected or self._objects.values()) if obj.visible]
        if kind == "vmd":
            objects = [obj for obj in objects if obj.clip is not None]
            if not objects:
                self.status.setText(
                    "No animated objects to export: open a stage with animated models "
                    "(the status line shows how many), or select some."
                )
                return
        if not objects:
            self.status.setText(
                "Nothing to export: every selected object is unchecked."
                if selected
                else "Nothing to export: add meshes or a stage first, or check some objects."
            )
            return
        groups = {obj.group for obj in objects if obj.group}
        default = (
            groups.pop() if len(groups) == 1 else (objects[0].name.rsplit(".", 1)[0] if len(objects) == 1 else "scene")
        )
        archive = self.archive()
        stage_game = getattr(self, "_stage_game", None)
        textures = {key: entry.get("label", "") for key, entry in self.view.textures.items()}
        snapshot = [
            (
                obj.name,
                obj.mesh,
                obj.neox_matrix,
                [(part.material, part.texture_key, part.alpha, part.cutoff) for part in obj.parts],
                obj.clip,
            )
            for obj in objects
        ]

        def build(progress, cancelled, report):
            from core.npk.stage_export import PackIndex, StageLoader, game_root_for, read_options_for

            loader = None
            game = (
                stage_game[0]
                if stage_game
                else (
                    game_root_for(archive.file_path)
                    if archive is not None and getattr(archive, "file_path", None)
                    else None
                )
            )
            if game is not None:
                options = (stage_game[1] if stage_game else None) or read_options_for(archive)
                loader = StageLoader(PackIndex(game, options), max_texture_size=8192)
            images, items = {}, []

            def image_for(key):
                if key in images:
                    return images[key]
                image = None
                try:
                    if key.startswith("memory:"):
                        image = MEMORY_TEXTURES.get(key)
                    elif key.startswith("game:") and loader is not None:
                        image = loader.texture(key[5:])
                    elif key.startswith("file:"):
                        with open(key[5:], "rb") as stream:
                            data = stream.read()
                        try:
                            image = decode_image_bytes(data)
                        except Exception:
                            from PIL import Image
                            import io

                            image = Image.open(io.BytesIO(data))
                            image.load()
                    elif key.startswith("entry:") and archive is not None:
                        image = decode_image_bytes(read_entry_data(archive, int(key.rsplit(":", 1)[1])))
                except Exception as exc:
                    report.append(f"texture {textures.get(key, key)}: {exc}")
                images[key] = image
                return image

            for number, (name, mesh, matrix, parts, clip) in enumerate(snapshot):
                if cancelled():
                    raise InterruptedError
                progress(f"Collecting {name}…", number, len(snapshot))
                items.append(
                    {
                        "name": name,
                        "mesh": mesh,
                        "matrix": matrix,
                        "animation": clip,
                        "parts": [
                            {
                                "material": material,
                                "texture": textures.get(key) or None,
                                "image": image_for(key) if key else None,
                                "alpha": alpha,
                                "cutoff": cutoff,
                            }
                            for material, key, alpha, cutoff in parts
                        ],
                    }
                )
            if loader is not None:
                report.extend(loader.report)
            return items

        start_export(self, kind, default, build)

    def remove_selected(self):
        objects = self.selected_objects()
        if not objects:
            return
        scan_memory.begin()
        self.tree.blockSignals(True)
        for obj in objects:
            item = self._items.pop(obj.id, None)
            if item is not None:
                (item.parent() or self.tree.invisibleRootItem()).removeChild(item)
            self._objects.pop(obj.id, None)
        for i in reversed(range(self.tree.topLevelItemCount())):
            item = self.tree.topLevelItem(i)
            if item.data(0, GROUP_ROLE) is not None and item.childCount() == 0:
                self.tree.takeTopLevelItem(i)
        self.tree.blockSignals(False)
        self.view.remove_objects(objects)
        self._summary()
        scan_memory.end()


def viewer_for(main_window) -> SceneViewerWindow:
    viewer = getattr(main_window, "scene_viewer", None)
    if viewer is None:
        viewer = SceneViewerWindow(main_window)
        main_window.scene_viewer = viewer
    return viewer


def show_viewer(main_window) -> SceneViewerWindow:
    viewer = viewer_for(main_window)
    viewer.show()
    viewer.raise_()
    viewer.activateWindow()
    return viewer


def add_selection_to_viewer(main_window):
    viewer = show_viewer(main_window)
    viewer.add_from_main_window()


def install(app) -> None:
    """Tools menu items, "Add to Scene Viewer" in both context menus."""
    for window in app.topLevelWidgets():
        if not hasattr(window, "entry_category_filter_combobox") or getattr(window, "_scene_viewer_installed", False):
            continue
        window._scene_viewer_installed = True
        tools = None
        for action in window.menuBar().actions():
            if action.menu() is not None and action.text().replace("&", "") == "Tools":
                tools = action.menu()
        if tools is not None:
            # Right under the file viewers: the Scene Viewer and the creator; export switches live with the exports
            open_action = QtGui.QAction("Scene Viewer", tools)
            open_action.setShortcut(QtGui.QKeySequence("Ctrl+Shift+V"))
            open_action.triggered.connect(lambda _checked=False, w=window: show_viewer(w))
            tools.addAction(open_action)
            makeup_action = QtGui.QAction("Main Character Creator", tools)
            makeup_action.setToolTip(
                "Dress a face like the game's face editor (skin tone, makeup, face shape, hair, "
                "outfit, body size I / II) and export it with the makeup baked in."
            )

            def open_makeup(_checked=False, w=window):
                from gui.widgets import face_makeup_dialog

                face_makeup_dialog.open_dialog(w)

            makeup_action.triggered.connect(open_makeup)
            tools.addAction(makeup_action)
        # Not in a menu any more, but the shortcut still adds the selection to the Scene Viewer
        add_action = QtGui.QAction("Add Selected to Scene Viewer", window)
        add_action.setShortcut(QtGui.QKeySequence("Ctrl+Shift+A"))
        add_action.triggered.connect(lambda _checked=False, w=window: add_selection_to_viewer(w))
        window.addAction(add_action)

        try:
            from gui.widgets import animations_page

            animations_page.install(window)
        except Exception as exc:
            print(f"Animations tab not installed: {exc}")

        characters = getattr(window, "_character_folders_window", None)
        if characters is not None and hasattr(characters, "_create_context_menu"):
            original = characters._create_context_menu

            def create_context_menu(items, original=original, w=window):
                menu = original(items)
                menu.addSeparator()
                menu.addAction("Add to Scene Viewer", lambda: add_selection_to_viewer(w))
                face_folder = _selected_face_folder(w)
                if face_folder:

                    def open_face_makeup(w=w, folder=face_folder):
                        from gui.widgets import face_makeup_dialog

                        face_makeup_dialog.open_dialog(w, folder)

                    menu.addAction(f"Main Character Creator ({ntpath.basename(face_folder)})…", open_face_makeup)
                menu.addAction("Export all as one GLB…", lambda: export_character_selection(w, "glb"))
                menu.addAction("Export all as one PMX…", lambda: export_character_selection(w, "pmx"))
                # Owned by the menu and kept on it: a submenu made by addMenu("title") can be
                # collected by PySide as soon as this function returns
                each = QtWidgets.QMenu("Export each mesh separately", menu)
                for label, fmt in EACH_FORMATS:
                    each.addAction(
                        f"{label} (one folder per mesh)…", lambda fmt=fmt: export_character_meshes_each(w, fmt)
                    )
                menu.addMenu(each)
                per_folder = QtWidgets.QMenu("Export each folder as one file", menu)
                per_folder.setToolTipsVisible(True)
                for label, fmt in EACH_FORMATS:
                    action = per_folder.addAction(
                        f"{label} (one file per folder)…",
                        lambda fmt=fmt: export_character_meshes_each(w, fmt, per_folder=True),
                    )
                    action.setToolTip(
                        "Every folder that holds meshes becomes one model, each mesh with its own "
                        "materials (e.g. a face folder: face, eyes, eyebrows, lashes)."
                    )
                menu.addMenu(per_folder)
                faces = QtWidgets.QMenu("Export faces (face + eyes + eyebrows)", menu)
                faces.setToolTipsVisible(True)
                for label, fmt in EACH_FORMATS:
                    action = faces.addAction(
                        f"{label} (one file per face)…",
                        lambda fmt=fmt: export_character_meshes_each(w, fmt, faces_only=True),
                    )
                    action.setToolTip(
                        "Each face folder in the selection (e.g. body\\male_body_03) becomes one model: "
                        "face, eyeballs, lashes and eyebrows, each as its own material with its texture. "
                        "Other folders in the selection are skipped."
                    )
                faces.addSeparator()
                for label, fmt in EACH_FORMATS:
                    action = faces.addAction(
                        f"{label} with face shape variants…",
                        lambda fmt=fmt: export_character_meshes_each(w, fmt, face_shapes=True),
                    )
                    action.setToolTip(
                        "Like above, plus one more model per face shape the game builds from that face "
                        "(its .mb files: nose, jaw, eyes, mouth moved), saved as <face>_shape_NN."
                    )
                menu.addMenu(faces)
                hats = QtWidgets.QMenu("Export hats only (no hair masks)", menu)
                hats.setToolTipsVisible(True)
                for label, fmt in EACH_FORMATS:
                    action = hats.addAction(
                        f"{label} (one folder per hat)…",
                        lambda fmt=fmt: export_character_meshes_each(w, fmt, hats_only=True),
                    )
                    action.setToolTip(
                        "Each hat in the selection gets its own folder with its textures. The mask "
                        "folders (hairstyles trimmed to fit under each hat) are skipped."
                    )
                menu.addMenu(hats)
                icons = QtWidgets.QMenu("Export item icons (PNG)", menu)
                icons.setToolTipsVisible(True)
                action = icons.addAction("Icons of the selected items…", lambda: export_selected_item_icons(w))
                action.setToolTip(
                    "Each selected outfit, hat, hair, accessory or face: its in-game icon, saved as " "<item name>.png."
                )
                action = icons.addAction("Every character item icon…", lambda: export_item_icons(w))
                action.setToolTip(
                    "All of the game's icons for outfits, hats, hair, faces, eyes, earrings, glasses… "
                    "as PNG files with their game names."
                )
                menu.addMenu(icons)
                options = QtWidgets.QMenu("Export options", menu)
                options.setToolTipsVisible(True)
                item_info = options.addAction(ITEM_INFO_LABEL)
                item_info.setCheckable(True)
                item_info.setChecked(include_item_info())
                item_info.setToolTip(
                    "Per-mesh / per-folder / face / hat exports: name each model's folder after its "
                    "in-game item in English and save the item's icon next to it (<name>_icon.png)."
                )
                settings = QtWidgets.QApplication.instance().property("settings_manager")
                if settings is not None:
                    item_info.toggled.connect(
                        lambda checked, st=settings: st.set(ITEM_INFO_SETTING, bool(checked), True)
                    )
                try:
                    from core.mesh_converter.formats.pmx import pmx_option_actions

                    options.addSection("PMX")
                    for action in pmx_option_actions(options):
                        options.addAction(action)
                except Exception:
                    get_logger().exception("Could not add the PMX options to the menu")
                menu.addMenu(options)
                menu._each_menu, menu._folder_menu, menu._faces_menu, menu._hats_menu = each, per_folder, faces, hats
                menu._icons_menu, menu._options_menu = icons, options
                return menu

            characters._create_context_menu = create_context_menu

        file_list = getattr(window, "list_widget", None)
        if file_list is not None and hasattr(file_list, "show_context_menu"):
            _wrap_file_list_menu(file_list, window)


def _wrap_file_list_menu(file_list, window):
    """The file list builds and runs its menu in one call, so add the item while that menu opens."""
    original = file_list.show_context_menu
    try:
        if not file_list.customContextMenuRequested.disconnect(original):
            return
    except (RuntimeError, TypeError):
        return

    def show_context_menu(position):
        def add_item():
            # Runs inside the menu's own event loop, while it is the active popup
            menu = QtWidgets.QApplication.activePopupWidget()
            if (
                isinstance(menu, QtWidgets.QMenu)
                and not menu.property("scene_viewer_item")
                and selected_mesh_specs(window)
            ):
                menu.setProperty("scene_viewer_item", True)
                menu.addSeparator()
                menu.addAction("Add to Scene Viewer", lambda: add_selection_to_viewer(window))

        QtCore.QTimer.singleShot(0, add_item)
        original(position)

    file_list.customContextMenuRequested.connect(show_context_menu)
