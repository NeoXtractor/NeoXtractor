"""ID5 path mapping dialog: the two `.thy` name tables of a package.

Non-Wwise packages use them to restore the original vfs path of every IDX
entry. `wwise.idx` uses the same tables to resolve Wwise event / media names
and mounts the banks of the package; that part is disabled for other packages.
"""

from __future__ import annotations

import os

from PySide6 import QtWidgets

from core.logger import get_logger
from core.utils import get_application_path
from id5mapping.wwise import (
    WwiseEventIndex,
    build_hash_index,
    bundled_index_path,
    merge_index,
    mount_archive_banks,
    save_index,
)
from id5mapping.wwise.event_index import _thy_pair_from_thy
from id5mapping.wwise.thy import guess_thy_path

THY_FILTER = "Name tables (*.thy);;All Files (*)"


def is_wwise_archive(idx_path: str | None) -> bool:
    """Return True for `wwise.idx` — the only package using Wwise mapping."""
    if not idx_path:
        return False
    return os.path.splitext(os.path.basename(idx_path))[0].lower() == "wwise"


class Id5PathMappingWindow(QtWidgets.QDialog):
    """Pick the base / hot-update name tables and apply the ID5 mapping."""

    def __init__(
        self,
        parent: QtWidgets.QWidget | None = None,
        idx_path: str | None = None,
        archive_file=None,
    ):
        super().__init__(parent)

        self.setWindowTitle("ID5 Path Mapping")
        self.resize(760, 300)

        self.index: WwiseEventIndex | None = None
        self.restored = 0
        self.idx_path = idx_path
        self.archive_file = archive_file
        self.is_wwise = is_wwise_archive(idx_path)

        layout = QtWidgets.QVBoxLayout(self)
        form = QtWidgets.QFormLayout()

        self.scope = QtWidgets.QLabel()
        self.scope.setWordWrap(True)
        if self.is_wwise:
            self.scope.setText(
                "Package: wwise.idx — event / media naming from the two name tables."
            )
        else:
            self.scope.setText(
                "Package: %s — original vfs paths restored from the two name tables."
                % (os.path.basename(idx_path) if idx_path else "none")
            )
        layout.addWidget(self.scope)

        self.index_path = QtWidgets.QLineEdit(bundled_index_path() or "")
        self.index_path.setPlaceholderText(
            "Bundled hash index (id5mapping/wwise/wwise_event_hash_index.bin)"
        )
        self.index_row = self._with_browse(
            self.index_path,
            "Open event hash index",
            "Index (*.bin *.json *.gz);;All Files (*)",
        )
        self.index_label = QtWidgets.QLabel("Event hash index")
        form.addRow(self.index_label, self.index_row)

        self.thy_base = QtWidgets.QLineEdit()
        self.thy_base.setPlaceholderText(
            "base name table, optional (e.g. <root>/thd/wwise.thy)"
        )
        form.addRow(
            "Name table (base)", self._with_browse(self.thy_base, "Open .thy", THY_FILTER)
        )

        self.thy_hot = QtWidgets.QLineEdit()
        self.thy_hot.setPlaceholderText(
            "hot-update name table, optional (e.g. <root>/Documents/thd/wwise.thy)"
        )
        form.addRow(
            "Name table (hot update)",
            self._with_browse(self.thy_hot, "Open .thy", THY_FILTER),
        )

        guessed = guess_thy_path(idx_path or "")
        if guessed:
            pair = _thy_pair_from_thy(guessed)
            self.thy_base.setText(pair[0] if pair else guessed)
            if len(pair) > 1:
                self.thy_hot.setText(pair[-1])

        layout.addLayout(form)

        self.status = QtWidgets.QLabel("No mapping applied.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        reload_button = buttons.addButton(
            "Reload", QtWidgets.QDialogButtonBox.ButtonRole.ActionRole
        )
        reload_button.clicked.connect(self.reload_mapping)
        self.rebuild_button = buttons.addButton(
            "Rebuild index...", QtWidgets.QDialogButtonBox.ButtonRole.ActionRole
        )
        self.rebuild_button.clicked.connect(self.rebuild_index)
        buttons.accepted.connect(self.apply)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        # Wwise mapping only applies to wwise.idx.
        for widget in (self.index_label, self.index_row, self.rebuild_button):
            widget.setVisible(self.is_wwise)

        if self.is_wwise and bundled_index_path():
            self.reload_mapping()

    def _with_browse(
        self, line_edit: QtWidgets.QLineEdit, title: str, file_filter: str
    ) -> QtWidgets.QWidget:
        container = QtWidgets.QWidget()
        row = QtWidgets.QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(line_edit)

        button = QtWidgets.QPushButton("Browse...")

        def browse():
            path, _ = QtWidgets.QFileDialog.getOpenFileName(
                self, title, "", file_filter
            )
            if path:
                line_edit.setText(path)

        button.clicked.connect(browse)
        row.addWidget(button)
        return container

    def _selected_thy_paths(self) -> list[str]:
        return [
            path
            for path in (self.thy_base.text().strip(), self.thy_hot.text().strip())
            if path
        ]

    def reload_mapping(self) -> bool:
        """Apply the name tables to the opened package."""
        if self.is_wwise:
            return self.build_wwise_mapping()
        return self.apply_path_restore()

    def apply_path_restore(self) -> bool:
        """Name the IDX entries from the selected name tables."""
        if self.archive_file is None:
            self._error("Open an IDX package first.")
            return False
        try:
            thy_paths = self._selected_thy_paths() or None
            self.restored = self.archive_file.apply_path_mapping(thy_paths)
        except Exception as error:  # noqa: BLE001
            get_logger().exception("ID5 path restore failed")
            self._error(f"Failed to restore paths: {error}")
            return False

        self.status.setText(
            f"Restored {self.restored}/{self.archive_file.file_count} entry paths from "
            f"{len(thy_paths or [])} name table(s)."
        )
        return True

    def build_wwise_mapping(self) -> bool:
        """Load the hash index, mount the package banks and report coverage."""
        index = WwiseEventIndex()
        try:
            path = self.index_path.text().strip()
            if path:
                index.load_hash_index(path)
            elif not index.load_bundled_index():
                self._error(
                    "Bundled event hash index not found. Select an index file or use Rebuild index."
                )
                return False

            base_thy = self.thy_base.text().strip()
            hot_thy = self.thy_hot.text().strip()
            if base_thy or hot_thy:
                index.load_thy_files([p for p in (base_thy, hot_thy) if p])
            else:
                index.autodetect_thy(self.idx_path)

            mounted = 0
            if self.archive_file is not None:
                mounted = mount_archive_banks(self.archive_file, index)
        except Exception as error:  # noqa: BLE001
            get_logger().exception("Wwise mapping failed")
            self._error(f"Failed to build mapping: {error}")
            return False

        self.index = index
        self.status.setText(
            f"Event name hashes {len(index.name_by_hash)}; events mounted from this package {mounted}, "
            f"media short IDs {len(index.short_id_events)}, "
            f"package entries mapped {len(index.md5_events)}, "
            f"name table keys {index.thy_key_count}."
            + ("" if index.thy_path else " Warning: wwise.thy not found, external wem cannot be resolved.")
        )
        return True

    def rebuild_index(self) -> None:
        """Recompute the event hash index from an event name list."""
        events_path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Select the event name list", "", "Event List (*.py *.json);;All Files (*)"
        )
        if not events_path:
            return

        default_out = os.path.join(
            get_application_path(), "id5mapping", "wwise", "wwise_event_hash_index.bin"
        )
        out_path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save event hash index", default_out, "Index (*.bin *.json *.gz);;All Files (*)"
        )
        if not out_path:
            return

        try:
            index = build_hash_index(events_path)
            index = merge_index(index, out_path)
            save_index(index, out_path)
        except Exception as error:  # noqa: BLE001
            get_logger().exception("Wwise hash index rebuild failed")
            self._error(f"Failed to rebuild index: {error}")
            return

        self.index_path.setText(out_path)
        self.status.setText(
            f"Hash index written to {out_path}: {index['stats']['events']} events."
        )
        self.build_wwise_mapping()

    def apply(self) -> None:
        if not self.reload_mapping():
            return
        self.accept()

    def _error(self, message: str) -> None:
        QtWidgets.QMessageBox.critical(self, "ID5 Path Mapping", message)
        self.status.setText(message)
