"""Background package open driven from the main window's status bar.

``registry.open_file`` runs on a worker thread and the worker never touches
a Qt widget directly: the driver's ``progress`` callback is bridged through
queued signals.  ``request_cancel`` sets the cancel flag and invokes the
cancel callable the driver hands along; the driver then raises
``OperationCancelled`` at its next safe point.

The driver's consent hook (``ReadAsk``) is bridged *synchronously*: the
worker posts ``ask_requested`` and parks on an event until the UI answers
through :meth:`LoadWorker.answer_ask`.  Consent defaults to deny.  The UI
dialog is modal, so no cancel click or window close can race a pending ask.
An "all" answer latches a per-worker policy: later asks in the same open
skip the popup, and the next open (a fresh worker) asks again from scratch.

The outcome arrives as a signal: ``succeeded`` carries the
``PackageResult`` for the caller to adopt, ``failed`` carries the
exception -- cancellation is simply one exception type, so the caller can
report it quietly while real failures get a message box.
"""

from __future__ import annotations

import threading
from typing import Callable

from PySide6 import QtCore

from neoxtractor.core.packages import registry
from neoxtractor.core.packages.base import ReadOptions

__all__ = ["LoadWorker"]


class _ProgressBridge:
    """Adapter: the driver calls ``.emit()``; forward to a queued Qt signal.

    ``open_file`` wants a :class:`Progress` object, not a bare function.  The
    cancel callable the driver passes along is handed to the worker, so a
    Cancel click made before the first emit takes effect as soon as the
    driver offers the flag.
    """

    def __init__(self, worker: "LoadWorker") -> None:
        self._worker = worker

    def emit(
        self, stage: str, done: int, total: int, cancel: Callable[[], None]
    ) -> None:
        self._worker._capture_cancel(cancel)
        self._worker.progressed.emit(stage, done, total)


class _AskBridge:
    """Adapter: the driver's ReadAsk, bridged to the UI thread synchronously.

    Each call posts ``ask_requested`` (queued across threads) and parks on
    the event until the UI slot answers.  A latched policy short-circuits:
    no popup, no park -- the stored verdict is returned directly.
    """

    def __init__(self, worker: "LoadWorker") -> None:
        self._worker = worker

    def __call__(self, path: str, reason: str) -> bool:
        # A cancel already in flight means no UI will ever answer: deny
        # immediately and let the driver reach its next safe point.
        if self._worker._cancel_requested:
            return False
        policy = self._worker._ask_policy
        if policy is not None:
            return policy
        self._worker._ask_answer = False
        self._worker._ask_event.clear()
        self._worker.ask_requested.emit(path, reason)
        self._worker._ask_event.wait()
        return self._worker._ask_answer


class LoadWorker(QtCore.QObject):
    """Runs ``open_file`` off the UI thread and posts back via signals."""

    progressed = QtCore.Signal(str, int, int)  # stage, done, total
    ask_requested = QtCore.Signal(str, str)  # path, reason -> answer via answer_ask
    succeeded = QtCore.Signal(object)  # registry.PackageResult
    failed = QtCore.Signal(object)  # BaseException

    def __init__(
        self, path: str, options: ReadOptions | None, fmt: str | None
    ) -> None:
        super().__init__()
        self._path = path
        self._options = options
        self._format = fmt
        self._cancel: Callable[[], None] | None = None
        self._cancel_requested = False
        self._ask_event = threading.Event()
        self._ask_answer = False
        # Latched verdict for the rest of this open; None = ask each time.
        self._ask_policy: bool | None = None

    def _capture_cancel(self, cancel: Callable[[], None]) -> None:
        # Attribute writes are atomic under the GIL; the driver reuses one flag.
        if self._cancel is None:
            self._cancel = cancel
            if self._cancel_requested:
                cancel()

    def request_cancel(self) -> None:
        """Ask the driver to stop (a plain flag set, thread-safe)."""
        self._cancel_requested = True
        cancel = self._cancel
        if cancel is not None:
            cancel()
        # Release a worker parked on a consent question with a deny: the
        # answer can never come from a UI that is being torn down.
        self._ask_event.set()

    @QtCore.Slot(str)
    def answer_ask(self, answer: str) -> None:
        """Deliver the UI's consent answer to the parked worker thread.

        ``answer`` is one of the five dialog verdicts: ``accept`` / ``reject``
        resolve just this ask; ``accept_all`` / ``reject_all`` additionally
        latch a policy so later asks in this open skip the popup; ``abort``
        cancels the whole open (releasing the park with a deny).
        """
        if answer == "abort":
            self.request_cancel()
            return
        if answer == "accept_all":
            self._ask_policy = True
            approved = True
        elif answer == "reject_all":
            self._ask_policy = False
            approved = False
        elif answer == "accept":
            approved = True
        else:  # "reject"
            approved = False
        self._ask_answer = approved
        self._ask_event.set()

    @QtCore.Slot()
    def run(self) -> None:
        try:
            result = registry.open_file(
                self._path,
                format=self._format,
                options=self._options,
                progress=_ProgressBridge(self),
                ask=_AskBridge(self),
            )
        except BaseException as exc:  # forwarded to the UI, never swallowed here
            self.failed.emit(exc)
        else:
            self.succeeded.emit(result)
