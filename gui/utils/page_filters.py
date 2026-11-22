"""The main window's search box and file-type boxes, as each page of the left-hand list uses them."""

FILE_SEARCH = "Search by filename..."
# Pages that list game folders search them with the same box; the file-type boxes only filter the file list
PAGE_SEARCH = {
    "animations": "Search animations or models (e.g. dance, run, hippogriff, idle)...",
    "stages": "Search stages or scenes...",
}
FILE_TYPE_BOXES = ("filter_binary_filter", "filter_text_filter", "filter_slot_filter")


def update_page_filters(window) -> None:
    """Shows the file-type boxes and the search hint that suit the page picked in the category list."""
    page = window.entry_category_filter_combobox.currentData()
    own = isinstance(page, str) and page in PAGE_SEARCH
    for name in FILE_TYPE_BOXES:
        box = getattr(window, name, None)
        if box is not None:
            box.setVisible(not own)
    search = getattr(window, "name_filter_input", None)
    if search is not None:
        search.setPlaceholderText(PAGE_SEARCH[page] if own else FILE_SEARCH)


def watch_page_filters(window) -> None:
    """Keeps the boxes in step with the category list (once per window, whichever page installs first)."""
    if getattr(window, "_page_filters_watched", False):
        return
    window._page_filters_watched = True
    window.entry_category_filter_combobox.currentIndexChanged.connect(lambda _index: update_page_filters(window))
