"""Embedded Characters filter: real meshes and material-linked textures."""

import json
from pathlib import Path
from threading import Event
from time import perf_counter
from types import SimpleNamespace
from PySide6 import QtCore, QtGui, QtWidgets
import shiboken6
from core.npk.class_types import NPKEntry
from core.process_memory import check_scan_memory, ScanMemoryLimitExceeded
from gui.theme import ThemeManager
from gui.utils.viewer import ALL_VIEWERS
from gui.utils.character_scan import CharacterScanWorker
from gui.utils.file_list_theme import apply_file_list_theme
from gui.utils.character_tree_index import CharacterTreeIndex

PATH_ROLE = QtCore.Qt.ItemDataRole.UserRole
ENTRY_ROLE = PATH_ROLE + 1
KIND_ROLE = PATH_ROLE + 2
VISIBLE_ASSET_ROLE = PATH_ROLE + 3
FOLDER_PATH_ROLE = PATH_ROLE + 4
ASSET_KEY_ROLE = PATH_ROLE + 5
LAZY_ROLE = PATH_ROLE + 6
LOADED_ROLE = PATH_ROLE + 7
PAGE_ROLE = PATH_ROLE + 8
PAGE_SIZE = 200


class CharacterFoldersWindow(QtWidgets.QWidget):
    """Main-window filter page, not a dialog."""
    preview_entry_requested = QtCore.Signal(int)
    unresolved_requested = QtCore.Signal(str)
    open_entry_with = QtCore.Signal(int, NPKEntry, type, int)
    scan_started = QtCore.Signal()
    scan_progress = QtCore.Signal(str, int, int)
    scan_finished = QtCore.Signal(str)

    def __init__(self, npk_file=None, roots=None, parent=None):
        super().__init__(parent)
        self.npk_file, self.roots = npk_file, roots
        self.signature_name_map = {}
        self.catalog = None
        self.scan_busy = False
        self._scan_generation = 0
        self._cancel_event = None
        self._workers = {}
        self._render_iterator = None
        self._item_count = 0
        self._filters_dirty = False
        self._tree_index = None
        self._directory_items = {}
        self._folder_icon = self._file_icon = None
        self._render_timer = QtCore.QTimer(self)
        self._render_timer.setInterval(1)
        self._render_timer.timeout.connect(self._advance_render)
        self.search_text = ""
        self.include_binary = self.include_text = self.include_slot = True
        self.setObjectName("characterFoldersPage")
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QtWidgets.QHBoxLayout()
        self.asset_filter = QtWidgets.QComboBox()
        for label, kind in (("All assets", ""), ("Meshes", "Mesh"), ("Textures", "Texture"), ("Metadata", "Metadata")):
            self.asset_filter.addItem(label, kind)
        self.asset_filter.currentIndexChanged.connect(self.apply_filter)
        row.addWidget(self.asset_filter)
        self.show_missing = QtWidgets.QCheckBox("Show missing references")
        self.show_missing.toggled.connect(self.apply_filter)
        self.show_missing.setToolTip("Metadata paths absent from the loaded archive.")
        row.addWidget(self.show_missing)
        layout.addLayout(row)
        self.explorer_heading = QtWidgets.QLabel("Characters Explorer")
        layout.addWidget(self.explorer_heading)
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderHidden(True)
        self.tree.setAlternatingRowColors(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setColumnWidth(0, 290)
        self.tree.header().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Interactive)
        self.tree.header().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.Interactive)
        self.tree.setColumnWidth(1, 250)
        self.tree.currentItemChanged.connect(self._selection_changed)
        self.tree.itemDoubleClicked.connect(self._double_clicked)
        self.tree.itemExpanded.connect(self._populate_node)
        self.tree.itemCollapsed.connect(self._release_node)
        self.tree.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        layout.addWidget(self.tree, 1)
        self.status_label = QtWidgets.QLabel()
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        try:
            self.theme = ThemeManager.instance()
            self.theme.theme_changed.connect(self._apply_palette)
        except RuntimeError:
            self.theme = None
        self._apply_palette()
        self.refresh()

    def _apply_palette(self, *_):
        apply_file_list_theme(self.tree, self.theme)

    def set_archive(self, npk_file, roots=None, signature_name_map=None):
        self.cancel_scan(announce=False)
        if self.npk_file is not npk_file:
            self.tree.clear()
            self.catalog = None
            self._tree_index = None
            self._directory_items.clear()
            self._item_count = 0
        self.npk_file, self.roots = npk_file, roots
        self.signature_name_map = signature_name_map or {}
        self.refresh()

    def _folder_item(self, parent, label, detail=""):
        item = QtWidgets.QTreeWidgetItem([label, detail])
        if self._folder_icon is None:
            self._folder_icon = self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_DirIcon)
        item.setIcon(0, self._folder_icon)
        parent.addChild(item)
        self._item_count += 1
        return item

    def _asset_item(self, parent, asset, label=None, key=None):
        entry = self.npk_file.entries.get(asset.entry_index)
        item = QtWidgets.QTreeWidgetItem([label or asset.path.rsplit("\\", 1)[-1],
                                          hex(entry.file_signature) if entry is not None else "Not in archive"])
        if entry is not None and hasattr(self.npk_file, "source_label"):
            item.setText(1, hex(entry.file_signature) + " · " + self.npk_file.source_label(asset.entry_index))
        item.setData(0, PATH_ROLE, asset.path)
        item.setData(0, ENTRY_ROLE, asset.entry_index)
        item.setData(0, KIND_ROLE, asset.kind)
        item.setData(0, ASSET_KEY_ROLE, key)
        item.setToolTip(0, asset.path)
        item.setToolTip(1, asset.evidence + (f" · entry {asset.entry_index}" if entry is not None else ""))
        if self._file_icon is None:
            self._file_icon = self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_FileIcon)
        item.setIcon(0, self._file_icon)
        parent.addChild(item)
        self._item_count += 1
        if asset.kind == "Mesh" and (asset.textures or asset.metadata):
            item.setData(0, LAZY_ROLE, ("mesh", key, ""))
            item.setChildIndicatorPolicy(QtWidgets.QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)
        return item

    def refresh(self):
        self.cancel_scan(announce=False)
        if self.npk_file is None:
            self.tree.clear()
            self.catalog = None
            self._tree_index = None
            self._directory_items.clear()
            self._item_count = 0
            self.status_label.setText("Load an archive to browse character folders.")
            return
        self._scan_generation += 1
        generation = self._scan_generation
        self._cancel_event = Event()
        # The worker reads a snapshot; a related-archive set does its own
        # locking and is shared directly.
        snapshot = self.npk_file if hasattr(self.npk_file, "build_catalog") else SimpleNamespace(
            file_path=self.npk_file.file_path, entries=dict(self.npk_file.entries),
            indices=list(getattr(self.npk_file, "indices", ())))
        worker = CharacterScanWorker(generation, snapshot,
                                     list(self.roots) if self.roots is not None else None,
                                     dict(self.signature_name_map), self._cancel_event)
        worker.signals.progress.connect(self._scan_update)
        worker.signals.finished.connect(self._scan_result)
        self._workers[generation] = worker
        self.scan_busy = True
        self.tree.setEnabled(False)
        self.status_label.setText("Scanning character folders…")
        self.scan_started.emit()
        self.scan_progress.emit("Starting character scan", 0, 1)
        QtCore.QThreadPool.globalInstance().start(worker)

    @QtCore.Slot(int, str, int, int)
    def _scan_update(self, generation, stage, current, total):
        if generation == self._scan_generation and self.scan_busy:
            self.scan_progress.emit(stage, current, total)

    @QtCore.Slot(int, object, str)
    def _scan_result(self, generation, catalog, error):
        self._workers.pop(generation, None)
        if generation != self._scan_generation or not self.scan_busy:
            return
        if error:
            self.status_label.setText("Character scan cancelled." if error == "cancelled"
                                      else "Character scan failed: " + error)
            self._finish_scan(error)
            return
        self.tree.clear()
        self._directory_items.clear()
        self._item_count = 0
        self.catalog = catalog
        self._tree_index = CharacterTreeIndex(catalog, self.npk_file.entries)
        self._render_iterator = self._render_tree_steps()
        self._render_timer.start()

    def cancel_scan(self, announce=True):
        if self._cancel_event is not None:
            self._cancel_event.set()
        self._scan_generation += 1
        self._render_timer.stop()
        if self._render_iterator is not None:
            self._render_iterator.close()
            self._render_iterator = None
            self.tree.clear()
            self.catalog = None
            self._tree_index = None
            self._directory_items.clear()
            self._item_count = 0
        if self.scan_busy:
            if announce:
                self.status_label.setText("Character scan cancelled. Use Tools → Scan character folders to retry.")
            self._finish_scan("cancelled")

    def _finish_scan(self, outcome):
        self.scan_busy = False
        self._cancel_event = None
        self.tree.setEnabled(True)
        if outcome == "complete":
            self._populate_expanded()
        self.scan_finished.emit(outcome)

    def _populate_expanded(self):
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            if item.isExpanded() and not item.isHidden():
                self._populate_node(item)

    def _advance_render(self):
        # Build the tree in small time slices so the window stays responsive.

        deadline = perf_counter() + 0.008
        update = None
        try:
            check_scan_memory()
            for _ in range(150):
                update = next(self._render_iterator)
                if perf_counter() >= deadline:
                    break
        except StopIteration:
            self._render_timer.stop()
            self._render_iterator = None
            self._finish_scan("complete")
        except Exception as exc:
            self.cancel_scan(announce=False)
            self.status_label.setText("Could not display character folders: " + str(exc))
        if update is not None and self.scan_busy:
            self.scan_progress.emit(*update)

    def _render_tree_steps(self):
        directory_items = self._directory_items
        directory_counts = {}
        invisible = self.tree.invisibleRootItem()
        # Directories are created up front; assets are added only when a
        # directory is expanded.
        folders = sorted(self.catalog.folders.items())
        for current, (folder, paths) in enumerate(folders, 1):
            parts = folder.split("\\")
            parent = invisible
            records = [self.catalog.assets[p] for p in paths]
            for depth in range(2, len(parts) + 1):
                directory = "\\".join(parts[:depth])
                if directory not in directory_items:
                    label = directory if depth == 2 else parts[depth - 1]
                    item = self._folder_item(parent, label)
                    item.setData(0, FOLDER_PATH_ROLE, directory)
                    item.setToolTip(0, directory)
                    item.setExpanded(depth == 2)
                    item.setData(0, LAZY_ROLE, ("folder", directory, ""))
                    if directory in self.catalog.folders:
                        item.setChildIndicatorPolicy(QtWidgets.QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)
                    directory_items[directory] = item
                    directory_counts[directory] = {"Mesh": set(), "Texture": set()}
                parent = directory_items[directory]
                for asset in records:
                    if asset.kind in ("Mesh", "Texture") and asset.entry_index is not None:
                        directory_counts[directory][asset.kind].add(asset.entry_index)
                yield "Building character directories", current, max(len(folders), 1)
        for current, (directory, counts) in enumerate(directory_counts.items(), 1):
            directory_items[directory].setText(
                1, f"{len(counts['Mesh'])} meshes · {len(counts['Texture'])} textures")
            yield "Counting directory contents", current, max(len(directory_counts), 1)
        if self.catalog.unmapped:
            root = self._folder_item(invisible, "Unidentified archive assets", "No recovered path")
            root.setData(0, LAZY_ROLE, ("unmapped", "", ""))
            root.setChildIndicatorPolicy(QtWidgets.QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)
        while True:
            self._filters_dirty = False
            yield from self._filter_steps()
            if not self._filters_dirty:
                return

    def _lazy_group(self, parent, spec, label):
        keys = self._tree_index.keys_for(spec)
        if not keys:
            return None
        group = self._folder_item(parent, label, str(len(keys)))
        group.setData(0, LAZY_ROLE, spec)
        group.setChildIndicatorPolicy(QtWidgets.QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)
        return group

    def _delete_child(self, parent, index):
        child = parent.takeChild(index)
        def size(item):
            return 1 + sum(size(item.child(i)) for i in range(item.childCount()))
        self._item_count -= size(child)
        shiboken6.delete(child)

    def _release_node(self, item):
        # Collapsing frees the rows below; directories stay so their
        # expansion state is kept.
        if self._tree_index is None or self.scan_busy:
            return
        with QtCore.QSignalBlocker(self.tree):
            self._release_rows(item)

    def _release_rows(self, item):
        for i in range(item.childCount() - 1, -1, -1):
            child = item.child(i)
            if child.data(0, FOLDER_PATH_ROLE):
                self._release_rows(child)
            else:
                self._delete_child(item, i)
        item.setData(0, LOADED_ROLE, False)

    def _populate_node(self, item):
        if self._tree_index is None or self.scan_busy:
            return
        try:
            check_scan_memory(force=True)
        except ScanMemoryLimitExceeded as exc:
            self.status_label.setText(str(exc))
            return
        spec = tuple(item.data(0, LAZY_ROLE) or ())
        if not spec or item.data(0, LOADED_ROLE):
            return
        tag, path, _kind = spec
        if tag == "page":
            parent, offset = item.parent(), item.data(0, PAGE_ROLE)
            self._delete_child(parent, parent.indexOfChild(item))
            self._populate_page(parent, offset)
            return
        item.setData(0, LOADED_ROLE, True)
        if tag == "folder":
            for kind in ("Mesh", "Texture", "Metadata"):
                group = self._lazy_group(item, ("group", path, kind), "Textures" if kind == "Texture" else kind)
                if group is not None:
                    group.setExpanded(True)
        elif tag == "mesh":
            for kind in ("Texture", "Metadata"):
                self._lazy_group(item, ("links", path, kind), "Textures" if kind == "Texture" else kind)
        else:
            # Groups, links and unmapped assets are paged.
            self._populate_page(item, 0)
        # Expanded subdirectories were released with their parent.
        if tag == "folder":
            for i in range(item.childCount()):
                child = item.child(i)
                if child.data(0, FOLDER_PATH_ROLE) and child.isExpanded():
                    self._populate_node(child)

    def _populate_page(self, parent, offset):
        spec = tuple(parent.data(0, LAZY_ROLE))
        if spec[0] == "unmapped":
            keys = [i for i in self.catalog.unmapped if self._tree_index.unmapped_matches(i)]
        else:
            keys = self._tree_index.keys_for(spec)
        with QtCore.QSignalBlocker(self.tree):
            for i in range(parent.childCount() - 1, -1, -1):
                self._delete_child(parent, i)
            if offset:
                item = QtWidgets.QTreeWidgetItem(["Previous page…", ""])
                item.setData(0, LAZY_ROLE, ("page", "", ""))
                item.setData(0, PAGE_ROLE, max(0, offset - PAGE_SIZE))
                item.setChildIndicatorPolicy(QtWidgets.QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)
                parent.addChild(item)
                self._item_count += 1
            for key in keys[offset:offset + PAGE_SIZE]:
                if spec[0] == "unmapped":
                    entry = self.npk_file.entries[key]
                    detail = hex(entry.file_signature)
                    if hasattr(self.npk_file, "source_label"):
                        detail += " · " + self.npk_file.source_label(key)
                    item = QtWidgets.QTreeWidgetItem([entry.filename, detail])
                    item.setData(0, ENTRY_ROLE, key)
                    item.setData(0, KIND_ROLE, "Mesh" if entry.extension == "mesh" else "Texture")
                    parent.addChild(item)
                    self._item_count += 1
                else:
                    self._asset_item(parent, self.catalog.assets[key], key=key)
            following = offset + PAGE_SIZE
            if following < len(keys):
                item = QtWidgets.QTreeWidgetItem([f"Load next {min(PAGE_SIZE, len(keys) - following)}…", ""])
                item.setData(0, LAZY_ROLE, ("page", "", ""))
                item.setData(0, PAGE_ROLE, following)
                item.setChildIndicatorPolicy(QtWidgets.QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)
                parent.addChild(item)
                self._item_count += 1

    def set_filters(self, text="", include_binary=True, include_text=True, include_slot=True):
        self.search_text = text.lower()
        self.include_binary, self.include_text, self.include_slot = include_binary, include_text, include_slot
        self.apply_filter()

    def apply_filter(self, *_):
        if self._render_iterator is not None:
            self._filters_dirty = True
            return
        for _ in self._filter_steps():
            pass

    def _filter_steps(self):
        if self._tree_index is None:
            return
        yield from self._tree_index.filter_steps(self.search_text, self.asset_filter.currentData(),
                                                 self.show_missing.isChecked(), self.include_binary, self.include_text, self.include_slot)
        with QtCore.QSignalBlocker(self.tree):
            for i in range(self.tree.topLevelItemCount()):
                self._release_rows(self.tree.topLevelItem(i))
            for directory, item in self._directory_items.items():
                item.setHidden(directory not in self._tree_index.visible_directories)
            for i in range(self.tree.topLevelItemCount()):
                item = self.tree.topLevelItem(i)
                if tuple(item.data(0, LAZY_ROLE) or ()) == ("unmapped", "", ""):
                    item.setHidden(not any(self._tree_index.unmapped_matches(index) for index in self.catalog.unmapped))
        meshes = {a.entry_index for a in self.catalog.assets.values() if a.kind == "Mesh" and a.entry_index is not None}
        textures = {a.entry_index for a in self.catalog.assets.values() if a.kind == "Texture" and a.entry_index is not None}
        self.status_label.setText(f"{len(meshes)} meshes · {len(textures)} textures identified\n{len(self._tree_index.visible_entries)} available files in filter · rows load on expansion")
        if hasattr(self.npk_file, "archives"):
            self.status_label.setText(self.status_label.text() + f"\n{len(self.npk_file.primary_sources)} same-name packs · {len(self.npk_file.archives) - len(self.npk_file.primary_sources)} explicitly referenced packs")
            self.status_label.setToolTip("\n".join(self.npk_file.warnings))
        if not self.scan_busy:
            self._populate_expanded()

    def _selection_changed(self, item, _previous):
        if item is None:
            return
        if tuple(item.data(0, LAZY_ROLE) or ()) == ("page", "", ""):
            self._populate_node(item)
            return
        index, path = item.data(0, ENTRY_ROLE), item.data(0, PATH_ROLE)
        if index is not None:
            self.preview_entry_requested.emit(index)
        elif path:
            self.unresolved_requested.emit(path)

    def _double_clicked(self, item, _column):
        if item.data(0, KIND_ROLE) == "Mesh":
            item.setExpanded(True)
            for i in range(item.childCount()):
                if item.child(i).text(0) == "Textures":
                    item.child(i).setExpanded(True)

    def entry_items(self, items=None, visible_only=False):
        result = {}
        if self._tree_index is None:
            return result
        if items is None:
            self._tree_index.folder_entries(result, visible_only=visible_only)
            return result
        for item in items:
            folder = item.data(0, FOLDER_PATH_ROLE)
            key = item.data(0, ASSET_KEY_ROLE)
            spec = tuple(item.data(0, LAZY_ROLE) or ())
            if folder:
                self._tree_index.folder_entries(result, folder, visible_only)
            elif key is not None:
                self._tree_index.add_asset_entries(result, key, visible_only)
            elif spec and spec[0] in {"links", "group"}:
                for key in self._tree_index.keys_for(spec, filtered=visible_only):
                    mesh_path = self.catalog.assets[spec[1]].path if spec[0] == "links" else ""
                    self._tree_index.add_asset_entries(result, key, visible_only, mesh_path)
            elif spec and spec[0] == "unmapped":
                for index in self.catalog.unmapped:
                    if not visible_only or self._tree_index.unmapped_matches(index):
                        result.setdefault(index, None)
            else:
                index = item.data(0, ENTRY_ROLE)
                if index is not None:
                    result.setdefault(index, item.data(0, PATH_ROLE))
        return result

    def _context_menu(self, position):
        clicked = self.tree.itemAt(position)
        if clicked is not None and not clicked.isSelected():
            self.tree.clearSelection()
            self.tree.setCurrentItem(clicked)
            clicked.setSelected(True)
        items = self.tree.selectedItems()
        if items:
            menu = self._create_context_menu(items)
            menu.exec(self.tree.viewport().mapToGlobal(position))

    def _create_context_menu(self, items):
        menu = QtWidgets.QMenu(self)
        menu.addAction("Save selected / folder (including linked textures)…",
                       lambda: self._extract(self.entry_items(items)))
        # Viewers open single entries: only rows that are loaded archive
        # entries are offered, in their tree order, without duplicates.

        indices = list(dict.fromkeys(item.data(0, ENTRY_ROLE) for item in items
                                     if item.data(0, ENTRY_ROLE) in getattr(self.npk_file, "entries", {})))
        if indices:
            menu.addSeparator()
            for viewer in ALL_VIEWERS:
                action = menu.addAction("Open in " + viewer.name)
                action.triggered.connect(
                    lambda _checked, v=viewer: self._open_entries_with(indices, v))
        return menu

    def _open_entries_with(self, indices, viewer):
        if self.npk_file is None:
            return
        batch_index = 0
        for index in indices:
            if index not in self.npk_file.entries:
                continue
            entry = self.npk_file.find_entry_by_id(index)
            self.open_entry_with.emit(index, entry, viewer, batch_index)
            batch_index += 1

    def extract_selected(self):
        self._extract(self.entry_items(self.tree.selectedItems()))

    def extract_visible(self):
        self._extract(self.entry_items(visible_only=True))

    def _extract(self, entries):
        if not entries:
            QtWidgets.QMessageBox.information(self, "No files selected", "Choose a mapped asset or folder first.")
            return
        destination = QtWidgets.QFileDialog.getExistingDirectory(self, "Save character assets")
        if not destination:
            return
        root, outputs = Path(destination).resolve(), []
        for index, path in entries.items():
            entry = self.npk_file.entries[index]
            if hasattr(self.npk_file, "export_path"):
                path = self.npk_file.export_path(index, path)
            parts = str(path or entry.filename).replace("/", "\\").split("\\")
            if any(part in {"", ".", ".."} or ":" in part for part in parts):
                continue
            target = root.joinpath(*parts)
            if entry.extension:
                target = target.with_suffix("." + entry.extension)
            target = target.resolve()
            if target.is_relative_to(root):
                outputs.append((target, index))
        if any(target.exists() for target, _ in outputs) and \
                QtWidgets.QMessageBox.question(self, "Replace existing files?", "Some files already exist. Replace them?") != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        failures, saved = [], 0
        for target, index in outputs:
            try:
                entry = self.npk_file.find_entry_by_id(index)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(entry.get_export_data(decoded=True))
                saved += 1
            except OSError as exc:
                failures.append(str(exc))
        QtWidgets.QMessageBox.information(self, "Character extraction", f"Saved {saved} files.\n" + ("\n".join(failures[:3]) if failures else str(root)))

    def export_report(self):
        if self.catalog is None:
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Character asset report", "character-assets.json", "JSON (*.json)")
        if path:
            try:
                Path(path).write_text(json.dumps(self.catalog.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
            except OSError as exc:
                QtWidgets.QMessageBox.critical(self, "Report failed", str(exc))
