"""A custom model for displaying NPK files in a QListView."""

from typing import Any, cast

from PySide6 import QtCore, QtGui, QtWidgets

from core.config import Config
from core.npk.class_types import NPKEntryDataFlags, State
from core.npk.npk_file import NPKFile
from core.utils import get_filename_in_config
from gui.theme import ThemeManager


class NPKFileModel(QtCore.QAbstractListModel):
    """
    Custom model for displaying NPK files in a QListView.
    """

    _file_names_cache: dict[int, str] = {}

    def __init__(self, npk_file: NPKFile, parent: QtCore.QObject | None = None):
        super().__init__(parent)

        if isinstance(parent, QtWidgets.QWidget):
            self._loading_icon = parent.style().standardIcon(
                QtWidgets.QStyle.StandardPixmap.SP_BrowserReload
            )
            self._encrypted_icon = parent.style().standardIcon(
                QtWidgets.QStyle.StandardPixmap.SP_MessageBoxWarning
            )
            self._errored_icon = parent.style().standardIcon(
                QtWidgets.QStyle.StandardPixmap.SP_MessageBoxCritical
            )
            self._file_icon = parent.style().standardIcon(
                QtWidgets.QStyle.StandardPixmap.SP_FileIcon
            )

        self._npk_file = npk_file
        app = cast(QtCore.QCoreApplication, QtWidgets.QApplication.instance())
        self._game_config: Config = app.property("game_config")
        self._theme_config: ThemeManager = ThemeManager.instance()

    def rowCount(
        self,
        parent: QtCore.QModelIndex
        | QtCore.QPersistentModelIndex = QtCore.QModelIndex(),
    ) -> int:
        return self._npk_file.file_count

    def data(
        self,
        index: QtCore.QModelIndex | QtCore.QPersistentModelIndex,
        role: int = QtCore.Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if not index.isValid():
            return None
        if role == QtCore.Qt.ItemDataRole.DisplayRole:
            filename = self.get_filename(index)
            if not self._npk_file.is_entry_loaded(index.row()):
                return filename

            # Entry is loaded at this point, get it from cache.
            entry = self._npk_file.find_entry_by_id(index.row())

            if entry.data_flags & NPKEntryDataFlags.ERROR:
                return f"{filename} (Error)"
            if entry.data_flags & NPKEntryDataFlags.ENCRYPTED:
                return f"{filename} (Encrypted)"

            return filename
        if role == QtCore.Qt.ItemDataRole.DecorationRole:
            if not self._npk_file.is_entry_loaded(index.row()):
                return self._loading_icon

            # Entry is loaded at this point, get it from cache.
            entry = self._npk_file.find_entry_by_id(index.row())

            if entry.data_flags & NPKEntryDataFlags.ERROR:
                return self._errored_icon
            if entry.data_flags & NPKEntryDataFlags.ENCRYPTED:
                return self._encrypted_icon

            return self._file_icon
        if role == QtCore.Qt.ItemDataRole.ForegroundRole:
            if self._npk_file.is_entry_loaded(index.row()):
                entry = self._npk_file.find_entry_by_id(index.row())
                if entry.data_flags & NPKEntryDataFlags.ERROR:
                    return QtGui.QBrush(QtGui.QColorConstants.Red)
        if role == QtCore.Qt.ItemDataRole.BackgroundRole:
            if self._npk_file.is_entry_loaded(index.row()):
                entry = self._npk_file.find_entry_by_id(index.row())
                if entry.state == State.UNLOADED:
                    return QtGui.QBrush(QtGui.QColorConstants.Red)
                elif entry.state == State.CACHED:
                    return QtGui.QBrush(
                        self._theme_config.get_color("palette.background")
                    )
                elif entry.state == State.PRIMARY_LOAD:
                    return QtGui.QBrush(QtGui.QColorConstants.Green)
                elif entry.state == State.SECONDARY_LOAD:
                    return QtGui.QBrush(QtGui.QColorConstants.DarkYellow)
        if role == QtCore.Qt.ItemDataRole.UserRole:
            return self._npk_file.indices[index.row()]
        return None

    def get_filename(
        self,
        index: QtCore.QModelIndex | QtCore.QPersistentModelIndex,
        invalidate_cache=False,
    ) -> str:
        """Get the filename for a given index."""
        if not index.isValid():
            return ""

        if index.row() in self._file_names_cache and not invalidate_cache:
            return self._file_names_cache[index.row()]

        filename = get_filename_in_config(
            self._game_config, index.row(), self._npk_file
        )

        wwise_index = self._wwise_index()
        if wwise_index is not None:
            md5 = getattr(self._npk_file.indices[index.row()], "raw_hash_hex", None)
            label = wwise_index.label_for_md5(md5) if md5 else None
            if label:
                filename = label

        self._file_names_cache[index.row()] = filename
        return filename

    @staticmethod
    def _wwise_index():
        """Active Wwise event mapping, if the user built one."""
        app = cast(QtCore.QCoreApplication, QtWidgets.QApplication.instance())
        return app.property("wwise_event_index")
