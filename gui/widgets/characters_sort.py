"""
Sorting for the Characters tab: a "Sort" box (name or size, either direction) and a size column.

The tab's own code (gui.windows.character_folders_window) builds every folder item up front
and fills the files of a folder lazily, a page at a time, from CharacterTreeIndex.keys_for.
So folders are sorted by reordering their items, and files by ordering the keys keys_for
returns; open file groups are re-filled so their pages follow the new order.
"""

from __future__ import annotations

from PySide6 import QtCore, QtWidgets
from core.logger import get_logger

from gui.utils.character_tree_index import CharacterTreeIndex
from gui.windows import character_folders_window as cfw

SETTING_SORT = "characters.sort"
MODES = [
    ("Name (A → Z)", "name"),
    ("Name (Z → A)", "name_desc"),
    ("Size (largest first)", "size_desc"),
    ("Size (smallest first)", "size"),
]
SIZE_COLUMN = 2
SIZE_ROLE = QtCore.Qt.ItemDataRole.UserRole + 40

_state = {"mode": "name", "page": None, "sizes": {}, "archive": None}


def size_text(size: int) -> str:
    if size >= 1024**3:
        return f"{size / 1024 ** 3:.2f} GB"
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.0f} KB"
    return f"{size} B" if size else ""


def entry_size(archive, index) -> int:
    """Unpacked size of an archive entry from its index record (nothing is read from disk)."""
    if archive is None or index is None:
        return 0
    if _state["archive"] is not archive:
        _state["archive"], _state["sizes"] = archive, {}
    sizes = _state["sizes"]
    if index not in sizes:
        record = None
        try:
            if hasattr(archive, "peek_entry"):
                record = archive.peek_entry(index)
            if record is None:
                indices = getattr(archive, "indices", None)
                record = indices[index] if indices is not None and 0 <= index < len(indices) else None
        except Exception:
            record = None
        sizes[index] = int(getattr(record, "file_original_length", 0) or getattr(record, "file_length", 0) or 0)
    return sizes[index]


def _asset_key(page, mode):
    """Sort key for catalog asset keys."""
    assets, archive = page.catalog.assets, page.npk_file

    def name(key):
        asset = assets.get(key)
        return str(getattr(asset, "path", key)).rsplit("\\", 1)[-1].lower()

    def size(key):
        asset = assets.get(key)
        return entry_size(archive, getattr(asset, "entry_index", None))

    return name if mode.startswith("name") else (lambda key: (size(key), name(key)))


def _sorted_keys(keys):
    page, mode = _state["page"], _state["mode"]
    if page is None or getattr(page, "catalog", None) is None or not keys:
        return keys
    try:
        return sorted(keys, key=_asset_key(page, mode), reverse=mode.endswith("_desc"))
    except Exception:
        return keys


def _patch_tree_index() -> None:
    if getattr(CharacterTreeIndex, "_sort_patched", False):
        return
    original = CharacterTreeIndex.keys_for

    def keys_for(self, *args, **kwargs):
        return _sorted_keys(list(original(self, *args, **kwargs)))

    CharacterTreeIndex.keys_for = keys_for
    CharacterTreeIndex._sort_patched = True


def _folder_sizes(page) -> dict[str, int]:
    """Total size of the files in each directory shown in the tree, sub-folders included."""
    totals = dict.fromkeys(page._directory_items, 0)
    assets, archive = page.catalog.assets, page.npk_file
    for folder, keys in page.catalog.folders.items():
        size = sum(entry_size(archive, getattr(assets.get(key), "entry_index", None)) for key in keys)
        parts = folder.split("\\")
        for depth in range(2, len(parts) + 1):
            directory = "\\".join(parts[:depth])
            if directory in totals:
                totals[directory] += size
    return totals


def apply_sort(page) -> None:
    """Reorder the folder items and re-fill open file groups for the current mode."""
    if getattr(page, "catalog", None) is None or page.scan_busy or not page._directory_items:
        return
    mode, tree = _state["mode"], page.tree
    sizes = _folder_sizes(page)
    for directory, item in page._directory_items.items():
        item.setData(0, SIZE_ROLE, sizes.get(directory, 0))
        item.setText(SIZE_COLUMN, size_text(sizes.get(directory, 0)))

    def key(item):
        label = item.text(0).lower()
        return label if mode.startswith("name") else (item.data(0, SIZE_ROLE) or 0, label)

    parents = [tree.invisibleRootItem()] + list(page._directory_items.values())
    tree.setUpdatesEnabled(False)
    try:
        with QtCore.QSignalBlocker(tree):  # no folder fill / release while items move
            # Moving an item makes the view forget what was open below it, so note all of it first:
            # the open folders, and the open file groups (Mesh / Textures / Metadata) inside them
            expanded = [item for item in page._directory_items.values() if item.isExpanded()]
            open_groups = [
                parent.child(i)
                for parent in parents
                for i in range(parent.childCount())
                if not parent.child(i).data(0, cfw.FOLDER_PATH_ROLE) and parent.child(i).isExpanded()
            ]
            for parent in parents:
                children = [parent.child(i) for i in range(parent.childCount())]
                folders = [c for c in children if c.data(0, cfw.FOLDER_PATH_ROLE)]
                if len(folders) < 2:
                    continue
                others = [c for c in children if not c.data(0, cfw.FOLDER_PATH_ROLE)]
                ordered = sorted(folders, key=key, reverse=mode.endswith("_desc")) + others
                if ordered != children:
                    parent.takeChildren()
                    parent.addChildren(ordered)
            for item in expanded + open_groups:
                item.setExpanded(True)
    finally:
        tree.setUpdatesEnabled(True)
    # Open file groups still list their files in the old order: close and reopen each one, which
    # makes the tab drop its rows and fill the first page again from the newly ordered keys
    for group in open_groups:
        if group.data(0, cfw.LOADED_ROLE):
            group.setExpanded(False)
            group.setExpanded(True)


def _wrap_asset_item(page) -> None:
    original = page._asset_item

    def asset_item(parent, asset, *args, **kwargs):
        item = original(parent, asset, *args, **kwargs)
        size = entry_size(page.npk_file, getattr(asset, "entry_index", None))
        item.setData(0, SIZE_ROLE, size)
        item.setText(SIZE_COLUMN, size_text(size))
        return item

    page._asset_item = asset_item


def _drop_row_filters(page, row) -> None:
    """
    Take the "All assets" type box and the "Show missing references" switch out of the Characters tab: every type
    is listed, and references the archive doesn't have never are. (The tab's own filter reads both widgets, so they
    stay as hidden, fixed settings rather than being deleted.)
    """
    for name, settle in (
        ("asset_filter", lambda w: w.setCurrentIndex(0)),
        ("show_missing", lambda w: w.setChecked(False)),
    ):
        widget = getattr(page, name, None)
        if widget is None:
            continue
        settle(widget)
        if row is not None:
            row.removeWidget(widget)
        widget.hide()


SUMMARY_MARKS = ("textures identified", "available files in filter", "explicitly referenced packs")


def _log_summary(page) -> None:
    """
    Sends the scan counts to the log instead of the label under the tree. Other messages, like scan progress, still show
    in the label.
    """
    label = getattr(page, "status_label", None)
    if label is None or getattr(label, "_summary_to_log", False):
        return
    show = label.setText

    def set_text(text, label=label, show=show):
        text = str(text)
        if any(mark in text for mark in SUMMARY_MARKS):
            get_logger().info("Characters: %s", " | ".join(line.strip() for line in text.splitlines() if line.strip()))
            show("")
            label.hide()
            return
        show(text)
        label.setVisible(bool(text))

    label.setText = set_text
    label._summary_to_log = True
    if any(mark in label.text() for mark in SUMMARY_MARKS):
        set_text(label.text())


def install(app) -> None:
    """Add the Sort box and the size column to the Characters tab of every main window."""
    _patch_tree_index()
    for window in app.topLevelWidgets():
        page = getattr(window, "_character_folders_window", None)
        if page is None or getattr(page, "_sort_installed", False) or not hasattr(page, "asset_filter"):
            continue
        page._sort_installed = True
        _state["page"] = page
        settings = app.property("settings_manager")
        try:
            saved = settings.get(SETTING_SORT, "name") if settings is not None else "name"
        except Exception:
            saved = "name"
        _state["mode"] = saved if saved in {mode for _label, mode in MODES} else "name"

        combo = QtWidgets.QComboBox()
        for label, mode in MODES:
            combo.addItem(label, mode)
        combo.setCurrentIndex(max(0, combo.findData(_state["mode"])))
        combo.setToolTip("Order of folders and of the files inside them. Sizes are unpacked file sizes.")
        row = page.layout().itemAt(0).layout() if page.layout() is not None and page.layout().count() else None
        _drop_row_filters(page, row)
        _log_summary(page)
        if row is not None:
            row.addWidget(QtWidgets.QLabel("Sort:"))
            row.addWidget(combo)
        page.sort_combo = combo

        page.tree.setColumnCount(3)
        page.tree.setColumnWidth(SIZE_COLUMN, 80)
        _wrap_asset_item(page)

        def changed(_index, page=page, combo=combo, settings=settings):
            _state["mode"] = combo.currentData()
            if settings is not None:
                try:
                    settings.set(SETTING_SORT, _state["mode"], True)
                except Exception:
                    get_logger().warning("Could not save the sort setting", exc_info=True)
            apply_sort(page)

        combo.currentIndexChanged.connect(changed)
        # The tree is rebuilt after every scan (new archive, refresh): sort it again then
        page.scan_finished.connect(lambda _outcome, page=page: QtCore.QTimer.singleShot(0, lambda: apply_sort(page)))
        apply_sort(page)
