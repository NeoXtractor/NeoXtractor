"""Background content probe for one open archive.

The scan walks every entry, opens its stream, and asks the processors facade
what it looks like, so the list can group and route nameless entries without
blocking browsing.  The core functions never touch Qt; the ``ProbeWorker``
shell buffers results for the UI thread, which drains them on a timer.
Entries open through the archive's eager decode: the scan reads only a
bounded head, but those bytes are decoded in full.  Windowed on-demand
decoding is a future upgrade within the same EntryStream contract.

Cancellation is the archive's own lifetime: once the archive is closed the
next ``get_res`` raises ``ValueError`` and the loop returns.  There is no
separate cancel control (the scan is bound to the archive session).
"""

from __future__ import annotations

import threading
from typing import Callable, NamedTuple

from PySide6 import QtCore

from neoxtractor.core.packages.base import Archive
from neoxtractor.core.packages.errors import PayloadDecodeError
from neoxtractor.core.processors import resolve
from neoxtractor.core.utils.content import head_is_binary
from neoxtractor.core.utils.filelike import Seekable
from neoxtractor.core.utils.logger import get_logger
from neoxtractor.core.utils.result import Category

__all__ = ["ProbeOutcome", "probe_stream", "iter_probe_results", "ProbeWorker"]

# Bytes the text/binary decision inspects.  Format sniffing is the facade's
# job (each probe reads its own bounded head), so this window serves only the
# is_binary classification and stays small to keep the scan cheap.
_TEXT_WINDOW = 8192


class ProbeOutcome(NamedTuple):
    """What the scan learned from one entry's header.

    ``unreadable`` marks an entry whose content could not be opened for
    probing -- a decoded read that raised (a withheld key, an unknown
    algorithm, a corrupt payload, a missing codec).  Such a row belongs with
    the encrypted/unreadable group in the list, not with ordinary content.
    """

    ext: str
    category: str
    is_text: bool
    unreadable: bool = False


def probe_stream(stream: Seekable) -> ProbeOutcome:
    """Classify one already-opened entry stream from its content.

    The stream is handed straight to the facade's :func:`resolve`, which
    probes every registered format against it (each probe reads only its own
    bounded head and rewinds, per the file-like contract) and returns the
    winning :class:`Resolution` or ``None``.  The text/binary answer comes
    from a separate bounded window of the same stream, independent of whether
    a format matched.
    """
    resolution = resolve(stream)
    if resolution is not None:
        ext = resolution.ext.lstrip(".").lower()
        category = resolution.category or Category.OTHER.value
    else:
        ext = ""
        category = Category.OTHER.value
    return ProbeOutcome(
        ext=ext,
        category=category,
        is_text=not head_is_binary(stream, _TEXT_WINDOW),
    )


def iter_probe_results(
    archive: Archive,
    count: int,
    on_result: Callable[[int, ProbeOutcome], None],
) -> None:
    """Probe entries ``0..count-1``, calling back per completed entry.

    Stops as soon as the archive is closed (``get_res`` raises
    ``ValueError``).  A single unreadable entry is logged and reported as an
    unknown-but-done result so it never stays in the waiting state; the rest
    of the scan continues.
    """
    log = get_logger()
    for key in range(count):
        outcome: ProbeOutcome
        try:
            stream = archive.get_res(key)
        except ValueError:
            # Archive closed: the session ended, so the scan is cancelled.
            return
        except PayloadDecodeError as exc:
            # Content exists but failed to decode: flag the row unreadable
            # so the list groups it with the encrypted entries.
            log.warning("probe could not decode entry %d: %s", key, exc)
            on_result(
                key,
                ProbeOutcome(
                    ext="", category=Category.OTHER.value, is_text=False,
                    unreadable=True,
                ),
            )
            continue
        except Exception as exc:  # an unavailable entry (missing) is not fatal
            # An availability fault (MissingIndexedResourceError, an EOFError
            # subclass), not a content fault: unreadable stays False.
            log.warning("probe could not open entry %d: %s", key, exc)
            on_result(
                key,
                ProbeOutcome(ext="", category=Category.OTHER.value, is_text=False),
            )
            continue
        try:
            with stream:
                outcome = probe_stream(stream)
        except Exception as exc:  # a bad entry must not abort the whole scan
            # Classification failed on readable content: unclassified, not
            # unreadable.
            log.warning("probe failed for entry %d: %s", key, exc)
            outcome = ProbeOutcome(ext="", category=Category.OTHER.value, is_text=False)
        on_result(key, outcome)


class ProbeWorker(QtCore.QObject):
    """Runs the scan off the UI thread and buffers results for pickup.

    Move an instance to a ``QThread`` and connect :attr:`scan_finished`.
    The worker never writes to the shared source model: results pile up in
    its own buffer (appended under a lock) and the UI thread drains them on
    its own timer through :meth:`take_batch`, keeping event-loop traffic
    bounded per poll instead of one signal per entry.
    """

    scan_finished = QtCore.Signal()

    def __init__(self, archive: Archive, count: int) -> None:
        super().__init__()
        self._archive = archive
        self._count = count
        self._lock = threading.Lock()
        self._pending: list[tuple[int, ProbeOutcome]] = []
        self._done = 0

    @QtCore.Slot()
    def run(self) -> None:
        def on_result(key: int, outcome: ProbeOutcome) -> None:
            with self._lock:
                self._pending.append((key, outcome))
                self._done += 1

        iter_probe_results(self._archive, self._count, on_result)
        self.scan_finished.emit()

    def take_batch(self) -> tuple[list[tuple[int, ProbeOutcome]], int]:
        """Swap out the result buffer; returns (results, done_count).

        Safe to call from the UI thread at any cadence: the swap is under
        the worker's lock, so a batch is a consistent snapshot and nothing
        is delivered twice.  ``done_count`` is the scan cursor the progress
        bar tracks -- reading it costs nothing beyond the lock.
        """
        with self._lock:
            batch = self._pending
            self._pending = []
            return batch, self._done
