"""English item names and icons in the Characters tab, from the game's item tables.

Item folders and meshes show their item name, the search matches it, and a panel under the tree shows the
selected item's icon.
"""

from __future__ import annotations

import threading

from PySide6 import QtCore, QtGui, QtWidgets

from core.logger import get_logger
from gui.utils.character_tree_index import CharacterTreeIndex
from gui.windows import character_folders_window as cfw

SEPARATOR = "  ·  "
LABEL_ROLE = QtCore.Qt.ItemDataRole.UserRole + 41      # a folder's own label, before the name is added
ICON_SIZE = 96

_state = {"links": {}, "names": {}, "index": None, "generation": 0, "pixmaps": {}}


class _Signals(QtCore.QObject):
    loaded = QtCore.Signal(int, object, object)        # generation, links, pack index


_signals: _Signals | None = None


def _link(path):
    if not _state["links"] or not path:
        return None
    from core.npk.game_tables import link_for
    return link_for(_state["links"], str(path))


# -- search ------------------------------------------------------------------------------------------
def _patch_search() -> None:
    if getattr(CharacterTreeIndex, "_names_patched", False):
        return
    original = CharacterTreeIndex.matches

    def matches(self, asset, mesh_path=""):
        if original(self, asset, mesh_path):
            return True
        text = getattr(self, "text", "")
        if not text or not _state["names"]:
            return False
        name = _state["names"].get(str(getattr(asset, "path", "")).lower()) or _state["names"].get(str(mesh_path).lower())
        if not name or text not in name.lower():
            return False
        self.text = ""                                   # the name matched: apply the tab's other filters
        try:
            return original(self, asset, mesh_path)
        finally:
            self.text = text

    CharacterTreeIndex.matches = matches
    CharacterTreeIndex._names_patched = True


# -- tree labels -------------------------------------------------------------------------------------
def _folder_link(page, directory):
    catalog = getattr(page, "catalog", None)
    if catalog is None:
        return None
    for key in catalog.folders.get(directory, ()):
        asset = catalog.assets.get(key)
        if asset is not None and asset.kind == "Mesh":
            link = _link(asset.path)
            if link and link.get("name"):
                return link
    return None


def _label_folders(page) -> None:
    for directory, item in list(getattr(page, "_directory_items", {}).items()):
        label = item.data(0, LABEL_ROLE) or item.text(0)
        item.setData(0, LABEL_ROLE, label)
        link = _folder_link(page, directory)
        item.setText(0, f"{label}{SEPARATOR}{link['name']}" if link else label)
        if link:
            item.setToolTip(0, f"{directory}\n{link['name']}")


def _wrap_asset_item(page) -> None:
    original = page._asset_item

    def asset_item(parent, asset, *args, **kwargs):
        item = original(parent, asset, *args, **kwargs)
        if getattr(asset, "kind", "") == "Mesh":
            link = _link(asset.path)
            if link and link.get("name"):
                item.setText(0, f"{item.text(0)}{SEPARATOR}{link['name']}")
        return item

    page._asset_item = asset_item


# -- icon panel --------------------------------------------------------------------------------------
def _icon_pixmap(link):
    path = link.get("icon")
    if not path or _state["index"] is None:
        return None
    if path not in _state["pixmaps"]:
        _state["pixmaps"][path] = None
        found = _state["index"].read(path.replace("/", "\\"))
        if found is not None:
            try:
                from core.npk.stage_export import decode_texture
                image = decode_texture(found[0]).convert("RGBA")
                qimage = QtGui.QImage(image.tobytes(), image.width, image.height, 4 * image.width,
                                      QtGui.QImage.Format.Format_RGBA8888).copy()
                _state["pixmaps"][path] = QtGui.QPixmap.fromImage(qimage).scaled(
                    ICON_SIZE, ICON_SIZE, QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                    QtCore.Qt.TransformationMode.SmoothTransformation)
            except Exception:
                get_logger().warning("Could not decode the item icon %s", path, exc_info=True)
    return _state["pixmaps"][path]


def _install_panel(page):
    panel = QtWidgets.QWidget()
    row = QtWidgets.QHBoxLayout(panel)
    row.setContentsMargins(0, 4, 0, 4)
    icon = QtWidgets.QLabel()
    icon.setFixedSize(ICON_SIZE, ICON_SIZE)
    icon.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
    text = QtWidgets.QLabel()
    text.setWordWrap(True)
    text.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
    row.addWidget(icon)
    row.addWidget(text, 1)
    panel.hide()
    layout = page.layout()
    status = getattr(page, "status_label", None)
    position = layout.indexOf(status) if status is not None else -1
    layout.insertWidget(position if position >= 0 else layout.count(), panel)
    page._item_panel = (panel, icon, text)

    def show_for(item, _previous=None):
        link = None
        if item is not None:
            path = item.data(0, cfw.PATH_ROLE)
            directory = item.data(0, cfw.FOLDER_PATH_ROLE)
            if path:
                link = _link(path)
                if link is None:                        # a texture / metadata row: its folder's item
                    link = _folder_link(page, str(path).rsplit("\\", 1)[0])
            elif directory:
                link = _folder_link(page, directory)
        if not link:
            panel.hide()
            return
        pixmap = _icon_pixmap(link)
        icon.setPixmap(pixmap if pixmap is not None else QtGui.QPixmap())
        text.setText(f"<b>{link.get('name') or '(no name)'}</b><br><span style='color:gray'>item {link.get('item')}"
                     f"<br>{link.get('icon', '').rsplit('/', 1)[-1]}</span>")
        panel.show()

    page.tree.currentItemChanged.connect(show_for)


# -- loading -----------------------------------------------------------------------------------------
def _load(page) -> None:
    """Load the mesh -> item links for the open archive's game in the background."""
    archive = getattr(page, "npk_file", None)
    path = getattr(archive, "file_path", None)
    if not path or getattr(page, "catalog", None) is None:
        return
    from core.npk.stage_export import game_root_for
    game = game_root_for(path)
    if game is None:
        return
    _state["generation"] += 1
    generation = _state["generation"]

    def work():
        try:
            from core.npk import game_tables
            from core.npk.stage_export import PackIndex, read_options_for
            options = read_options_for(archive)
            index = PackIndex(game, options)
            links = game_tables.mesh_links(game, options, lambda p: (index.read(p) or (None,))[0])
            _signals.loaded.emit(generation, links, index)
        except Exception:
            get_logger().exception("Could not load item names for the Characters tab")

    threading.Thread(target=work, daemon=True).start()


def _loaded(page, generation, links, index) -> None:
    if generation != _state["generation"]:
        return
    _state["links"], _state["index"], _state["pixmaps"] = links, index, {}
    names = {}
    catalog = getattr(page, "catalog", None)
    if catalog is not None:
        for asset in catalog.assets.values():
            if getattr(asset, "kind", "") == "Mesh":
                link = _link(asset.path)
                if link and link.get("name"):
                    names[str(asset.path).lower()] = link["name"]
    _state["names"] = names
    _label_folders(page)
    # Re-run the current search so names can match it
    if getattr(page, "search_text", ""):
        page.apply_filter()


def install(app) -> None:
    global _signals
    _patch_search()
    for window in app.topLevelWidgets():
        page = getattr(window, "_character_folders_window", None)
        if page is None or getattr(page, "_items_installed", False):
            continue
        page._items_installed = True
        if _signals is None:
            _signals = _Signals()
        _signals.loaded.connect(lambda generation, links, index, page=page: _loaded(page, generation, links, index))
        _wrap_asset_item(page)
        _install_panel(page)
        page.scan_finished.connect(lambda _outcome, page=page: QtCore.QTimer.singleShot(50, lambda: _load(page)))
        if getattr(page, "catalog", None) is not None:
            _load(page)
