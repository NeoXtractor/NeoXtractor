"""Read-only catalog worker; never create/access widgets from its thread."""

from time import monotonic
from PySide6 import QtCore
from core.npk.character_catalog import build_character_catalog
from core.npk.scan_control import CharacterScanCancelled


class CharacterScanSignals(QtCore.QObject):
    progress = QtCore.Signal(int, str, int, int)
    finished = QtCore.Signal(int, object, str)


class CharacterScanWorker(QtCore.QRunnable):
    def __init__(self, generation, archive, roots, names, cancel_event):
        super().__init__()
        self.signals = CharacterScanSignals()
        self.generation, self.archive = generation, archive
        self.roots, self.names, self.cancel_event = roots, names, cancel_event
        self._last_stage, self._last_update = "", 0.0

    def _progress(self, stage, current, total):
        now = monotonic()
        if stage != self._last_stage or current == total or now - self._last_update >= 0.05:
            self._last_stage, self._last_update = stage, now
            self.signals.progress.emit(self.generation, stage, current, total)

    def run(self):
        try:
            catalog = build_character_catalog(self.archive, self.roots, self.names,
                                              progress=self._progress, cancelled=self.cancel_event.is_set)
            self.signals.finished.emit(self.generation, catalog, "")
        except CharacterScanCancelled:
            self.signals.finished.emit(self.generation, None, "cancelled")
        except Exception as exc:
            self.signals.finished.emit(self.generation, None, str(exc))
