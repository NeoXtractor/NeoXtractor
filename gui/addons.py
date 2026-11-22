"""
The HPMA add-ons: the pages, menus and speed-ups this build adds to NeoXtractor, attached from outside so that
NeoXtractor's own files stay as they are. install_speedups() runs before any pack is read, install() once the main
window exists (see gui.run).
"""

from PySide6 import QtWidgets

from core.logger import get_logger

# Category-list entries that show their own page in the left column instead of the file list
PAGES: set[str] = set()


def install_speedups() -> None:
    """Faster helpers with the same results, and the sound-file detection: before any pack is read."""
    from core import fast_bxml, fast_logger, fast_paths
    from core.npk import metadata_cache, wwise_audio

    for name, install in (
        ("the fast get_logger", fast_logger.install),  # no inspect.stack(): reading packs gets much faster
        ("the fast binary XML decoding", fast_bxml.install),  # metadata scans and .gim reads
        ("the fast path helpers", fast_paths.install),  # the Characters scan
        ("the metadata cache", metadata_cache.install),  # remembers named metadata of unchanged packs
        ("the Wwise sound detection", wwise_audio.install_detection),  # .wem / .ogg / .wav join the sound banks
    ):
        try:
            install()
        except Exception:
            get_logger().exception("Could not install %s", name)


def install(app: QtWidgets.QApplication) -> None:
    """The pages and menus, in the order they appear in the category list."""
    from core.npk import layered_archives, wwise_audio, wwise_names
    from gui.widgets import (
        animations_page,
        characters_items,
        characters_page,
        characters_sort,
        scene_viewer,
        stages_page,
    )

    windows = [window for window in app.topLevelWidgets() if hasattr(window, "entry_category_filter_combobox")]

    for name, step in (
        ("the Characters category", lambda: characters_page.install(app)),
        ("the Stages category", lambda: stages_page.install(app)),
        ("the Scene Viewer", lambda: scene_viewer.install(app)),
        ("the Animations category", lambda: [animations_page.install(window) for window in windows]),
        ("the combined game installs", layered_archives.install),  # a pack joins its Documents patch copy
        ("sorting in the Characters tab", lambda: characters_sort.install(app)),
        ("item names in the Characters tab", lambda: characters_items.install(app)),
        ("the Audio category", lambda: wwise_audio.install_category(app)),
        ("the sound bank labels", lambda: wwise_names.install(app)),  # numbered .wem sounds show their bank
    ):
        try:
            step()
        except Exception:
            get_logger().exception("Could not add %s", name)


def left_stack(window) -> QtWidgets.QStackedWidget:
    """
    The stack in the main window's left column: the file list, and the pages the add-ons put beside it. Made once;
    picking a category that is not an add-on page shows the file list again.
    """
    stack = getattr(window, "left_content", None)
    if stack is not None:
        return stack
    stack = QtWidgets.QStackedWidget(window)
    layout = window.control_layout
    layout.insertWidget(layout.indexOf(window.list_widget), stack, 1)
    stack.addWidget(window.list_widget)
    window.left_content = stack
    combo = window.entry_category_filter_combobox

    def show_file_list(_index):
        if combo.currentData() in PAGES:
            return
        stack.setCurrentWidget(window.list_widget)
        # The pages hide the extract buttons; they act on the file list, once a pack is loaded
        window.extract_button_widget.setVisible(window.unload_npk_action.isEnabled())

    combo.currentIndexChanged.connect(show_file_list)
    # While a page is shown the file list is hidden: filtering it would only read every entry for nothing
    apply_filter = window.filter.apply_filter
    window.filter.apply_filter = lambda *args, **kwargs: (
        None if combo.currentData() in PAGES else apply_filter(*args, **kwargs)
    )
    return stack


def menu(window, title: str) -> QtWidgets.QMenu | None:
    """The main window's menu with this title ("File", "Tools"), or None."""
    for action in window.menuBar().actions():
        if action.menu() is not None and action.text().replace("&", "") == title:
            return action.menu()
    return None
