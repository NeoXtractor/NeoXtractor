"""The Animations category: every animation in the game, grouped by skeleton like the Characters tab.

The selected clip plays on its model in the viewer area. Right-click a clip to export it as GLB, or as VMD
(with the model as PMX when it isn't the player character).
"""

from __future__ import annotations

import os
import threading

from PySide6 import QtCore, QtGui, QtWidgets
from core.logger import get_logger

GROUP_ROLE = QtCore.Qt.ItemDataRole.UserRole + 51
CLIP_ROLE = QtCore.Qt.ItemDataRole.UserRole + 52
SEARCH_LIMIT = 3000

class _Signals(QtCore.QObject):
    scanned = QtCore.Signal(object, str)
    preview = QtCore.Signal(int, object, str)


class AnimationsPage(QtWidgets.QWidget):
    def __init__(self, main_window):
        super().__init__(main_window)
        self.main_window = main_window
        self.game = None
        self.index = None
        self.groups = []
        self.generation = 0
        self.current = None            # (group, clip name)
        self.preview_object = None

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("Search animations or models (e.g. dance, run, hippogriff, idle)…")
        self.search.setClearButtonEnabled(True)
        layout.addWidget(self.search)
        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        layout.addWidget(splitter, 1)
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderLabels(["Animation", "Clips"])
        self.tree.setColumnWidth(0, 300)
        self.tree.setUniformRowHeights(True)
        splitter.addWidget(self.tree)

        details = QtWidgets.QWidget()
        form = QtWidgets.QVBoxLayout(details)
        form.setContentsMargins(4, 4, 4, 4)
        self.info = QtWidgets.QLabel("Open any Harry Potter: Magic Awakened .npk, then pick an animation.")
        self.info.setWordWrap(True)
        self.info.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        form.addWidget(self.info)
        form.addStretch(1)
        splitter.addWidget(details)
        splitter.setSizes([640, 160])

        # The player, shown in the main window's viewer area while this page is open (see install)
        self.viewer = QtWidgets.QWidget()
        viewer_layout = QtWidgets.QVBoxLayout(self.viewer)
        viewer_layout.setContentsMargins(0, 0, 0, 0)
        bar = QtWidgets.QHBoxLayout()
        self.play = QtWidgets.QPushButton("Pause")
        self.play.setToolTip("Pause or play the animation")
        self.play.clicked.connect(self._toggle_play)
        bar.addWidget(self.play)
        bar.addStretch(1)
        self.bones = QtWidgets.QCheckBox("Show Bones")
        self.bones.setChecked(False)
        self.bones.toggled.connect(self._toggle_bones)
        bar.addWidget(self.bones)
        bar.addStretch(1)
        viewer_layout.addLayout(bar)
        try:
            from gui.widgets.scene_render import SceneRenderWidget
            self.view = SceneRenderWidget()
        except Exception as exc:
            self.view = QtWidgets.QLabel(f"3D preview unavailable: {exc}")
        viewer_layout.addWidget(self.view, 1)
        # Over the view, as in the mesh viewer: the clip's numbers top left, the keys bottom left, and the model
        # the clip plays on top right
        self.model = QtWidgets.QComboBox(self.view)
        self.model.setToolTip("The model the animation plays on: the one matching its skeleton is picked; any other "
                              "model from the same folder can be chosen.")
        self.model.setSizeAdjustPolicy(QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.model.hide()
        from gui.widgets.viewer_overlay import ViewOverlays
        self.overlays = ViewOverlays(self.view, corner=self.model)

        self._signals = _Signals(self)
        self._signals.scanned.connect(self._scanned)
        self._signals.preview.connect(self._preview_ready)
        self._search_timer = QtCore.QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(250)
        self._search_timer.timeout.connect(self._fill)
        self.search.textChanged.connect(lambda _text: self._search_timer.start())
        self.tree.itemExpanded.connect(self._expand)
        self.tree.currentItemChanged.connect(self._selected)
        self.model.currentIndexChanged.connect(lambda _i: self._load_preview())

    def _show_clip_info(self, rows):
        self.overlays.set_info(rows)

    # -- loading -----------------------------------------------------------------------------
    def activate(self):
        """Called when the page is shown: scan the game's animations once per game folder."""
        from gui.widgets.character_anim_export import _game_folder, _game_options
        game = _game_folder()
        if game is None:
            self.info.setText("Open any Harry Potter: Magic Awakened .npk first, so the game folder is known.")
            return
        if self.game is not None and str(game) == str(self.game):
            return                                 # this game is listed, or its scan is still running
        self.game = game
        self.info.setText("Reading the game's file list…")
        threading.Thread(target=_scan, args=(game, _game_options(), self._signals), daemon=True).start()

    def _scanned(self, payload, error):
        if error:
            self.game = None                       # so opening Animations again tries once more
            self.info.setText(f"Could not list the animations: {error}")
            return
        self.index, self.groups = payload
        total = sum(len(g.clips) for g in self.groups)
        self.info.setText(f"{total:,} animations of {len(self.groups):,} models. Pick one to preview it.")
        self._fill()

    def _fill(self):
        self.tree.clear()
        words = self.search.text().lower().split()
        folders: dict[str, QtWidgets.QTreeWidgetItem] = {}      # lower-case game folder -> its row
        totals: dict[str, int] = {}                             # clips under each folder
        shown = 0
        for group in sorted(self.groups, key=lambda g: g.folder.lower()):
            group_text = f"{group.label} {group.folder}".lower()
            if words:
                group_match = all(w in group_text for w in words)
                clips = group.clips if group_match else [c for c in group.clips
                                                        if all(w in (c.lower() + " " + group_text) for w in words)]
                if not clips:
                    continue
            else:
                clips = None
            # The game's folders down to the animations_ folder, as in the Characters tab
            parts = group.folder.split("\\")
            parent = None
            for depth in range(1, len(parts)):
                key = "\\".join(parts[:depth]).lower()
                node = folders.get(key)
                if node is None:
                    node = QtWidgets.QTreeWidgetItem(parent if parent is not None else self.tree, [parts[depth - 1], ""])
                    node.setToolTip(0, "\\".join(parts[:depth]))
                    folders[key] = node
                totals[key] = totals.get(key, 0) + len(group.clips)
                parent = node
            item = QtWidgets.QTreeWidgetItem(parent if parent is not None else self.tree,
                                             [parts[-1], f"{len(group.clips):,}"])
            item.setToolTip(0, f"{group.folder}\nPlays on {group.label}")
            item.setData(0, GROUP_ROLE, group)
            if clips is None:
                item.setChildIndicatorPolicy(QtWidgets.QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)
            else:
                for clip in clips[:SEARCH_LIMIT - shown]:
                    child = QtWidgets.QTreeWidgetItem(item, [clip, ""])
                    child.setData(0, CLIP_ROLE, (group, clip))
                shown += len(clips)
                item.setExpanded(True)
            if words and shown >= SEARCH_LIMIT:
                break
        for key, node in folders.items():
            node.setText(1, f"{totals[key]:,}")
            node.setExpanded(bool(words) or "\\" not in key)     # searching opens the way to each match

    def _expand(self, item):
        group = item.data(0, GROUP_ROLE)
        if group is None or item.childCount():
            return
        for clip in group.clips:
            child = QtWidgets.QTreeWidgetItem(item, [clip, ""])
            child.setData(0, CLIP_ROLE, (group, clip))

    # -- preview -----------------------------------------------------------------------------
    def _selected(self, item, _previous):
        if item is None:
            return
        data = item.data(0, CLIP_ROLE)
        if data is None:
            group = item.data(0, GROUP_ROLE)
            if group is None:
                return
            data = (group, group.clips[0]) if group.clips else None
            if data is None:
                return
        group, clip = data
        if self.current is None or self.current[0] is not group:
            blocker = QtCore.QSignalBlocker(self.model)
            self.model.clear()
            for path in ([group.default_model] if group.default_model else []) + \
                    [m for m in group.models if m != group.default_model]:
                self.model.addItem(path.rsplit("\\", 1)[-1], path)
            del blocker
            self.model.setVisible(self.model.count() > 0)
            self.overlays.place()
        self.current = (group, clip)
        self._load_preview()

    def _load_preview(self):
        if self.current is None or self.index is None:
            return
        group, clip = self.current
        self.generation += 1
        model = self.model.currentData()
        self.info.setText(f"<b>{clip}</b> — {group.label}<br>Loading…")
        self._show_clip_info([("Clip", clip), ("Owner", group.label), ("Status", "Loading…")])
        threading.Thread(target=_load_preview, args=(self.generation, self.index, group, clip, model, self._signals),
                         daemon=True).start()

    def _preview_ready(self, generation, payload, error):
        if generation != self.generation:
            return
        group, clip_name = self.current
        model_path = self.model.currentData()
        if error:
            self.info.setText(f"<b>{clip_name}</b> — {group.label}<br>{error}<br><small>{group.folder}</small>")
            self._show_clip_info([("Clip", clip_name), ("Owner", group.label)])
        if payload is None:
            return
        mesh, parts, clip, matched = payload
        self.info.setText(f"<b>{clip_name}</b> — {group.label}<br><small>Model: {model_path}<br>"
                          f"Animation: {group.folder}\\{clip_name}<br>Skeleton: {group.skeleton}</small>")
        self._show_clip_info([("Clip", clip_name), ("Model", (model_path or "").rsplit("\\", 1)[-1]),
                              ("Duration", f"{clip.duration:.2f} s"), ("Frames", clip.animation.frame_count),
                              ("Bones", f"{len(clip.bone_names)} ({matched} on the model)"),
                              ("Vertices", len(mesh.position)), ("Triangles", len(mesh.face))])
        if not hasattr(self.view, "add_object"):
            return
        from gui.widgets.scene_render import SceneObject
        if self.preview_object is not None:
            self.view.remove_objects([self.preview_object])
        obj = SceneObject(clip_name, mesh, None, model_path, "")
        for number, part in enumerate(obj.parts):
            info = parts[number] if number < len(parts) else None
            if info and info.get("qimage") is not None:
                part.texture_key = self.view.set_texture("game:" + (info.get("texture") or str(id(info))),
                                                         info["qimage"], (info.get("texture") or "").rsplit("\\", 1)[-1])
                part.alpha = info.get("alpha", "OPAQUE")
                part.cutoff = info.get("cutoff", 0.5)
        obj.set_animation(clip)
        self.preview_object = obj
        self.view.add_object(obj)
        self.view.show_bones = self.bones.isChecked()
        self.view.focus([obj])
        self.view.update()

    def _toggle_play(self):
        if hasattr(self.view, "set_playing"):
            playing = not self.view.playing
            self.view.set_playing(playing)
            self.play.setText("Pause" if playing else "Play")

    def _toggle_bones(self, checked):
        if hasattr(self.view, "show_bones"):
            self.view.show_bones = checked
            self.view.update()

    # -- output ------------------------------------------------------------------------------
    def _export(self, kind):
        if self.current is None:
            return
        export_animation(self.main_window, self.index, self.current[0], self.current[1], self.model.currentData(), kind)


def _scan(game, options, signals):
    from core.npk import anim_library
    from gui.widgets.character_anim_export import _index
    try:
        index = _index(game, options)
        payload, error = (index, anim_library.scan(index.manifest())), ""
    except Exception as exc:
        payload, error = None, str(exc)
    try:
        signals.scanned.emit(payload, error)
    except RuntimeError:
        pass


def _preview_payload(index, group, clip_name, model_path):
    """((mesh, parts with QImages, clip, matched bones), error)."""
    from core.npk import anim_library
    from core.npk.stage_export import StageLoader
    from gui.widgets.scene_render import pil_to_qimage
    read = lambda p: (index.read(p) or (None,))[0]  # noqa: E731
    try:
        clip = anim_library.load_clip(read, group, clip_name)
    except ValueError as exc:
        if "Unsupported" in str(exc):
            return None, "This animation is stored in a format that can't be read yet."
        return None, f"Could not read this animation: {exc}"
    except Exception as exc:
        return None, f"Could not read this animation: {exc}"
    if not model_path:
        return None, "No model found next to this animation."
    model = StageLoader(index).model(model_path)
    if model is None:
        return None, f"The model {model_path} could not be read."
    mesh, parts = model
    parts = [dict(p) for p in parts]
    for part in parts:
        if part.get("image") is not None:
            part["qimage"] = pil_to_qimage(part["image"])
        part["image"] = None
    matched = anim_library.match_bones(clip, mesh)
    if not matched:
        return None, "The chosen model has none of this animation's bones; pick another model."
    return (mesh, parts, clip, matched), ""


def _load_preview(generation, index, group, clip_name, model_path, signals):
    payload, error = _preview_payload(index, group, clip_name, model_path)
    try:
        signals.preview.emit(generation, payload, error)
    except RuntimeError:
        pass


def export_animation(main_window, index, group, clip_name, model_path, kind):
    """GLB: model + animation. VMD: the player rigs as VMD (for exported PMX), other models as PMX + VMD."""
    from gui.widgets.character_exports import start_export

    def build(progress, cancelled, report, target):
        from core.mesh_converter import character_anim, model_export, vmd_export
        from core.npk import anim_library
        from core.npk.stage_export import StageLoader
        read = lambda p: (index.read(p) or (None,))[0]  # noqa: E731
        progress(f"Exporting {clip_name}…", 0, 1)
        clip = anim_library.load_clip(read, group, clip_name)
        safe = character_anim.safe_name(clip_name)
        if group.is_player and kind == "vmd":
            sex = "female" if "female" in group.parent.lower() else "male"
            skeleton = character_anim.load_skeleton(read, sex)
            player_clip = character_anim.load_clip(read, sex, clip_name, skeleton)
            path, scaled = character_anim.write_vmd(player_clip, skeleton, os.path.join(target, safe + ".vmd"))
            if scaled:
                report.append("This animation also scales bones, which VMD cannot store; that part is left out.")
            return str(path)
        model = StageLoader(index, max_texture_size=8192).model(model_path) if model_path else None
        if model is None:
            raise ValueError("The model could not be read.")
        anim_library.match_bones(clip, model[0])
        model_name = model_path.rsplit("\\", 1)[-1].rsplit(".", 1)[0]
        item = {"name": model_name, "mesh": model[0], "parts": model[1], "animation": clip}
        if kind == "glb":
            return str(model_export.write_glb([item], os.path.join(target, f"{model_name}_{safe}.glb")))
        folder = os.path.join(target, f"{model_name}_{safe}")
        vmd_export.write_pmx_vmd(item, folder)
        return f"{model_name}.pmx + {safe} motion in {folder}"

    start_export(main_window, f"each:{'glb' if kind == 'glb' else 'vmd'}", clip_name, build)


def install(window) -> None:
    """Add "Animations" to the window's category list and its page next to the file list."""
    combo = getattr(window, "entry_category_filter_combobox", None)
    stack = getattr(window, "left_content", None)
    if combo is None or stack is None or getattr(window, "_animations_page", None) is not None:
        return
    page = AnimationsPage(window)
    window._animations_page = page
    stack.addWidget(page)
    combo.addItem("Animations", "animations")
    preview = getattr(window, "preview_widget", None)
    area = getattr(preview, "widget_layout", None)
    if area is not None:
        area.addWidget(page.viewer, 1)
    page.viewer.hide()

    def show_player(shown):
        if area is None:
            return
        if shown:
            for previewer in getattr(preview, "_previewers", []):
                previewer.setVisible(False)
            label = getattr(preview, "message_label", None)
            if label is not None:
                label.setVisible(False)
            try:
                preview.set_control_bar_visible(False)
            except Exception:
                pass
            page.viewer.show()
        elif page.viewer.isVisible():
            page.viewer.hide()
            try:
                preview.clear()                    # back to "select an entry" for the file list
            except Exception:
                get_logger().debug("Could not reset the preview area", exc_info=True)

    def changed(_index):
        if combo.currentData() == "animations":
            try:
                window.filter.filter_type = None
            except Exception:
                get_logger().debug("Could not clear the type filter", exc_info=True)
            stack.setCurrentWidget(page)
            show_player(True)
            for name in ("extract_button_widget", "mesh_biped_head_filter_checkbox"):   # they act on the file list
                widget = getattr(window, name, None)
                if widget is not None:
                    widget.hide()
            page.activate()
        else:
            show_player(False)

    combo.currentIndexChanged.connect(changed)

    # Right-click: the batch export of the character animations (moved here from the Tools menu)
    page.tree.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)

    def context_menu(position, page=page):
        menu = QtWidgets.QMenu(page.tree)
        menu.setToolTipsVisible(True)
        item = page.tree.itemAt(position)
        if item is not None and item.data(0, CLIP_ROLE) is not None:
            if page.tree.currentItem() is not item:
                page.tree.setCurrentItem(item)
            glb = menu.addAction("Export GLB…")
            glb.setToolTip("The model with this animation, as an animated GLB.")
            glb.triggered.connect(lambda _checked=False: page._export("glb"))
            vmd = menu.addAction("Export VMD…")
            vmd.setToolTip("Player character: a VMD for the PMX models exported from this app. "
                           "Other models: the model as PMX with this motion as VMD.")
            vmd.triggered.connect(lambda _checked=False: page._export("vmd"))
            menu.addSeparator()
        action = menu.addAction("Export character animations (VMD / GLB)…")
        action.setToolTip("Save the male / female character animations (dances, runs, idles, spells…) as VMD motions "
                          "for the exported PMX models, or as animated GLB files.")

        def open_export():
            from gui.widgets import character_anim_export
            character_anim_export.open_dialog(window)

        action.triggered.connect(open_export)
        menu.exec(page.tree.viewport().mapToGlobal(position))

    page.tree.customContextMenuRequested.connect(context_menu)
