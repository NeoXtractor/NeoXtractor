"""Preview dispatcher: produces a ViewSource per request and routes it.

The widget owns no viewer instances -- instances come from the viewers
registry (built on first use, cached there).  Each preview request
produces a *fresh* :class:`ViewSource`, so two viewers never share one
stream and displacement always releases the right bytes.  Selection: the
display name's extension when it names a specialist; otherwise the
content decides the text/binary fallback.
"""

from __future__ import annotations

import os
from typing import cast

from PySide6 import QtCore, QtWidgets

from neoxtractor.core.packages.base import Archive
from neoxtractor.core.packages.errors import PackageError
from neoxtractor.core.processors import ProcessorError
from neoxtractor.core.utils.logger import get_logger
from neoxtractor.gui.viewers import registry
from neoxtractor.gui.viewers.base import ViewerBase, ViewSource, make_entry_source

SELECT_ENTRY_TEXT = "Select an entry to preview."
WAITING_TEXT = "Waiting for content detection..."


def _extension_of(name: str) -> str:
    return os.path.splitext(name)[1].lstrip(".").lower()


class PreviewWidget(QtWidgets.QWidget):
    """A widget that previews the selected archive entry."""

    def __init__(self, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)

        self._archive: Archive | None = None
        self._key: int | None = None
        self._name: str = ""
        self._extension: str = ""
        self._current: ViewerBase | None = None

        self.message_label = QtWidgets.QLabel(SELECT_ENTRY_TEXT)
        self.message_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)

        self.widget_layout = QtWidgets.QVBoxLayout(self)

        self.control_bar_layout = QtWidgets.QHBoxLayout()

        self.status_label = QtWidgets.QLabel()
        self.control_bar_layout.addWidget(self.status_label)
        self.control_bar_layout.addStretch()

        self.previewer_selector = QtWidgets.QComboBox(self)
        for info in registry.list_viewers():
            self.previewer_selector.addItem(info.display_name, info.name)

        def on_previewer_selected(index: int) -> None:
            del index
            name = self.previewer_selector.currentData()
            if name is not None:
                self._show_in(name)

        self.previewer_selector.currentIndexChanged.connect(on_previewer_selected)
        self.control_bar_layout.addWidget(self.previewer_selector)

        self.widget_layout.addLayout(self.control_bar_layout)

        self._viewer_area = QtWidgets.QWidget(self)
        self._viewer_layout = QtWidgets.QVBoxLayout(self._viewer_area)
        self._viewer_layout.setContentsMargins(0, 0, 0, 0)
        self._viewer_layout.addWidget(self.message_label)
        self.widget_layout.addWidget(self._viewer_area, stretch=1)

        self.set_control_bar_visible(False)

    # --- public API ---------------------------------------------------------

    def set_entry(
        self, archive: Archive, key: int, name: str, extension: str = ""
    ) -> None:
        """Preview one archive entry: pick the best viewer and load it.

        ``extension`` is the caller's usable extension (name hint or probe
        result); nameless entries carry a hex name no extension can be
        derived from, so the probe answer must arrive through this argument.
        """
        self.clear()
        self._archive = archive
        self._key = key
        self._name = name
        self._extension = extension

        info = registry.find_specialist(extension or _extension_of(name))
        if info is not None:
            self._show_in(info.name)
            return

        # No usable extension hint: let the content choose the fallback.
        try:
            source = make_entry_source(archive, key, name)
        except (ValueError, EOFError, PackageError) as exc:
            self._show_message(cast(str, exc.args[0] or str(exc)))
            return
        chosen = registry.find_viewer_class(
            source.extension(), is_text=source.content_is_text
        )
        if chosen is None:
            self._show_message("No viewer available for this file type")
            return
        if chosen.input == source.input:
            self._display(source, chosen)
        else:
            self._show_in(chosen.name)

    def set_waiting(self, name: str) -> None:
        """Placeholder for a not-yet-detected entry (probe completion
        calls :meth:`recheck` and the preview fills itself in)."""
        self.clear()
        self._name = name
        self.message_label.setText(WAITING_TEXT)
        self.message_label.setVisible(True)

    def recheck(self) -> None:
        """Re-run selection for the current entry."""
        if self._archive is not None and self._key is not None:
            self.set_entry(self._archive, self._key, self._name, self._extension)

    def clear(self) -> None:
        """Release every built viewer and reset the pane."""
        for name in registry.viewer_names():
            instance = registry.peek_viewer(name)
            if instance is not None:
                instance.unload()
                instance.setVisible(False)
        self._current = None
        self._archive = None
        self._key = None
        self.set_control_bar_visible(False)
        self.message_label.setText(SELECT_ENTRY_TEXT)
        self.message_label.setVisible(True)

    def set_control_bar_visible(self, visible: bool) -> None:
        self.status_label.setVisible(visible)
        self.previewer_selector.setVisible(visible)

    # --- internals -----------------------------------------------------------

    def _show_in(self, viewer_name: str) -> None:
        """Load the current entry into one named viewer, showing it."""
        if self._archive is None or self._key is None:
            return
        info = registry.info_for(viewer_name)
        if info is None:
            return

        self.message_label.setVisible(False)
        if self._current is not None:
            self._current.unload()
            self._current.setVisible(False)

        try:
            source = make_entry_source(
                self._archive, self._key, self._name, input=info.input
            )
        except (ValueError, EOFError, PackageError) as exc:
            self._show_message(cast(str, exc.args[0] or str(exc)))
            return

        self._display(source, info)

    def _display(self, source: ViewSource, info: registry.ViewerInfo) -> None:
        viewer = registry.get_viewer(info.name)
        if viewer is None:
            self._show_message("No viewer available for this file type")
            return

        # Embed and show before loading: a parentless cached instance would
        # float as a top-level window, and viewers that refresh sub-panels
        # only while visible populate nothing otherwise.  The message and a
        # viewer are never both shown.
        if self._viewer_layout.indexOf(viewer) == -1:
            self._viewer_layout.addWidget(viewer)
        self.message_label.setVisible(False)
        viewer.setVisible(True)

        try:
            viewer.load(source)
        except (ValueError, ProcessorError, PackageError) as exc:
            # The viewer opened the bytes but declined to render them: keep
            # the control bar so another viewer or the fallback can be picked.
            viewer.unload()
            viewer.setVisible(False)
            self._current = None
            self.status_label.setText(self._name)
            self._show_message(
                str(exc.args[0]) if exc.args else str(exc), keep_controls=True
            )
            # Point the dropdown at the failed viewer: message and selection
            # must agree on whose attempt failed.
            self._sync_selector(info.name)
            return
        except Exception as exc:  # a viewer bug must not crash the event loop
            get_logger().warning("viewer %s raised on load: %s", info.name, exc)
            viewer.unload()
            viewer.setVisible(False)
            self._current = None
            self.status_label.setText(self._name)
            self._show_message(
                f"Viewer failed: {type(exc).__name__}: {exc}", keep_controls=True
            )
            self._sync_selector(info.name)
            return

        self._current = viewer
        self.status_label.setText(self._name)
        self.set_control_bar_visible(True)
        self._sync_selector(info.name)

    def _sync_selector(self, viewer_name: str) -> None:
        """Point the dropdown at *viewer_name* without re-triggering it.

        ``currentIndexChanged`` re-displays through the chosen viewer, so the
        index change must block signals to avoid echoing back into
        :meth:`_show_in`.  An unknown name leaves the dropdown alone.
        """
        index = self.previewer_selector.findData(viewer_name)
        if index != -1 and self.previewer_selector.currentIndex() != index:
            self.previewer_selector.blockSignals(True)
            self.previewer_selector.setCurrentIndex(index)
            self.previewer_selector.blockSignals(False)

    def _show_message(self, text: str, *, keep_controls: bool = False) -> None:
        self.message_label.setText(text)
        self.message_label.setVisible(True)
        self.set_control_bar_visible(keep_controls)
